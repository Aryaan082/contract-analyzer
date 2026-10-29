"""The report: what the model submits, and what the runner prints.

`submit_report` is a tool whose input schema is the report, so the model ends the run by filling in
a form rather than by writing free text, and the runner can check the form. What comes back out is
`Report`: the model's findings that passed the citation validator, each ID resolved to a citation a
reader can check, and the parts the runner writes itself (the checks that do not exist yet, the
cost, the validation outcome).

Verdicts. There is deliberately no "safe": the tools cannot prove safety, so the best answer is
`no_red_flags_found`, which always comes with `not_checked`.
"""

import copy
from typing import Any, Literal

from pydantic import BaseModel, Field

from review.types import ReviewSummary

Verdict = Literal["high_risk", "elevated_risk", "no_red_flags_found", "insufficient_evidence"]
Severity = Literal["info", "low", "medium", "high"]
# observation: a fact about THIS contract, from tool output. context: what a known pattern is or
# why it matters, from the knowledge base. A retrieved audit finding shows that a pattern exists
# somewhere, never that this contract has it, so it can back a context claim but not an observation.
# source_review: a model's reading of this contract's source (review_source), cites S ids. The
# renderer, not the model, labels it as an unchecked reading.
Kind = Literal["observation", "context", "source_review"]

VERDICTS: tuple[str, ...] = (
    "high_risk",
    "elevated_risk",
    "no_red_flags_found",
    "insufficient_evidence",
)
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}

MAX_CLAIM_CHARS = 600
MAX_SUMMARY_CHARS = 1500


class FindingIn(BaseModel):
    """One claim as the model wrote it. Lenient on purpose (an empty evidence list parses), so the
    validator can say what is wrong with each claim instead of refusing the whole report."""

    claim: str = ""
    kind: Kind = "observation"
    severity: Severity = "info"
    evidence_ids: list[str] = Field(default_factory=list)


class CheckedIn(BaseModel):
    what: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class NotCheckedIn(BaseModel):
    what: str = ""
    why: str = ""


class Submission(BaseModel):
    verdict: Verdict
    summary: str = ""
    findings: list[FindingIn] = Field(default_factory=list)
    checked: list[CheckedIn] = Field(default_factory=list)
    not_checked: list[NotCheckedIn] = Field(default_factory=list)


# The input schema the model sees for `submit_report`. Written by hand, not generated, so the
# wording of every field is part of the prompt.
SUBMIT_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": list(VERDICTS),
            "description": "high_risk: evidence of a way users lose funds or control. "
            "elevated_risk: capabilities or conditions that need trust in someone. "
            "no_red_flags_found: nothing found in what was checked (NOT 'safe'). "
            "insufficient_evidence: the tools did not give enough to judge.",
        },
        "summary": {
            "type": "string",
            "description": "Two to five sentences answering the person's question, saying what "
            "was and was not checked. Plain language. No claims beyond the findings.",
        },
        "findings": {
            "type": "array",
            "description": "One fact per finding.",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "description": "One sentence."},
                    "kind": {
                        "type": "string",
                        "enum": ["observation", "context"],
                        "description": "observation: about THIS contract, cites tool evidence "
                        "(T ids). context: about a known pattern, cites knowledge ids.",
                    },
                    "severity": {"type": "string", "enum": ["info", "low", "medium", "high"]},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ids received in this run, such as T3 or "
                        "taxonomy:privileged-mint. At least one; none may be invented.",
                    },
                },
                "required": ["claim", "kind", "severity", "evidence_ids"],
            },
        },
        "checked": {
            "type": "array",
            "description": "What was looked at, each with the evidence ids that show it ran.",
            "items": {
                "type": "object",
                "properties": {
                    "what": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["what", "evidence_ids"],
            },
        },
        "not_checked": {
            "type": "array",
            "description": "What was not looked at, or could not be, and why. A check that did "
            "not run is 'not checked', never 'not found'.",
            "items": {
                "type": "object",
                "properties": {"what": {"type": "string"}, "why": {"type": "string"}},
                "required": ["what", "why"],
            },
        },
    },
    "required": ["verdict", "summary", "findings", "checked", "not_checked"],
}


def submit_report_schema(with_review: bool) -> dict[str, Any]:
    """The `submit_report` input schema. With a source reviewer in the run the claim kinds gain
    `source_review`; without one it is `SUBMIT_REPORT_SCHEMA` unchanged, so what a run without a
    reviewer offers (and fingerprints) does not move."""
    if not with_review:
        return SUBMIT_REPORT_SCHEMA
    schema = copy.deepcopy(SUBMIT_REPORT_SCHEMA)
    kind = schema["properties"]["findings"]["items"]["properties"]["kind"]
    kind["enum"] = ["observation", "context", "source_review"]
    kind["description"] += (
        " source_review: about THIS contract's source, cites S ids from review_source."
    )
    return schema


SUBMIT_REPORT_SCHEMA_WITH_REVIEW: dict[str, Any] = submit_report_schema(True)


class Problem(BaseModel, frozen=True):
    where: str  # "findings[2]", "verdict", "report"
    code: str
    message: str


class CitedEvidence(BaseModel, frozen=True):
    id: str
    kind: Literal["chain", "knowledge", "source"]
    citation: str


class ReportFinding(BaseModel, frozen=True):
    claim: str
    kind: Kind
    severity: Severity
    evidence_ids: tuple[str, ...]


class ReportChecked(BaseModel, frozen=True):
    what: str
    evidence_ids: tuple[str, ...]


class ReportNotChecked(BaseModel, frozen=True):
    what: str
    why: str


class Unavailable(BaseModel, frozen=True):
    tool: str
    what: str


StoppedBecause = Literal[
    "report_accepted",
    "report_rejected",
    "no_report",
    "turn_limit",
    "time_limit",
    "cost_cap",
    "model_unavailable",
]


class Validation(BaseModel, frozen=True):
    accepted: bool
    attempts: int
    problems: tuple[Problem, ...] = ()  # of the last attempt, when it was not accepted


class RunInfo(BaseModel, frozen=True):
    model: str
    turns: int
    tool_calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float
    seconds: float
    stopped_because: StoppedBecause


class Report(BaseModel, frozen=True):
    chain: str
    address: str
    block: int | None
    verdict: Verdict
    summary: str
    findings: tuple[ReportFinding, ...]
    checked: tuple[ReportChecked, ...]
    not_checked: tuple[ReportNotChecked, ...]  # written by the model
    tools_not_available: tuple[Unavailable, ...]  # written by the runner, whatever the model said
    evidence: tuple[CitedEvidence, ...]  # every id cited above, resolved
    validation: Validation
    run: RunInfo
    # Written by the runner, never by the model: what the source reviewer read and did not. None
    # when no reviewer was wired into the run.
    source_review: ReviewSummary | None = None


SOURCE_REVIEW_PREFIX = "Source reviewer (model {model}), unchecked reading:"
NOT_EVIDENCE_OF_SAFETY = "Absence of a flag is not evidence of safety."
AS_PUBLISHED = (
    "Source is as published on the explorer, which can match similar rather than identical code."
)


def finding_label(report: Report, finding: ReportFinding) -> str:
    """The words the runner puts in front of a finding. Only `source_review` has any: the model
    cannot drop or change them, because they are not part of its claim."""
    if finding.kind != "source_review":
        return ""
    model = report.source_review.reviewer_model if report.source_review else None
    return SOURCE_REVIEW_PREFIX.format(model=model or "unknown")


def source_review_lines(summary: ReviewSummary) -> list[str]:
    """The coverage block, written by the runner from counts it kept. It says what was read and
    what was not, and never that the contract is fine."""
    reader = (
        f"read by {summary.reviewer_model}, a model; a reading is not proof"
        if summary.status in ("ran", "partial") and summary.reviewer_model
        else "no source was read"
    )
    lines = [f"Source review ({reader})"]
    if summary.status in ("ran", "partial"):
        read = (
            f"Read {summary.definitions_reviewed} of {summary.definitions_new} new definitions "
            f"({summary.chars_reviewed:,} of {summary.chars_total:,} characters of the source)"
        )
        if summary.definitions_known:
            libraries = ", ".join(
                f"{k.library} {k.version}: {k.definitions}" for k in summary.known_libraries
            )
            read += (
                f"; {summary.definitions_known} known-library definitions matched exactly and "
                f"were not read ({libraries})"
            )
        lines.append(f"  {read}.")
        if summary.status == "partial":
            lines.append(f"  Partly read: {summary.reason}.")
    else:
        lines.append(f"  Not reviewed: {summary.reason}.")
    lines.append(f"  {NOT_EVIDENCE_OF_SAFETY} {AS_PUBLISHED}")
    extra = []
    if summary.assembly_blocks:
        extra.append(f"Assembly blocks: {summary.assembly_blocks} (read with lower confidence).")
    if summary.scan_flags:
        flags = ", ".join(f"{f.flag} x{f.count}" for f in summary.scan_flags)
        extra.append(f"Scan flags (model-free): {flags}.")
    not_fetched = sum(d.count for d in summary.dropped)
    if not_fetched:
        why = ", ".join(sorted({d.reason for d in summary.dropped}))
        extra.append(f"Not fetched: {not_fetched} file(s) ({why}).")
    if summary.unsupported:
        extra.append(f"Unsupported: {summary.unsupported}.")
    if summary.files_not_checked or summary.definitions_not_reviewed:
        extra.append(
            f"Could not be read safely: {summary.files_not_checked} file(s), "
            f"{summary.definitions_not_reviewed} definition(s)."
        )
    if summary.confirmation_model:
        extra.append(f"A second reader ({summary.confirmation_model}) re-read places rated high.")
    if summary.cost_usd:
        extra.append(f"Cost ${summary.cost_usd:.4f}.")
    lines += [f"  {text}" for text in extra]
    return lines


def render_text(report: Report) -> str:
    """The report for a person: verdict, findings and their citations, then what was not checked."""
    lines = [
        f"Contract {report.address} on {report.chain}, state at block {report.block}",
        f"Verdict: {report.verdict.replace('_', ' ').upper()}",
    ]
    if report.source_review is not None:
        lines += source_review_lines(report.source_review)
    lines += ["", report.summary]
    if not report.validation.accepted:
        lines += ["", "The report did not pass the citation validator:"]
        lines += [f"  - {p.where}: {p.message}" for p in report.validation.problems]
    lines += ["", "Findings"]
    for number, finding in enumerate(report.findings, start=1):
        cited = ", ".join(finding.evidence_ids)
        label = finding_label(report, finding)
        label = f"{label} " if label else ""
        lines.append(
            f"  {number}. [{finding.kind}, {finding.severity}] {label}{finding.claim}  ({cited})"
        )
    if not report.findings:
        lines.append("  none")
    lines += ["", "Checked"]
    for item in report.checked:
        cited = f"  ({', '.join(item.evidence_ids)})" if item.evidence_ids else ""
        lines.append(f"  - {item.what}{cited}")
    lines += ["", "Not checked"]
    lines += [f"  - {item.what}: {item.why}" for item in report.not_checked]
    lines += [
        f"  - {item.tool}: {item.what} (not available in this version)"
        for item in report.tools_not_available
    ]
    lines += ["", "Evidence"]
    lines += [f"  {item.id}: {item.citation}" for item in report.evidence]
    run = report.run
    lines += [
        "",
        f"Run: {run.model}, {run.turns} model calls, {run.tool_calls} tool calls, "
        f"{run.prompt_tokens} tokens in ({run.cached_tokens} from cache) and "
        f"{run.completion_tokens} out, ${run.cost_usd:.4f}, {run.seconds:.0f}s, "
        f"stopped because: {run.stopped_because}",
    ]
    return "\n".join(lines)
