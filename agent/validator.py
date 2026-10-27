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

What it does NOT prove: that the cited evidence really supports the claim. A claim can cite a real
T-ID and still misread it. That is faithfulness, measured in Component 6 with an LLM judge checked
against labels; this validator only guarantees that every claim points at something that was
actually recorded.
"""

import json
import re

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

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_SELECTOR = re.compile(r"0x[0-9a-fA-F]{8}(?![0-9a-fA-F])")


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


def _identifiers(text: str) -> set[str]:
    return {m.lower() for m in _ADDRESS.findall(text)} | {
        m.lower() for m in _SELECTOR.findall(text)
    }


def check_finding(finding: FindingIn, index: int, registry: EvidenceRegistry) -> list[Problem]:
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
    if finding.kind == "observation" and not tool_ok:
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
    if finding.kind == "context" and not knowledge:
        problems.append(
            Problem(
                where=where,
                code="context_needs_knowledge_evidence",
                message="A context claim must cite at least one knowledge id (a taxonomy entry "
                "or an audit finding you were shown).",
            )
        )
    if items:
        supported = " ".join(e.text() for e in items)
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
    facts = _facts(registry)
    problems = []

    def bad(message: str, code: str = "verdict_unsupported") -> None:
        problems.append(Problem(where="verdict", code=code, message=message))

    if verdict == "high_risk" and rank < SEVERITY_RANK["high"]:
        bad("high_risk needs at least one observation finding rated high (with a valid citation).")
    if verdict == "elevated_risk" and rank < SEVERITY_RANK["medium"]:
        bad(
            "elevated_risk needs at least one observation finding rated medium or high (with a "
            "valid citation)."
        )
    if verdict == "no_red_flags_found":
        if rank >= SEVERITY_RANK["medium"]:
            bad(
                "no_red_flags_found contradicts an observation finding rated medium or high.",
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


def valid_findings(submission: Submission, registry: EvidenceRegistry) -> list[FindingIn]:
    """The findings that pass on their own: what survives when the report as a whole is rejected."""
    return [
        f
        for i, f in enumerate(submission.findings[:MAX_FINDINGS])
        if not check_finding(f, i, registry)
    ]


def validate(submission: Submission, registry: EvidenceRegistry) -> list[Problem]:
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
    for index, finding in enumerate(submission.findings[:MAX_FINDINGS]):
        problems += check_finding(finding, index, registry)
    for index, item in enumerate(submission.checked):
        where = f"checked[{index}]"
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
        if not item.what.strip() or not item.why.strip():
            problems.append(
                Problem(
                    where=f"not_checked[{index}]",
                    code="empty_not_checked",
                    message="Say what was not checked and why.",
                )
            )
    valid = valid_findings(submission, registry)
    problems += check_verdict(submission, valid, registry)
    return problems
