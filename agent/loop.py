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
    CitedEvidence,
    Problem,
    Report,
    ReportChecked,
    ReportFinding,
    ReportNotChecked,
    RunInfo,
    StoppedBecause,
    Submission,
    Validation,
)
from agent.tools import PLANNED, RunContext, execute, tool_map
from agent.validator import parse_submission, valid_findings, validate
from chain.models import normalize_address

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


def _cited(findings, checked, registry: EvidenceRegistry) -> tuple[CitedEvidence, ...]:
    """Every id the report cites, resolved to a citation: tool results in order (T1, T2, ...), then
    knowledge records in order of first use."""
    ids: list[str] = []
    for source in (*(f.evidence_ids for f in findings), *(c.evidence_ids for c in checked)):
        ids += [i for i in source if i not in ids and i in registry]
    items = [registry.get(i) for i in ids]
    items.sort(key=lambda e: (e.source != "tool", int(e.id[1:]) if e.source == "tool" else 0))
    return tuple(
        CitedEvidence(
            id=item.id,
            kind="chain" if item.source == "tool" else "knowledge",
            citation=item.citation,
        )
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
    run: RunInfo,
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
        findings = valid_findings(submission, ctx.registry) if submission is not None else []
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
        tools_not_available=PLANNED,
        evidence=_cited(findings, checked, ctx.registry),
        validation=Validation(accepted=accepted, attempts=attempts, problems=tuple(problems)),
        run=run,
    )


def run_agent(
    model: ModelClient,
    ctx: RunContext,
    address: str,
    question: str = DEFAULT_QUESTION,
    *,
    limits: Limits | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AgentRun:
    limits = limits or Limits()
    address = normalize_address(address)
    block = ctx.client.pin()
    tools = tool_map()
    offered_all = [tool.schema() for tool in tools.values()] + [SUBMIT_TOOL]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(PLANNED)},
        {"role": "user", "content": user_message(address, ctx.chain, block, question)},
    ]
    started = clock()
    totals = Usage()
    turns: list[dict[str, Any]] = []
    tool_calls = attempts = reminders = 0
    stop: StoppedBecause = "turn_limit"
    wrapped = False
    accepted: Submission | None = None
    last: Submission | None = None
    problems: list[Problem] = []
    error: str | None = None

    def elapsed() -> float:
        return clock() - started

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
        try:
            reply = model.complete(
                messages,
                [SUBMIT_TOOL] if wrap_up else offered_all,
                max_tokens=limits.max_output_tokens,
            )
        except ModelUnavailable as err:
            stop, error = "model_unavailable", str(err)
            break
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
                continue
            tool_calls += 1
            results[call.id] = execute(ctx, call, tools)

        finished = exhausted = False
        for index, call in enumerate(submits):
            if index > 0 or others:
                results[call.id] = json.dumps({"accepted": False, "error": SUBMIT_ALONE})
                continue
            attempts += 1
            submission, problems = parse_submission(call.arguments)
            if submission is not None:
                last = submission
                problems = validate(submission, ctx.registry)
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

    info = RunInfo(
        model=model.name,
        turns=len(turns),
        tool_calls=tool_calls,
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        cached_tokens=totals.cached_tokens,
        cost_usd=round(totals.cost_usd, 6),
        seconds=round(elapsed(), 1),
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
        run=info,
    )
    return AgentRun(
        report=report, messages=messages, registry=ctx.registry, turns=turns, error=error
    )
