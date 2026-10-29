"""Source review inside the agent: what the `review_source` tool does (R4).

The report writer never reads source. This module is the one place where the agent meets the
`review/` package: it finds the logic contract of a proxy itself, fetches the bounded source
bundle, runs the review (`review.reviewer.run_review`, a separate model with no tools), and turns
the result into

* the tool's own result for the writer: status, coverage counts and, for each flagged place, only
  the closed fields of `review.spans.writer_fields` (ids, file id, lines, the function the parser
  found, our own entry title and clause text, severity, confidence). Never a path, a quote, the
  reviewer's note or any file name;
* `S` evidence records (the validator checks claims against them), each with the full span kept
  aside for the screen;
* the coverage block of the report, written by the runner whatever the model said.

Everything here is off unless a reviewer is wired into the run (`RunContext.review`).
"""

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from agent.config import AgentConfig
from agent.llm import ModelUnavailable
from agent.validator import ReviewRules, review_facts
from chain.code import code_hash_of
from chain.db import Database, PostgresBundleStore
from chain.errors import ToolError
from chain.models import Provenance
from chain.proxy import resolve_proxy
from review.bundle import fetch_source_bundle
from review.client import UNAVAILABLE, OpenRouterReviewer, ReviewerClient, company_of
from review.lexer import LEXER_VERSION
from review.pack import Checklist, build_checklist, prompt_sha
from review.reviewer import ReviewResult, run_review
from review.spans import writer_fields
from review.types import SEVERITY_RANK, ReviewSummary, SourceSpan, to_json

if TYPE_CHECKING:
    from agent.tools import RunContext

MAX_SPANS_SHOWN = 25  # flagged places the writer is shown; the rest stay in the trace and viewer
MAX_SCANS_SHOWN = 10
TIME_LIMIT = "the review time limit was reached"
EXPLORER_DOWN = "the explorer was not available or its answer could not be read"
NOT_THIS_CONTRACT = "review_source reads the contract under analysis, or its implementation, only"
ONE_REVIEW = "one source review is run per analysis"


@dataclass(frozen=True)
class ReviewSettings:
    """A reviewer wired into one run. `second` is the blind confirmation reader and is used only
    when `may_set_high_risk` is on (decision 11); both are `ScriptedReviewer`s in tests."""

    client: ReviewerClient
    second: ReviewerClient | None = None
    may_set_high_risk: bool = False
    ceiling_tokens: int = 300_000
    chunk_tokens: int = 12_000
    max_cost_usd: float = 0.50
    max_output_tokens: int = 8192
    wall_clock_seconds: float = 600.0
    clock: Callable[[], float] = time.monotonic

    def rules(self, can_still_run: bool) -> ReviewRules:
        return ReviewRules(
            offered=True, may_set_high=self.may_set_high_risk, can_still_run=can_still_run
        )


def build_settings(config: AgentConfig, *, model: str | None = None) -> ReviewSettings:
    """The reviewer from models.toml (or a tier or slug given on the command line). Raises
    ValueError for a switch that cannot work: high-risk on with a confirmation model from the
    reviewer's own company, since a second reading from the same company is not independent."""
    limits = config.reviewer
    spec = config.resolve_model(model or config.reviewer_model)
    second = None
    if config.review_may_set_high_risk:
        other = config.resolve_model(config.confirmation_model)
        if company_of(other.name) == company_of(spec.name):
            raise ValueError(
                f"review_may_set_high_risk is on, but the confirmation model {other.name} and the "
                f"reviewer {spec.name} are from the same company: pick another confirmation model."
            )
        second = OpenRouterReviewer(other, timeout=limits.request_timeout_seconds)
    return ReviewSettings(
        client=OpenRouterReviewer(spec, timeout=limits.request_timeout_seconds),
        second=second,
        may_set_high_risk=config.review_may_set_high_risk,
        ceiling_tokens=limits.ceiling_tokens,
        chunk_tokens=limits.chunk_tokens,
        max_cost_usd=limits.max_cost_usd,
        max_output_tokens=limits.max_output_tokens,
        wall_clock_seconds=limits.wall_clock_seconds,
    )


def bundle_store_from_env(env: Mapping[str, str]) -> PostgresBundleStore | None:
    """The source-bundle cache, if DATABASE_URL is set (a dead database is a miss, not an error)."""
    db = Database.from_env(env)
    return PostgresBundleStore(db) if db else None


class TimeBoxed:
    """A reviewer client that stops answering once a shared deadline has passed. `run_review` takes
    that as the model being unavailable and ends the review partial."""

    def __init__(self, inner: ReviewerClient, deadline: float, clock: Callable[[], float]) -> None:
        self.name = inner.name
        self._inner, self._deadline, self._clock = inner, deadline, clock
        self.expired = False

    def review(self, prompt, schema, *, max_tokens):
        if self._clock() >= self._deadline:
            self.expired = True
            raise ModelUnavailable(TIME_LIMIT)
        return self._inner.review(prompt, schema, max_tokens=max_tokens)


@dataclass(frozen=True)
class SourceEntry:
    """One `S` record to register: what the writer was shown, and the full span aside."""

    id: str
    citation: str
    shown: dict[str, Any]
    record: dict[str, Any]


@dataclass
class ReviewOutcome:
    data: dict[str, Any]  # the tool's result as the writer sees it
    ok: bool
    provenance: Provenance | None
    logic: str
    summary: ReviewSummary
    entries: tuple[SourceEntry, ...] = ()
    cost_usd: float = 0.0
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Resolution:
    logic: str  # the address whose source is reviewed
    identified: bool  # False: a wrapper or a failed read, the logic contract is not known
    kind: str
    found_by: str | None = None
    second_hop: str | None = None


def resolve_logic(ctx: "RunContext", subject: str) -> Resolution:
    """Which address holds the logic: the same deterministic read as `resolve_proxy`, one hop. The
    model's word is never used for it, and neither is the explorer's `Implementation` hint."""
    found = resolve_proxy(ctx.client, subject, ctx.code_store).data
    if isinstance(found, ToolError):
        return Resolution(subject, False, "undetermined")
    if found.implementation is not None:
        logic = found.implementation.address
        again = resolve_proxy(ctx.client, logic, ctx.code_store).data
        hop = None
        if not isinstance(again, ToolError) and again.kind != "not-a-proxy":
            hop = (
                f"the implementation is itself a proxy (kind {again.kind}); where it points was "
                "not followed"
            )
        return Resolution(logic, True, found.kind, found.implementation.found_by, hop)
    return Resolution(subject, found.kind == "not-a-proxy" and found.status == "ok", found.kind)


def _explorer_disagrees(ctx: "RunContext", subject: str, resolution: Resolution) -> bool:
    """The explorer named another implementation for the address than the chain did."""
    if not resolution.identified or resolution.logic.lower() == subject.lower():
        return False
    for item in ctx.registry.tool_results("get_verified_source"):
        asked = str((item.arguments or {}).get("address", "")).lower()
        named = item.data.get("explorer_implementation") if isinstance(item.data, dict) else None
        if asked == subject.lower() and named and str(named).lower() != resolution.logic.lower():
            return True
    return False


def source_citation(span: SourceSpan) -> str:
    """One line a reader can check; the path is display text, cleaned when the bundle was made."""
    where = (
        f"source review of {span.subject.address} on {span.subject.chain}, "
        f"block {span.subject.block}"
    )
    lines = f"{span.range.start_line}-{span.range.end_line}"
    file = f"file {span.file.path} lines {lines} (file sha256 {span.file.sha256[:12]})"
    if span.kind == "scan" and span.scan is not None:
        return f"{where}, scan flag {span.scan.flag} x{span.scan.count}, {file}"
    model = span.reviewer.model if span.reviewer else "unknown"
    return f"{where}, {file}, read by {model}"


def _skipped(
    settings: ReviewSettings, status: str, reason: str, *, unsupported: str | None = None
) -> ReviewSummary:
    return ReviewSummary(
        status=status,  # type: ignore[arg-type]
        reason=reason,
        reviewer_model=settings.client.name,
        unsupported=unsupported,
    )


def _coverage(summary: ReviewSummary) -> dict[str, Any]:
    """Counts only. Dropped files are reduced to a count and their fixed reasons, because a path is
    the author's text."""
    return {
        "files_total": summary.files_total,
        "files_reviewed": summary.files_reviewed,
        "definitions_known": summary.definitions_known,
        "definitions_new": summary.definitions_new,
        "definitions_reviewed": summary.definitions_reviewed,
        "definitions_not_reviewed": summary.definitions_not_reviewed,
        "files_not_checked": summary.files_not_checked,
        "assembly_blocks": summary.assembly_blocks,
        "new_tokens": summary.new_tokens,
        "ceiling_tokens": summary.ceiling_tokens,
        "files_not_fetched": sum(d.count for d in summary.dropped),
        "not_fetched_reasons": sorted({d.reason for d in summary.dropped}),
        "scan_flags": [{"flag": f.flag, "count": f.count} for f in summary.scan_flags],
        "known_libraries": [
            {"library": k.library, "version": k.version, "definitions": k.definitions}
            for k in summary.known_libraries
        ],
    }


def _id_number(span: SourceSpan) -> int:
    return int(span.id[1:])


def _shown_spans(result: ReviewResult) -> tuple[list[SourceSpan], list[SourceSpan]]:
    """(scans, spans) the writer is shown: the most severe, most confident places first."""
    order = sorted(
        result.spans,
        key=lambda s: (
            -SEVERITY_RANK[s.match.severity if s.match else "low"],
            -SEVERITY_RANK[s.match.confidence if s.match else "low"],
            _id_number(s),
        ),
    )
    return result.scans[:MAX_SCANS_SHOWN], sorted(order[:MAX_SPANS_SHOWN], key=_id_number)


def _outcome(
    ctx: "RunContext",
    given: str,
    resolution: Resolution,
    summary: ReviewSummary,
    *,
    ok: bool,
    result: ReviewResult | None = None,
    checklist: Checklist | None = None,
    bundle_info: dict[str, Any] | None = None,
) -> ReviewOutcome:
    settings = ctx.review
    assert settings is not None
    entries: list[SourceEntry] = []
    omitted = scans_omitted = 0
    if result is not None and checklist is not None:
        scans, spans = _shown_spans(result)
        omitted = len(result.spans) - len(spans)
        scans_omitted = len(result.scans) - len(scans)
        entries = [
            SourceEntry(
                id=s.id,
                citation=source_citation(s),
                shown=writer_fields(s, checklist),
                record=to_json(s),
            )
            for s in sorted([*scans, *spans], key=_id_number)
        ]
    data = {
        "status": summary.status,
        "reason": summary.reason,
        "given_address": given,
        "reviewed_address": resolution.logic,
        "logic_identified": resolution.identified,
        "proxy_kind": resolution.kind,
        "resolved_by": resolution.found_by,
        "second_hop": resolution.second_hop,
        "explorer_implementation_differs": _explorer_disagrees(ctx, given, resolution),
        "reader": settings.client.name,
        "coverage": _coverage(summary),
        "spans": [e.shown for e in entries],
        "spans_omitted": omitted,
        "scans_omitted": scans_omitted,
    }
    trace: dict[str, Any] = {
        "summary": to_json(summary),
        "given_address": given,
        "reviewed_address": resolution.logic,
        "bundle": bundle_info,
        "scans": [to_json(s) for s in result.scans] if result else [],
        "spans": [to_json(s) for s in result.spans] if result else [],
        "confirmations": [to_json(s) for s in result.confirmations] if result else [],
        "calls": [asdict(c) for c in result.calls] if result else [],
        "dropped": dict(result.dropped) if result else {},
        "adjusted": dict(result.adjusted) if result else {},
        "fingerprints": {
            "reviewer_prompt_sha": prompt_sha(),
            "checklist_sha": checklist.sha256 if checklist else None,
            "lexer_version": LEXER_VERSION,
            "reviewer_model": settings.client.name,
            "confirmation_model": summary.confirmation_model,
        },
    }
    return ReviewOutcome(
        data=data,
        ok=ok,
        provenance=ctx.client.provenance(resolution.logic, source="review_source"),
        logic=resolution.logic,
        summary=summary,
        entries=tuple(entries),
        cost_usd=summary.cost_usd,
        trace=trace,
    )


def run_source_review(ctx: "RunContext", given: str, hints: Sequence[str]) -> ReviewOutcome:
    """The tool. `given` is the address the model passed; the review reads the logic contract that
    the chain says is behind the run's own contract."""
    settings = ctx.review
    assert settings is not None, "review_source is offered only when a reviewer is wired in"
    subject = ctx.target or given
    resolution = resolve_logic(ctx, subject)
    logic = resolution.logic
    if ctx.target is not None and given.lower() not in {ctx.target.lower(), logic.lower()}:
        refused = _skipped(settings, "skipped", NOT_THIS_CONTRACT)
        out = _outcome(ctx, given, resolution, refused, ok=False)
        out.data = {"error": "not_this_contract", "reason": NOT_THIS_CONTRACT}
        out.entries = ()
        return out
    stored = ctx._reviews.get(logic.lower())
    if stored is not None and stored.ok:
        first = [e.id for e in ctx.registry.tool_results("review_source")]
        return ReviewOutcome(
            data={
                "already_reviewed": logic,
                "evidence_id": first[0] if first else None,
                "note": "This contract was reviewed in this run; that result still stands.",
            },
            ok=True,
            provenance=stored.provenance,
            logic=logic,
            summary=stored.summary,
        )
    if any(o.entries for k, o in ctx._reviews.items() if k != logic.lower()):
        refused = _skipped(settings, "skipped", ONE_REVIEW)
        out = _outcome(ctx, given, resolution, refused, ok=False)
        out.data = {"error": "one_review_per_run", "reason": ONE_REVIEW}
        return out

    outcome = _review_logic(ctx, settings, given, resolution, hints)
    ctx._reviews[logic.lower()] = outcome
    return outcome


def _review_logic(
    ctx: "RunContext",
    settings: ReviewSettings,
    given: str,
    resolution: Resolution,
    hints: Sequence[str],
) -> ReviewOutcome:
    logic = resolution.logic
    code = ctx.code(logic)
    if not code:
        reason = "there is no code at the address to review"
        return _outcome(ctx, given, resolution, _skipped(settings, "skipped", reason), ok=True)
    block = ctx.client.pin()
    fetched = fetch_source_bundle(
        ctx.services.explorer,
        ctx.bundle_store,
        chain=ctx.chain,
        chain_id=ctx.client.chain_id,
        address=logic,
        block=block,
        code_hash=code_hash_of(code),
    )
    info = {"status": fetched.status, "from_cache": fetched.from_cache}
    if fetched.bundle is None:
        status, reason, ok, unsupported = {
            "not_verified": ("skipped", "source is not verified on the explorer", True, None),
            "unsupported_language": (
                "skipped",
                "the source language is not supported; only Solidity is reviewed",
                True,
                "language: " + (_language(fetched.reason) or "not Solidity"),
            ),
            "too_large": ("refused", fetched.reason or "the source is too large", True, None),
        }.get(fetched.status, ("failed", EXPLORER_DOWN, False, None))
        summary = _skipped(settings, status, reason, unsupported=unsupported)
        return _outcome(ctx, given, resolution, summary, ok=ok, bundle_info=info)
    info["bundle_sha256"] = fetched.bundle.bundle_sha256
    info["files"] = len(fetched.bundle.files)

    limit = settings.max_cost_usd
    if ctx.dollars_left is not None:
        limit = min(limit, max(ctx.dollars_left, 0.0))
    if limit <= 0:
        reason = "the run's cost cap leaves nothing for a review"
        summary = _skipped(settings, "skipped", reason)
        return _outcome(ctx, given, resolution, summary, ok=True, bundle_info=info)

    checklist = build_checklist()
    deadline = settings.clock() + settings.wall_clock_seconds
    first = TimeBoxed(settings.client, deadline, settings.clock)
    second = (
        TimeBoxed(settings.second, deadline, settings.clock)
        if settings.may_set_high_risk and settings.second is not None
        else None
    )
    result = run_review(
        fetched.bundle,
        first,
        checklist=checklist,
        hints=hints,
        ceiling_tokens=settings.ceiling_tokens,
        chunk_tokens=settings.chunk_tokens,
        max_cost_usd=limit,
        max_tokens=settings.max_output_tokens,
        second=second,
    )
    summary = result.summary
    if (first.expired or (second and second.expired)) and summary.reason:
        summary = summary.model_copy(
            update={"reason": summary.reason.replace(UNAVAILABLE, TIME_LIMIT)}
        )
        result.summary = summary
    return _outcome(
        ctx,
        given,
        resolution,
        summary,
        ok=summary.status != "failed",
        result=result,
        checklist=checklist,
        bundle_info=info,
    )


def _language(reason: str | None) -> str | None:
    found = re.search(r"\(([A-Za-z ]{1,20})\)", reason or "")
    return found.group(1).lower() if found else None


def _best(ctx: "RunContext") -> ReviewOutcome | None:
    """The attempt that counts: one that produced records, else one that answered, else the last."""
    attempts = list(ctx._reviews.values())
    if not attempts:
        return None
    for wanted in (lambda o: o.ok and o.entries, lambda o: o.ok):
        found = next((o for o in reversed(attempts) if wanted(o)), None)
        if found is not None:
            return found
    return attempts[-1]


def coverage_block(ctx: "RunContext") -> ReviewSummary | None:
    """`Report.source_review`: the review's own coverage if it ran, otherwise why it did not. Never
    written by the model. None when no reviewer was wired into the run."""
    settings = ctx.review
    if settings is None:
        return None
    best = _best(ctx)
    if best is not None:
        return best.summary
    if ctx.services.explorer is None:
        reason = "no block explorer is configured (no ETHERSCAN_API_KEY), so no source was fetched"
        return _skipped(settings, "skipped", reason)
    facts = review_facts(ctx.registry)
    identify = next(iter(ctx.registry.tool_results("identify_contract")), None)
    summary = identify.data.get("summary") if identify and isinstance(identify.data, dict) else None
    if identify is None:
        reason = "identify_contract was not run, so it was not known whether a review applies"
    elif summary == "official":
        reason = "the protocol's own record lists this contract: not reviewed by default"
    elif summary == "no_code":
        reason = "there is no code at this address"
    elif not facts.applies:
        reason = "the code_review check does not apply to this contract"
    elif facts.unverified:
        reason = "source is not verified on the explorer"
    elif not facts.verified:
        reason = "the source of the logic contract was not looked up in this run"
    else:
        reason = "review_source was not run before the run ended"
    return _skipped(settings, "skipped", reason)


def review_records(
    ctx: "RunContext", block: ReviewSummary, seconds: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the trace's `review` key, `metrics["review"]`): the full records for the screen, and the
    numbers for the dashboards. Nothing here is shown to a model."""
    settings = ctx.review
    assert settings is not None
    best = _best(ctx)
    trace = best.trace if best is not None else {}
    trace = {
        "summary": to_json(block),
        "fingerprints": {
            "reviewer_prompt_sha": prompt_sha(),
            "lexer_version": LEXER_VERSION,
            "reviewer_model": settings.client.name,
        },
        **trace,
    }
    calls = trace.get("calls", [])
    bundle = trace.get("bundle") or {}
    metrics = {
        "status": block.status,
        "seconds": round(seconds, 3),
        "calls": len(calls),
        "prompt_tokens": sum(c.get("prompt_tokens", 0) for c in calls),
        "completion_tokens": sum(c.get("completion_tokens", 0) for c in calls),
        "cost_usd": round(block.cost_usd, 6),
        "definitions_new": block.definitions_new,
        "definitions_reviewed": block.definitions_reviewed,
        "new_tokens": block.new_tokens,
        "ceiling_tokens": block.ceiling_tokens,
        "bundle_from_cache": bool(bundle.get("from_cache")),
    }
    return trace, metrics
