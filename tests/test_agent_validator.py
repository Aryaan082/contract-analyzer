"""The citation validator: what it rejects, what it accepts, and what it does not claim to catch."""

import json

import pytest

from agent.evidence import EvidenceRegistry
from agent.report import FindingIn, Submission
from agent.validator import check_finding, parse_submission, valid_findings, validate
from chain.models import Provenance

PROV = Provenance(chain="ethereum", chain_id=1, address="0x" + "ab" * 20, block=7, source="x")
ADDR = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20


def registry(
    *, summary="standard", sell=None, risk_kinds=("mint",), identify=True
) -> EvidenceRegistry:
    """T1 identify, T2 risk_scan, T3 a failed tool, then two knowledge records."""
    reg = EvidenceRegistry()
    if identify:
        reg.add_tool("identify_contract", {"address": ADDR}, PROV, {"summary": summary})
    else:
        reg.add_tool("list_selectors", {"address": ADDR}, PROV, {"selectors": []})
    reg.add_tool(
        "risk_scan",
        {"address": ADDR},
        PROV,
        {"signals": [{"kind": k, "selector": "0x40c10f19"} for k in risk_kinds]},
    )
    reg.add_tool("simulate_sell", {"token": ADDR}, PROV, {"error": "x"}, ok=False)
    reg.add_knowledge("taxonomy:privileged-mint", "Taxonomy, taxonomy:privileged-mint", "mint text")
    reg.add_knowledge("r1:F-1", "Audit, finding F-1, page 3", "finding text")
    if sell:
        reg.add_tool("simulate_sell", {"token": OTHER}, PROV, {"verdict": sell})
    return reg


def finding(
    claim="The contract has a mint function.", kind="observation", severity="info", ids=("T2",)
):
    return FindingIn(claim=claim, kind=kind, severity=severity, evidence_ids=list(ids))


def submission(*findings, verdict="no_red_flags_found", **kwargs) -> Submission:
    return Submission(
        verdict=verdict,
        summary="A summary.",
        findings=list(findings) or [finding()],
        checked=kwargs.get("checked", []),
        not_checked=kwargs.get("not_checked", []),
    )


def codes(problems) -> set[str]:
    return {p.code for p in problems}


def test_a_well_cited_report_is_accepted():
    sub = submission(
        finding(),
        finding("Mint is a known risk pattern.", "context", "info", ("taxonomy:privileged-mint",)),
    )
    assert validate(sub, registry()) == []


def test_an_id_that_was_never_recorded_is_rejected():
    problems = check_finding(finding(ids=("T99",)), 0, registry())
    assert codes(problems) >= {"unknown_evidence"}
    assert "T99" in problems[0].message


def test_a_finding_with_no_evidence_is_rejected():
    assert codes(check_finding(finding(ids=()), 0, registry())) == {"no_evidence"}


def test_an_observation_cannot_rest_on_knowledge_alone():
    problems = check_finding(finding(ids=("taxonomy:privileged-mint", "r1:F-1")), 0, registry())
    assert codes(problems) == {"observation_needs_tool_evidence"}
    assert "not that this contract has it" in problems[0].message


def test_an_observation_cannot_rest_on_a_failed_tool_result():
    problems = check_finding(finding(ids=("T3",)), 0, registry())
    assert codes(problems) == {"observation_needs_tool_evidence"}


def test_a_context_claim_needs_a_knowledge_id():
    problems = check_finding(finding(kind="context", ids=("T2",)), 0, registry())
    assert codes(problems) == {"context_needs_knowledge_evidence"}


def test_an_observation_may_also_cite_knowledge_next_to_a_tool_result():
    assert check_finding(finding(ids=("T2", "taxonomy:privileged-mint")), 0, registry()) == []


def test_an_address_in_a_claim_must_appear_in_the_cited_evidence():
    assert check_finding(finding(f"The token at {ADDR} can mint."), 0, registry()) == []
    problems = check_finding(finding(f"The owner {OTHER} can mint."), 0, registry())
    assert codes(problems) == {"ungrounded_identifier"}


def test_the_grounding_check_ignores_case_and_covers_selectors():
    assert check_finding(finding("Function 0x40C10F19 is present."), 0, registry()) == []
    problems = check_finding(finding("Function 0xdeadbeef is present."), 0, registry())
    assert codes(problems) == {"ungrounded_identifier"}


def test_a_selector_is_not_confused_with_the_start_of_an_address():
    assert check_finding(finding(f"See {ADDR}."), 0, registry()) == []


def test_an_empty_or_overlong_claim_is_rejected():
    assert "empty_claim" in codes(check_finding(finding(claim="  "), 0, registry()))
    assert "claim_too_long" in codes(check_finding(finding(claim="x" * 700), 0, registry()))


@pytest.mark.parametrize(
    ("verdict", "severity", "ok"),
    [
        ("high_risk", "high", True),
        ("high_risk", "medium", False),
        ("elevated_risk", "medium", True),
        ("elevated_risk", "low", False),
        ("no_red_flags_found", "info", True),
        ("no_red_flags_found", "medium", False),
        ("insufficient_evidence", "high", True),
    ],
)
def test_the_verdict_has_to_agree_with_the_observations(verdict, severity, ok):
    sub = submission(finding(severity=severity), verdict=verdict)
    assert (validate(sub, registry()) == []) is ok


def test_high_risk_cannot_rest_on_context_alone():
    context = finding("Mint is dangerous.", "context", "high", ("taxonomy:privileged-mint",))
    assert "verdict_unsupported" in codes(
        validate(submission(context, verdict="high_risk"), registry())
    )


def test_no_red_flags_needs_a_successful_identify():
    problems = validate(submission(verdict="no_red_flags_found"), registry(identify=False))
    assert "verdict_unsupported" in codes(problems)


def test_a_blocked_sell_forces_high_risk():
    reg = registry(sell="sell_blocked")
    low = submission(finding(severity="high", ids=("T2",)), verdict="elevated_risk")
    assert "verdict_below_floor" in codes(validate(low, reg))
    assert validate(submission(finding(severity="high"), verdict="high_risk"), reg) == []


def test_a_lookalike_cannot_be_called_clean():
    reg = registry(summary="lookalike")
    assert "verdict_below_floor" in codes(validate(submission(verdict="no_red_flags_found"), reg))
    ok = submission(finding(severity="medium"), verdict="elevated_risk")
    assert validate(ok, reg) == []


def test_no_code_means_insufficient_evidence():
    reg = registry(summary="no_code")
    assert "verdict_below_floor" in codes(validate(submission(verdict="no_red_flags_found"), reg))
    assert validate(submission(verdict="insufficient_evidence"), reg) == []


def test_checked_entries_must_cite_something_that_ran():
    sub = submission(
        checked=[
            {"what": "a", "evidence_ids": []},
            {"what": "b", "evidence_ids": ["T3"]},
            {"what": "c", "evidence_ids": ["T77"]},
        ]
    )
    sub = Submission.model_validate(sub.model_dump())
    assert codes(validate(sub, registry())) >= {"no_evidence", "check_failed", "unknown_evidence"}


def test_not_checked_entries_need_a_reason():
    sub = submission(not_checked=[{"what": "owner", "why": " "}])
    sub = Submission.model_validate(sub.model_dump())
    assert "empty_not_checked" in codes(validate(sub, registry()))


def test_valid_findings_keeps_only_what_passes_on_its_own():
    good, bad = finding(), finding(ids=("T99",))
    assert valid_findings(submission(good, bad), registry()) == [good]


def test_a_report_that_misreads_real_evidence_is_still_accepted():
    """The documented limit: the validator checks that evidence exists and is of the right kind,
    not that it says what the claim says. T1 only says 'standard'; this claim is a stretch."""
    stretch = finding("The contract has been audited and is safe.", severity="info", ids=("T1",))
    assert validate(submission(stretch), registry()) == []


def test_parse_submission_reports_bad_json_and_bad_values():
    assert parse_submission("{nope")[0] is None
    bad = parse_submission(json.dumps({"verdict": "safe"}))
    assert bad[0] is None and bad[1][0].code == "schema_error" and "verdict" in bad[1][0].message
    good, problems = parse_submission(json.dumps({"verdict": "high_risk", "findings": []}))
    assert good is not None and problems == []


# --- wording about source: missing is "not checked", never "unverified" ----------------------


def with_source(*, verified: bool | None, ok: bool = True) -> EvidenceRegistry:
    """The usual registry (T1 identify, T2 risk_scan, ...) plus a get_verified_source result."""
    reg = registry()
    data = {"verified": verified} if verified is not None else {"error": {"code": "explorer_error"}}
    reg.add_tool("get_verified_source", {"address": ADDR}, PROV, data, ok=ok)
    return reg


NO_SOURCE_WORDS = [
    "No verified source is available for this contract.",
    "Treat it as unverified.",
    "The source is not available.",
    "The contract is not verified.",
    "There is no source code to read.",
    "Its source was never published; it is not published anywhere.",
]


@pytest.mark.parametrize("words", NO_SOURCE_WORDS)
def test_saying_the_source_is_missing_needs_a_result_that_says_verified_false(words):
    for reg in (registry(), with_source(verified=True), with_source(verified=None, ok=False)):
        problems = check_finding(finding(f"The token has mint. {words}"), 0, reg)
        assert codes(problems) == {"unsupported_unverified_wording"}, reg
        assert "not checked" in problems[0].message
    assert (
        check_finding(finding(f"The token has mint. {words}"), 0, with_source(verified=False)) == []
    )


def test_ordinary_not_checked_wording_about_source_is_fine():
    for words in (
        "I did not read the source text.",
        "Whether the source is verified was not checked.",
        "get_verified_source was not available in this run.",
    ):
        assert check_finding(finding(f"The token has mint. {words}"), 0, registry()) == [], words


def test_the_wording_rule_covers_the_summary_checked_and_not_checked_text():
    sub = Submission.model_validate(
        {
            "verdict": "no_red_flags_found",
            "summary": "Nothing found, but the source is unverified.",
            "findings": [finding().model_dump()],
            "checked": [{"what": "Source: not verified", "evidence_ids": ["T1"]}],
            "not_checked": [{"what": "Source", "why": "No verified source is available."}],
        }
    )
    where = {
        p.where for p in validate(sub, registry()) if p.code == "unsupported_unverified_wording"
    }
    assert where == {"summary", "checked[0]", "not_checked[0]"}
    clean = validate(sub, with_source(verified=False))
    assert not [p for p in clean if p.code == "unsupported_unverified_wording"]


def test_a_finding_with_the_wrong_wording_is_not_kept_when_the_report_falls_back():
    bad = finding("The token has mint, and its source is unverified.")
    assert valid_findings(submission(finding(), bad), registry()) == [finding()]
