"""The loop: send the conversation and the tools, run every tool call the model asks for, send the
results back, and stop when the model submits a report that passes the citation validator or a cap
is reached.

No framework: it is this function. A worked example of one turn. The model replies with two tool
calls, `identify_contract` and `list_selectors`. The loop runs both, records each result in the
evidence registry (T1, T2), and appends one `tool` message per call, each carrying its evidence id.
On the next turn the model can cite T1 and T2. When it calls `submit_report`, the validator checks
every id and the verdict; if something is wrong the problems go back as the tool result and the
model tries again, up to `max_report_attempts` times.

Limits (models.toml): model calls, tool calls, time, and cost. Near a limit the model is offered
only `submit_report`; at a limit the run stops and the report says `insufficient_evidence` and
which limit it was. The model cannot be forced to call a tool (OpenRouter refuses `tool_choice` for
this model, see llm.py), so a reply with no tool call is answered with a reminder, twice at most.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent.config import Limits
from agent.evidence import EvidenceRegistry
from agent.llm import ModelClient, ModelUnavailable, Usage
from agent.prompts import (
    DEFAULT_QUESTION,
    NO_TOOL_CALL,
    SUBMIT_ALONE,
    WRAP_UP,
    system_prompt,
    user_message,
)
from agent.report import (
    SUBMIT_REPORT_SCHEMA,
    SUBMIT_REPORT_SCHEMA_WITH_REVIEW,
    CitedEvidence,
    Problem,
    Report,
    ReportChecked,
    ReportFinding,
    ReportNotChecked,
    RunInfo,
    StoppedBecause,
    Submission,
    Unavailable,
    Validation,
)
from agent.review_stage import coverage_block, review_records
from agent.runmeta import digest
from agent.tools import RunContext, execute, tool_map, unavailable_tools
from agent.validator import NO_REVIEW, ReviewRules, parse_submission, valid_findings, validate
from chain.models import normalize_address
from review.types import ReviewSummary

WRAP_UP_SHARE = 0.8  # of the cost cap or the time limit, after which only submit_report is offered
MAX_REMINDERS = 2

SUBMIT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_report",
        "description": "Finish: submit the report. Call it once, on its own, after you have "
        "seen every result you need. Every claim must cite evidence ids from this run.",
        "parameters": SUBMIT_REPORT_SCHEMA,
    },
}

# What a run with a source reviewer offers: the same tool, with `source_review` among the kinds.
SUBMIT_TOOL_WITH_REVIEW: dict[str, Any] = {
    "type": "function",
    "function": {**SUBMIT_TOOL["function"], "parameters": SUBMIT_REPORT_SCHEMA_WITH_REVIEW},
}

_STOP_TEXT: dict[str, str] = {
    "report_rejected": "the model's report did not pass the citation validator",
    "no_report": "the model did not submit a report",
    "turn_limit": "the limit on model calls was reached",
    "time_limit": "the time limit was reached",
    "cost_cap": "the cost cap was reached",
    "model_unavailable": "the model could not be reached",
}


@dataclass
class AgentRun:
    report: Report
    messages: list[dict[str, Any]]
    registry: EvidenceRegistry
    turns: list[dict[str, Any]] = field(default_factory=list)  # per model call: tools, tokens, cost
    error: str | None = None  # why the model could not be reached; never contains the key
    # For Component 6, written to the trace and never read by the loop (see `_call_record`,
    # `_attempt_record` and the `metrics` built at the end of `run_agent`):
    calls: list[dict[str, Any]] = field(
        default_factory=list
    )  # per tool call: seconds, fingerprints
    attempts: list[dict[str, Any]] = field(default_factory=list)  # per submit_report attempt
    metrics: dict[str, Any] = field(default_factory=dict)  # where the time went, store hits
    review: dict[str, Any] | None = None  # the source review, for the trace (None: no reviewer)


def _call_record(
    turn: int, call: Any, result: str, seconds: float, wall_seconds: float
) -> dict[str, Any]:
    """What the trace keeps about one tool call: how long it took and a fingerprint of what went
    in and what came back. The result's fingerprint leaves out the evidence id, so the same call
    giving the same answer in two runs gets the same fingerprint however the calls were numbered."""
    try:
        shown = json.loads(result)
    except ValueError:
        shown = None
    shown = shown if isinstance(shown, dict) else {}
    evidence_id = shown.pop("evidence_id", None)
    return {
        "turn": turn,
        "tool": call.name,
        "call_id": call.id,
        "ran": True,
        "seconds": round(seconds, 3),
        "wall_seconds": round(wall_seconds, 3),
        "arguments_sha": digest(call.arguments),
        "result_sha": digest(json.dumps(shown, sort_keys=True) if shown else result),
        "evidence_id": evidence_id,
        "ok": shown.get("ok") if "ok" in shown else None,
        "duplicate": bool(shown.get("duplicate")),
        "error": "error" in shown,
    }


def _attempt_record(
    attempt: int,
    turn: int,
    submission: Submission | None,
    problems: list[Problem],
    registry: EvidenceRegistry,
    rules: ReviewRules = NO_REVIEW,
) -> dict[str, Any]:
    """One try at `submit_report`: whether the arguments parsed, how many claims, how many of them
    failed on their own, and every problem by place and code."""
    findings = submission.findings[:25] if submission is not None else None
    failing = (
        len(findings) - len(valid_findings(submission, registry, rules))
        if submission is not None and findings is not None
        else None
    )
    return {
        "attempt": attempt,
        "turn": turn,
        "parsed": submission is not None,
        "accepted": not problems,
        "claims": None if findings is None else len(findings),
        "claims_failing": failing,
        "problems": [{"where": p.where, "code": p.code} for p in problems],
    }


_CITED_KIND = {"tool": "chain", "source": "source", "knowledge": "knowledge"}
_CITED_ORDER = {"tool": 0, "source": 1, "knowledge": 2}


def _cited(findings, checked, registry: EvidenceRegistry) -> tuple[CitedEvidence, ...]:
    """Every id the report cites, resolved to a citation: tool results in order (T1, T2, ...), then
    source records (S1, ...), then knowledge records in order of first use."""
    ids: list[str] = []
    for source in (*(f.evidence_ids for f in findings), *(c.evidence_ids for c in checked)):
        ids += [i for i in source if i not in ids and i in registry]
    items = [registry.get(i) for i in ids]
    items.sort(
        key=lambda e: (_CITED_ORDER[e.source], int(e.id[1:]) if e.source != "knowledge" else 0)
    )
    return tuple(
        CitedEvidence(id=item.id, kind=_CITED_KIND[item.source], citation=item.citation)
        for item in items
        if item is not None
    )


def _report(
    *,
    ctx: RunContext,
    address: str,
    block: int | None,
    submission: Submission | None,
    accepted: bool,
    attempts: int,
    problems: list[Problem],
    stop: StoppedBecause,
    unavailable: tuple[Unavailable, ...],
    run: RunInfo,
    source_review: ReviewSummary | None = None,
    rules: ReviewRules = NO_REVIEW,
) -> Report:
    if accepted and submission is not None:
        verdict, summary = submission.verdict, submission.summary
        findings, checked = submission.findings, submission.checked
        not_checked = [ReportNotChecked(what=n.what, why=n.why) for n in submission.not_checked]
    else:
        # Only what passed the validator on its own is kept; the model's own summary is dropped,
        # since nothing checked it.
        verdict = "insufficient_evidence"
        summary = (
            f"No validated report: {_STOP_TEXT[stop]}. The findings listed, if any, are the ones "
            "that passed the citation validator on their own; nothing else can be relied on."
        )
        findings = valid_findings(submission, ctx.registry, rules) if submission is not None else []
        checked = []
        not_checked = [
            ReportNotChecked(what="The analysis as a whole", why=_STOP_TEXT[stop].capitalize())
        ]
    return Report(
        chain=ctx.chain,
        address=address,
        block=block,
        verdict=verdict,
        summary=summary,
        findings=tuple(
            ReportFinding(
                claim=f.claim, kind=f.kind, severity=f.severity, evidence_ids=tuple(f.evidence_ids)
            )
            for f in findings
        ),
        checked=tuple(
            ReportChecked(what=c.what, evidence_ids=tuple(c.evidence_ids)) for c in checked
        ),
        not_checked=tuple(not_checked),
        tools_not_available=unavailable,
        evidence=_cited(findings, checked, ctx.registry),
        validation=Validation(accepted=accepted, attempts=attempts, problems=tuple(problems)),
        run=run,
        source_review=source_review,
    )


def run_agent(
    model: ModelClient,
    ctx: RunContext,
    address: str,
    question: str = DEFAULT_QUESTION,
    *,
    limits: Limits | None = None,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> AgentRun:
    limits = limits or Limits()
    # Two clocks, for the trace only: `clock` (monotonic) drives the limits and the timings, `wall`
    # is the time of day. On a laptop the first stops while the machine sleeps and the second does
    # not, so a gap between them marks a run that slept.
    began, began_wall = clock(), wall()
    address = normalize_address(address)
    ctx.target = address
    block = ctx.client.pin()
    tools = tool_map(ctx)
    unavailable = unavailable_tools(ctx)
    submit_tool = SUBMIT_TOOL if ctx.review is None else SUBMIT_TOOL_WITH_REVIEW
    offered_all = [tool.schema() for tool in tools.values()] + [submit_tool]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(unavailable, review=ctx.review is not None)},
        {"role": "user", "content": user_message(address, ctx.chain, block, question)},
    ]
    started = clock()
    setup_seconds = started - began
    totals = Usage()
    turns: list[dict[str, Any]] = []
    call_log: list[dict[str, Any]] = []
    attempt_log: list[dict[str, Any]] = []
    model_seconds = tool_seconds = review_seconds = 0.0
    tool_calls = attempts = reminders = 0
    stop: StoppedBecause = "turn_limit"
    wrapped = False
    accepted: Submission | None = None
    last: Submission | None = None
    problems: list[Problem] = []
    error: str | None = None

    def elapsed() -> float:
        # The review has its own time limit ([reviewer] in models.toml); it is not the writer's.
        return clock() - started - review_seconds

    while True:
        if len(turns) >= limits.max_turns:
            stop = "turn_limit"
            break
        if elapsed() >= limits.wall_clock_seconds:
            stop = "time_limit"
            break
        if totals.cost_usd >= limits.max_cost_usd:
            stop = "cost_cap"
            break
        wrap_up = (
            tool_calls >= limits.max_tool_calls
            or len(turns) >= limits.max_turns - 2
            or totals.cost_usd >= WRAP_UP_SHARE * limits.max_cost_usd
            or elapsed() >= WRAP_UP_SHARE * limits.wall_clock_seconds
        )
        if wrap_up and not wrapped:
            messages.append({"role": "user", "content": WRAP_UP})
            wrapped = True
        asked, asked_wall = clock(), wall()
        try:
            reply = model.complete(
                messages,
                [submit_tool] if wrap_up else offered_all,
                max_tokens=limits.max_output_tokens,
            )
        except ModelUnavailable as err:
            model_seconds += clock() - asked
            stop, error = "model_unavailable", str(err)
            break
        model_took, model_wall = clock() - asked, wall() - asked_wall
        model_seconds += model_took
        usage = reply.usage
        totals = Usage(
            prompt_tokens=totals.prompt_tokens + usage.prompt_tokens,
            completion_tokens=totals.completion_tokens + usage.completion_tokens,
            cached_tokens=totals.cached_tokens + usage.cached_tokens,
            cost_usd=totals.cost_usd + usage.cost_usd,
        )
        turns.append(
            {
                "turn": len(turns) + 1,
                "tools": [c.name for c in reply.tool_calls],
                "finish_reason": reply.finish_reason,
                **usage.model_dump(),
                "seconds": round(model_took, 3),
                "wall_seconds": round(model_wall, 3),
                "retries": reply.retries,
                "provider": reply.provider,
                "model_served": reply.model,
            }
        )
        messages.append(reply.as_message())

        if not reply.tool_calls:
            reminders += 1
            if reminders > MAX_REMINDERS:
                stop = "no_report"
                break
            messages.append({"role": "user", "content": NO_TOOL_CALL})
            continue

        submits = [c for c in reply.tool_calls if c.name == "submit_report"]
        others = [c for c in reply.tool_calls if c.name != "submit_report"]
        results: dict[str, str] = {}
        for call in others:
            if (
                wrap_up
                or tool_calls >= limits.max_tool_calls
                or elapsed() >= limits.wall_clock_seconds
            ):
                results[call.id] = json.dumps(
                    {"error": "No more tool calls are allowed in this run. Call submit_report."}
                )
                call_log.append(
                    {"turn": len(turns), "tool": call.name, "call_id": call.id, "ran": False}
                )
                continue
            tool_calls += 1
            if ctx.review is not None:
                ctx.dollars_left = limits.max_cost_usd - totals.cost_usd
            ran, ran_wall = clock(), wall()
            results[call.id] = execute(ctx, call, tools)
            tool_took = clock() - ran
            tool_seconds += tool_took
            call_log.append(
                _call_record(len(turns), call, results[call.id], tool_took, wall() - ran_wall)
            )
            # Dollars a tool spent on a model of its own count in the run's cost and its cap.
            spent, ctx.tool_cost_usd = ctx.tool_cost_usd, 0.0
            if spent:
                totals = totals.model_copy(update={"cost_usd": totals.cost_usd + spent})
                call_log[-1]["cost_usd"] = round(spent, 6)
            if call.name == "review_source":
                review_seconds += tool_took

        finished = exhausted = False
        for index, call in enumerate(submits):
            if index > 0 or others:
                results[call.id] = json.dumps({"accepted": False, "error": SUBMIT_ALONE})
                continue
            attempts += 1
            submission, problems = parse_submission(call.arguments)
            rules = (
                ctx.review.rules(can_still_run=not wrap_up and tool_calls < limits.max_tool_calls)
                if ctx.review is not None
                else NO_REVIEW
            )
            if submission is not None:
                last = submission
                problems = validate(submission, ctx.registry, rules)
            attempt_log.append(
                _attempt_record(attempts, len(turns), submission, problems, ctx.registry, rules)
            )
            if not problems:
                accepted, finished = submission, True
                results[call.id] = json.dumps({"accepted": True})
                continue
            exhausted = attempts >= limits.max_report_attempts
            results[call.id] = json.dumps(
                {
                    "accepted": False,
                    "attempt": attempts,
                    "attempts_allowed": limits.max_report_attempts,
                    "problems": [p.model_dump() for p in problems],
                    "next": "Fix every problem and call submit_report again, on its own. Gather "
                    "more evidence first if a claim has none.",
                }
            )
        for call in reply.tool_calls:
            messages.append({"role": "tool", "tool_call_id": call.id, "content": results[call.id]})
        if finished:
            stop = "report_accepted"
            break
        if exhausted:
            stop = "report_rejected"
            break

    loop_seconds = elapsed() + review_seconds
    wall_seconds = wall() - began_wall
    metrics = {
        "schema": 2,
        "started_at": datetime.fromtimestamp(began_wall, UTC).isoformat(timespec="seconds"),
        "setup_s": round(setup_seconds, 3),
        "loop_s": round(loop_seconds, 3),
        "total_s": round(setup_seconds + loop_seconds, 3),
        "model_s": round(model_seconds, 3),
        "tools_s": round(tool_seconds, 3),
        # what the loop did itself (validating reports, building messages); should be near zero
        "overhead_s": round(max(0.0, loop_seconds - model_seconds - tool_seconds), 3),
        "wall_s": round(wall_seconds, 3),
        # time of day minus the monotonic clock: well above zero means the machine slept
        "skew_s": round(wall_seconds - (setup_seconds + loop_seconds), 3),
        "model_calls": len(turns),
        "tools_run": tool_calls,
        "system_prompt_sha": digest(messages[0]["content"]),
        "tools_sha": digest(json.dumps(offered_all, sort_keys=True, default=str)),
        "counters": dict(ctx.counters),
    }
    coverage = coverage_block(ctx)
    review_trace = None
    if coverage is not None:
        review_trace, metrics["review"] = review_records(ctx, coverage, review_seconds)
    info = RunInfo(
        model=model.name,
        turns=len(turns),
        tool_calls=tool_calls,
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        cached_tokens=totals.cached_tokens,
        cost_usd=round(totals.cost_usd, 6),
        seconds=round(elapsed() + review_seconds, 1),
        stopped_because=stop,
    )
    report = _report(
        ctx=ctx,
        address=address,
        block=block,
        submission=accepted or last,
        accepted=accepted is not None,
        attempts=attempts,
        problems=[] if accepted is not None else problems,
        stop=stop,
        unavailable=unavailable,
        run=info,
        source_review=coverage,
        rules=ctx.review.rules(can_still_run=False) if ctx.review is not None else NO_REVIEW,
    )
    return AgentRun(
        report=report,
        messages=messages,
        registry=ctx.registry,
        turns=turns,
        error=error,
        calls=call_log,
        attempts=attempt_log,
        metrics=metrics,
        review=review_trace,
    )
