"""The citation validator: no claim without evidence the runner recorded.

It reads a submitted report and the run's evidence registry and returns a list of problems. It
never edits the report and uses no model; each rule is a plain check:

1. Every cited ID is in this run's registry (a tool result the runner stored, or a knowledge record
   the model was shown). An invented ID, or one from another run, is rejected.
2. A finding cites at least one ID.
3. An `observation` (about this contract) cites at least one successful tool result. A taxonomy
   entry or an audit finding can never be its only support: they show that a pattern exists
   somewhere, not that this contract has it. A `context` claim cites at least one knowledge ID.
4. Grounding: an address or 4-byte selector written in a claim must appear in the evidence the
   claim cites, so a mistyped or invented one is caught.
5. The verdict agrees with the findings: `high_risk` needs an observation rated high,
   `elevated_risk` one rated medium or above, and `no_red_flags_found` cannot sit beside either.
6. A few facts set the verdict's floor, so a manipulated or careless model cannot talk them away:
   a sell rehearsal that was blocked means `high_risk`, a lookalike of a protocol's contract means
   at least `elevated_risk`, and an address with no code means `insufficient_evidence`.
   `no_red_flags_found` also needs a successful `identify_contract`.
7. Wording about source: the words "unverified", "no verified source", "source not available" and
   the like are rejected unless a `get_verified_source` result in this run returned
   `verified=false`. A tool that was missing, failed or was not run means "not checked", never
   "unverified" (a report once said so only because the tool was not there yet).

Source review adds `source_review` claims (a model's reading of the contract's source, cited by
S ids) and rules 8 to 12. They only act when there are S records or a reviewer is on offer
(`ReviewRules`), so a run without a reviewer is checked exactly as before:

8. An S record of kind `span` can back only a `source_review` claim, and a `source_review` claim
   cites at least one. A `scan` record (a model-free observation of hidden characters or text aimed
   at a reader) can back an `observation` rated at most `low`, and nothing else.
9. A file id, line number or function name written in a `source_review` claim must appear in the
   evidence it cites.
10. A `source_review` claim is rated no higher than what its cited span allows: the span's own
    severity, and never `high` unless a blind second pass agreed (`confirmed_by`) and the
    high-risk switch (`review_may_set_high_risk`, off by default) is on.
11. If `identify_contract` says the code_review check applies, `get_verified_source` says the logic
    contract (the implementation, for a proxy) is verified, and `review_source` was not run, the
    report is rejected (`source_review_not_run`). A failed or refused review ends the forcing:
    "not checked" is then the right answer. Not enforced once no tool call is left.
12. `source_review` claims count toward `elevated_risk` (rated medium or above) and, when rule 10
    allowed it, `high_risk`; they contradict `no_red_flags_found` from medium up. They never lower
    a verdict and never satisfy a floor.

What it does NOT prove: that the cited evidence really supports the claim. A claim can cite a real
T-ID and still misread it. That is faithfulness, measured in Component 6 with an LLM judge checked
against labels; this validator only guarantees that every claim points at something that was
actually recorded.
"""

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from agent.evidence import Evidence, EvidenceRegistry
from agent.report import (
    MAX_CLAIM_CHARS,
    SEVERITY_RANK,
    FindingIn,
    Problem,
    Submission,
)

MAX_FINDINGS = 25

# Words that say a contract's source is unverified, unpublished or missing. Only a successful
# get_verified_source with verified=false lets a report say that.
_NO_SOURCE = re.compile(
    r"\bunverified\b|\bnot (?:yet )?verified\b|\bno (?:verified|published|available) source\b|"
    r"\bno source code\b|\bnot published\b|"
    r"\bsource(?: code)? (?:is |was |are )?(?:not |un)(?:available|published|verified)\b",
    re.IGNORECASE,
)

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_SELECTOR = re.compile(r"0x[0-9a-fA-F]{8}(?![0-9a-fA-F])")

# What a source_review claim may name, checked against the evidence it cites (rule 9).
_FILE_ID = re.compile(r"\bF[1-9][0-9]*\b")
_LINES = re.compile(
    r"\blines?\s+(\d{1,7})(?:\s*(?:-|\u2013|\u2014|to|and)\s*(?:lines?\s+)?(\d{1,7}))?",
    re.IGNORECASE,
)
_CODE_NAME = re.compile(
    r"`([A-Za-z_$][A-Za-z0-9_$]*)(?:\([^`]*\))?`|\b([A-Za-z_$][A-Za-z0-9_$]*)\("
)
# Words that are Solidity syntax, not a function the contract defines.
_NOT_A_NAME = frozenset(
    {
        *("require", "assert", "revert", "if", "for", "while", "return", "returns", "emit", "new"),
        *("keccak256", "sha256", "call", "delegatecall", "staticcall", "selfdestruct", "address"),
        *("uint", "uint256", "int", "bool", "bytes", "string", "mapping", "abi", "payable"),
        *("function", "modifier", "constructor", "event", "error"),
    }
)


@dataclass(frozen=True)
class ReviewRules:
    """What the run's source review allows the validator to enforce. The default is a run with no
    reviewer, for which none of rules 10 (the high part) and 11 apply."""

    offered: bool = False  # review_source is on offer: rule 11 may force it
    may_set_high: bool = False  # review_may_set_high_risk (config, off by default)
    can_still_run: bool = True  # False once no tool call is left (wrap-up): do not force


NO_REVIEW = ReviewRules()


@dataclass(frozen=True)
class ReviewFacts:
    """What the recorded results say about whether a source review applies and happened."""

    target: str | None = None  # the address analysed (identify_contract's)
    logic: str | None = None  # the implementation when identify found one, else the target
    applies: bool = False  # identify_contract: the code_review check applies
    verified: bool = False  # get_verified_source on `logic` said verified=true
    unverified: bool = False  # ... said verified=false
    ran: bool = False  # a review_source result (ok or failed) exists for target or logic


def review_facts(registry: EvidenceRegistry) -> ReviewFacts:
    """Rule 11's inputs, read from the registry only (no model). `logic` follows identify_contract's
    own proxy read, the same function review_source resolves with."""
    identify = next(iter(registry.tool_results("identify_contract")), None)
    if identify is None or not isinstance(identify.data, dict) or identify.provenance is None:
        return ReviewFacts()
    target = (identify.provenance.address or "").lower() or None
    implementation = identify.data.get("implementation")
    logic = target
    if isinstance(implementation, dict) and implementation.get("address"):
        logic = str(implementation["address"]).lower()
    applies = any(
        isinstance(c, dict) and c.get("name") == "code_review" and c.get("applies") is True
        for c in identify.data.get("checks") or []
    )
    verified = unverified = False
    for item in registry.tool_results("get_verified_source"):
        asked = str((item.arguments or {}).get("address", "")).lower()
        if asked == logic and isinstance(item.data, dict):
            verified |= item.data.get("verified") is True
            unverified |= item.data.get("verified") is False
    ran = any(
        str((e.arguments or {}).get("address", "")).lower() in (target, logic)
        for e in registry
        if e.source == "tool" and e.tool == "review_source"
    )
    return ReviewFacts(target, logic, applies, verified, unverified, ran)


def parse_submission(arguments: str) -> tuple[Submission | None, list[Problem]]:
    """The model's `submit_report` arguments as a Submission, or why they are not one."""
    try:
        raw = json.loads(arguments)
    except ValueError:
        return None, [
            Problem(
                where="report", code="schema_error", message="The arguments are not valid JSON."
            )
        ]
    try:
        return Submission.model_validate(raw), []
    except ValidationError as err:
        shown = "; ".join(
            f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in err.errors()[:4]
        )
        return None, [Problem(where="report", code="schema_error", message=shown[:400])]


def _known(
    ids: list[str], registry: EvidenceRegistry, where: str
) -> tuple[list[Evidence], list[Problem]]:
    found, problems = [], []
    for evidence_id in ids:
        item = registry.get(evidence_id)
        if item is None:
            problems.append(
                Problem(
                    where=where,
                    code="unknown_evidence",
                    message=f"{evidence_id!r} is not evidence recorded in this run. Cite only "
                    "ids that appeared in tool results you received.",
                )
            )
        else:
            found.append(item)
    return found, problems


def check_wording(text: str, where: str, registry: EvidenceRegistry) -> list[Problem]:
    """A claim that the source is unverified or missing needs a get_verified_source result that
    says verified=false."""
    found = _NO_SOURCE.search(text)
    shown = any(
        isinstance(item.data, dict) and item.data.get("verified") is False
        for item in registry.tool_results("get_verified_source")
    )
    if found is None or shown:
        return []
    return [
        Problem(
            where=where,
            code="unsupported_unverified_wording",
            message=f"The words {found.group(0)!r} say the contract's source is unverified or "
            "missing, but no get_verified_source result in this run says verified=false. A tool "
            "that is missing, failed or was not run means 'not checked': say that instead.",
        )
    ]


def _identifiers(text: str) -> set[str]:
    return {m.lower() for m in _ADDRESS.findall(text)} | {
        m.lower() for m in _SELECTOR.findall(text)
    }


def _span_kind(item: Evidence) -> str | None:
    """`span` or `scan` for a source record, read from what the writer was shown."""
    if item.source != "source" or not isinstance(item.data, dict):
        return None
    return item.data.get("kind")


def _span_severity(item: Evidence) -> str:
    """The severity a source record carries: the full record's, else what the writer saw."""
    record = item.record if isinstance(item.record, dict) else {}
    match = record.get("match") if isinstance(record.get("match"), dict) else {}
    shown = item.data if isinstance(item.data, dict) else {}
    return match.get("severity") or shown.get("severity") or "low"


def _ceiling(item: Evidence, review: ReviewRules) -> str:
    """The highest rating a claim may give on this span (rule 10): its own severity, and never
    `high` unless a blind second pass agreed and the switch is on."""
    severity = _span_severity(item)
    if severity == "high":
        record = item.record if isinstance(item.record, dict) else {}
        match = record.get("match") if isinstance(record.get("match"), dict) else {}
        if not (match.get("confirmed_by") and review.may_set_high):
            return "medium"
    return severity


def _names_in(text: str) -> list[str]:
    found = []
    for quoted, called in _CODE_NAME.findall(text):
        name = quoted or called
        if name and name.lower() not in _NOT_A_NAME and name not in found:
            found.append(name)
    return found


def check_source_claim(
    finding: FindingIn,
    where: str,
    items: list[Evidence],
    supported: str,
    review: ReviewRules,
) -> list[Problem]:
    """Rules 8 to 10: how a claim may use S records."""
    spans = [e for e in items if _span_kind(e) == "span"]
    scans = [e for e in items if _span_kind(e) == "scan"]
    problems: list[Problem] = []

    def bad(code: str, message: str) -> None:
        problems.append(Problem(where=where, code=code, message=message))

    if finding.kind != "source_review":
        if spans:
            bad(
                "source_evidence_wrong_kind",
                f"{spans[0].id} is a model's reading of the source: it can back only a "
                "source_review claim, not an observation or a context claim.",
            )
        if scans and finding.kind == "context":
            bad(
                "source_evidence_wrong_kind",
                f"{scans[0].id} is an observation about this contract's source, not knowledge "
                "about a pattern: it cannot back a context claim.",
            )
        if scans and finding.kind == "observation" and SEVERITY_RANK[finding.severity] > 1:
            bad(
                "source_scan_severity",
                f"{scans[0].id} is a model-free scan flag: an observation that rests on it is "
                "rated at most low.",
            )
        return problems
    if not spans:
        bad(
            "source_review_needs_span",
            "A source_review claim must cite at least one S id of kind span from review_source.",
        )
        return problems
    allowed = max((_ceiling(e, review) for e in spans), key=lambda s: SEVERITY_RANK[s])
    if SEVERITY_RANK[finding.severity] > SEVERITY_RANK[allowed]:
        bad(
            "source_review_severity",
            f"The highest severity the cited S evidence allows is {allowed} (a reading by one "
            "model is never rated above its own severity, and high needs a second, blind reading "
            "that agreed).",
        )
    shown = [e.data for e in spans + scans if isinstance(e.data, dict)]
    file_ids = {str(d.get("file_id")) for d in shown}
    ranges = [(d.get("start_line"), d.get("end_line")) for d in shown]
    for token in sorted(set(_FILE_ID.findall(finding.claim))):
        if token not in file_ids:
            bad("source_review_ungrounded", f"{token} is written in the claim but is not a file id")
    for match in _LINES.finditer(finding.claim):
        for number in (n for n in match.groups() if n):
            if not any(
                isinstance(lo, int) and isinstance(hi, int) and lo <= int(number) <= hi
                for lo, hi in ranges
            ):
                bad(
                    "source_review_ungrounded",
                    f"line {number} is written in the claim but is outside the lines of the S "
                    "evidence it cites.",
                )
    for name in _names_in(finding.claim):
        if not re.search(rf"(?<![a-z0-9_$]){re.escape(name.lower())}(?![a-z0-9_$])", supported):
            bad(
                "source_review_ungrounded",
                f"{name} is written in the claim but does not appear in the evidence it cites.",
            )
    return problems


def check_finding(
    finding: FindingIn,
    index: int,
    registry: EvidenceRegistry,
    review: ReviewRules = NO_REVIEW,
) -> list[Problem]:
    where = f"findings[{index}]"
    problems: list[Problem] = []
    if not finding.claim.strip():
        problems.append(Problem(where=where, code="empty_claim", message="The claim is empty."))
    if len(finding.claim) > MAX_CLAIM_CHARS:
        problems.append(
            Problem(
                where=where,
                code="claim_too_long",
                message=f"The claim is over {MAX_CLAIM_CHARS} characters: one fact per finding.",
            )
        )
    if not finding.evidence_ids:
        problems.append(
            Problem(where=where, code="no_evidence", message="The finding cites no evidence id.")
        )
        return problems
    items, unknown = _known(finding.evidence_ids, registry, where)
    problems += unknown
    tool_ok = [e for e in items if e.source == "tool" and e.ok]
    knowledge = [e for e in items if e.source == "knowledge"]
    scans = [e for e in items if _span_kind(e) == "scan"]
    # A scan flag is a runner-made, model-free observation: it supports an observation like a
    # successful tool result does (rule 8).
    if finding.kind == "observation" and not (tool_ok or scans):
        if knowledge:
            why = (
                "it cites only knowledge records, which show that a pattern exists, not that "
                "this contract has it"
            )
        elif items:
            why = "it cites only failed tool results, which support 'not checked', not a finding"
        else:
            why = "none of its ids are valid"

        problems.append(
            Problem(
                where=where,
                code="observation_needs_tool_evidence",
                message=f"An observation is about this contract and must cite a successful tool "
                f"result (a T id); {why}. Cite one, or make it a context claim.",
            )
        )
    problems += check_wording(finding.claim, where, registry)
    if finding.kind == "context" and not knowledge:
        problems.append(
            Problem(
                where=where,
                code="context_needs_knowledge_evidence",
                message="A context claim must cite at least one knowledge id (a taxonomy entry "
                "or an audit finding you were shown).",
            )
        )
    supported = " ".join(e.text() for e in items)
    problems += check_source_claim(finding, where, items, supported, review)
    if items:
        for token in sorted(_identifiers(finding.claim)):
            if token not in supported:
                problems.append(
                    Problem(
                        where=where,
                        code="ungrounded_identifier",
                        message=f"{token} is written in the claim but does not appear in the "
                        "evidence it cites.",
                    )
                )
    return problems


def _facts(registry: EvidenceRegistry) -> dict[str, bool]:
    """What the recorded tool results settle, whatever the report says."""
    sell_blocked = lookalike = no_code = identified = False
    for item in registry.tool_results():
        data = item.data if isinstance(item.data, dict) else {}
        if item.tool == "identify_contract":
            identified = True
            lookalike |= data.get("summary") == "lookalike"
            no_code |= data.get("summary") == "no_code"
        elif item.tool == "simulate_sell":
            sell_blocked |= data.get("verdict") == "sell_blocked"
    return {
        "sell_blocked": sell_blocked,
        "lookalike": lookalike,
        "no_code": no_code,
        "identified": identified,
    }


def check_verdict(
    submission: Submission, valid: list[FindingIn], registry: EvidenceRegistry
) -> list[Problem]:
    verdict = submission.verdict
    rank = max((SEVERITY_RANK[f.severity] for f in valid if f.kind == "observation"), default=-1)
    # Rule 12: a source_review claim can raise a verdict or contradict "no red flags", never lower
    # one. The floors below read tool facts only.
    reading = max(
        (SEVERITY_RANK[f.severity] for f in valid if f.kind == "source_review"), default=-1
    )
    facts = _facts(registry)
    problems = []

    def bad(message: str, code: str = "verdict_unsupported") -> None:
        problems.append(Problem(where="verdict", code=code, message=message))

    if verdict == "high_risk" and max(rank, reading) < SEVERITY_RANK["high"]:
        bad(
            "high_risk needs at least one observation finding rated high (with a valid citation)"
            ", or a source_review finding rated high (which needs a second, blind reading)."
            if reading >= 0
            else "high_risk needs at least one observation finding rated high (with a valid "
            "citation)."
        )
    if verdict == "elevated_risk" and max(rank, reading) < SEVERITY_RANK["medium"]:
        bad(
            "elevated_risk needs at least one observation finding rated medium or high (with a "
            "valid citation)."
        )
    if verdict == "no_red_flags_found":
        if max(rank, reading) >= SEVERITY_RANK["medium"]:
            bad(
                "no_red_flags_found contradicts a finding rated medium or high.",
                "verdict_contradicts_findings",
            )
        if not facts["identified"]:
            bad("no_red_flags_found needs a successful identify_contract result.")
    if facts["sell_blocked"] and verdict != "high_risk":
        bad(
            "The sell rehearsal was blocked (simulate_sell verdict sell_blocked), so the verdict "
            "must be high_risk.",
            "verdict_below_floor",
        )
    if facts["lookalike"] and verdict in ("no_red_flags_found", "insufficient_evidence"):
        bad(
            "identify_contract found a lookalike of a protocol's contract, so the verdict must "
            "be elevated_risk or high_risk.",
            "verdict_below_floor",
        )
    if facts["no_code"] and verdict != "insufficient_evidence":
        bad(
            "There is no code at this address (identify_contract summary no_code), so the "
            "verdict must be insufficient_evidence.",
            "verdict_below_floor",
        )
    return problems


def valid_findings(
    submission: Submission, registry: EvidenceRegistry, review: ReviewRules = NO_REVIEW
) -> list[FindingIn]:
    """The findings that pass on their own: what survives when the report as a whole is rejected."""
    return [
        f
        for i, f in enumerate(submission.findings[:MAX_FINDINGS])
        if not check_finding(f, i, registry, review)
    ]


def check_review_ran(registry: EvidenceRegistry, review: ReviewRules) -> list[Problem]:
    """Rule 11: when the source review applies and could still run, it has to have run."""
    if not (review.offered and review.can_still_run):
        return []
    facts = review_facts(registry)
    if not (facts.applies and facts.verified and not facts.ran):
        return []
    return [
        Problem(
            where="report",
            code="source_review_not_run",
            message=f"identify_contract says the code_review check applies and get_verified_source "
            f"says the contract's logic ({facts.logic}) has verified source, but review_source "
            "has not been run. Call review_source, then submit the report again.",
        )
    ]


def validate(
    submission: Submission, registry: EvidenceRegistry, review: ReviewRules = NO_REVIEW
) -> list[Problem]:
    """Every problem with the submission; an empty list means it is accepted."""
    problems: list[Problem] = []
    if len(submission.findings) > MAX_FINDINGS:
        problems.append(
            Problem(
                where="findings",
                code="too_many_findings",
                message=f"More than {MAX_FINDINGS} findings: keep the ones that matter.",
            )
        )
    if not submission.summary.strip():
        problems.append(
            Problem(where="summary", code="empty_summary", message="The summary is empty.")
        )
    problems += check_wording(submission.summary, "summary", registry)
    for index, finding in enumerate(submission.findings[:MAX_FINDINGS]):
        problems += check_finding(finding, index, registry, review)
    for index, item in enumerate(submission.checked):
        where = f"checked[{index}]"
        problems += check_wording(item.what, where, registry)
        if not item.what.strip():
            problems.append(Problem(where=where, code="empty_check", message="The entry is empty."))
        if not item.evidence_ids:
            problems.append(
                Problem(where=where, code="no_evidence", message="A check must cite what ran.")
            )
            continue
        items, unknown = _known(item.evidence_ids, registry, where)
        problems += unknown
        if items and not any(e.ok for e in items):
            problems.append(
                Problem(
                    where=where,
                    code="check_failed",
                    message="Every cited result failed, so this was not checked: list it under "
                    "not_checked.",
                )
            )
    for index, item in enumerate(submission.not_checked):
        problems += check_wording(f"{item.what} {item.why}", f"not_checked[{index}]", registry)
        if not item.what.strip() or not item.why.strip():
            problems.append(
                Problem(
                    where=f"not_checked[{index}]",
                    code="empty_not_checked",
                    message="Say what was not checked and why.",
                )
            )
    valid = valid_findings(submission, registry, review)
    problems += check_verdict(submission, valid, registry)
    problems += check_review_ran(registry, review)
    return problems
