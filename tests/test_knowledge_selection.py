"""Which findings are kept: severity gate, exploit review, and the committed decisions."""

import pytest

from knowledge.findings import load_findings
from knowledge.selection import (
    REVIEWED_SEVERITIES,
    Review,
    SelectionError,
    load_reviews,
    select,
)
from tests.test_knowledge_findings import make_finding

REPORT = "test-tob-2024-01"


def finding(number: int, severity: str):
    return make_finding(
        id=f"{REPORT}:TOB-T-{number}", finding_id=f"TOB-T-{number}", severity=severity
    )


def review(number: int, exploit: bool, why: str = "A reason that is long enough.") -> Review:
    return Review(id=f"{REPORT}:TOB-T-{number}", exploit=exploit, why=why)


def test_only_reviewed_severities_can_be_kept_and_the_rest_are_listed():
    findings = [
        finding(1, "high"),
        finding(2, "low"),
        finding(3, "informational"),
        finding(4, "undetermined"),
    ]
    reviews = {r.id: r for r in [review(1, True), review(4, True)]}
    kept, excluded, problems = select(findings, reviews, REPORT)
    assert [f.finding_id for f in kept] == ["TOB-T-1", "TOB-T-4"]  # undetermined is judged too
    assert [(e.finding_id, e.reason, e.why) for e in excluded] == [
        ("TOB-T-2", "below_severity", None),
        ("TOB-T-3", "below_severity", None),
    ]
    assert problems == []
    assert {"low", "informational"}.isdisjoint(REVIEWED_SEVERITIES)


def test_a_severe_finding_judged_not_an_exploit_is_listed_with_the_reason():
    findings = [finding(1, "high"), finding(2, "medium")]
    reviews = {
        r.id: r for r in [review(1, False, "Process best practice, no attacker."), review(2, True)]
    }
    kept, excluded, problems = select(findings, reviews, REPORT)
    assert [f.finding_id for f in kept] == ["TOB-T-2"]
    assert [(e.finding_id, e.reason, e.why) for e in excluded] == [
        ("TOB-T-1", "not_an_exploit", "Process best practice, no attacker.")
    ]
    assert problems == []


def test_a_severe_finding_with_no_decision_is_a_problem_not_a_default():
    kept, excluded, problems = select([finding(1, "high")], {}, REPORT)
    assert kept == [] and excluded == []
    assert problems == [f"{REPORT}:TOB-T-1: rated high, but has no decision"]


def test_a_decision_for_a_finding_the_report_does_not_have_is_a_problem():
    reviews = {r.id: r for r in [review(1, True), review(9, True)]}
    _, _, problems = select([finding(1, "high")], reviews, REPORT)
    assert problems == [f"{REPORT}:TOB-T-9: a decision for a finding this report does not have"]
    # decisions for other reports are none of this report's business
    other = Review(id="other-tob-2024-01:TOB-X-1", exploit=True, why="Another report entirely.")
    assert select([finding(1, "high")], {**reviews, other.id: other}, REPORT)[2] == problems


def write(tmp_path, text):
    path = tmp_path / "review.toml"
    path.write_text(text)
    return path


GOOD = '[[review]]\nid = "a-tob-2024-01:TOB-A-1"\nexploit = true\nwhy = "Anyone can take funds."\n'


def test_the_review_file_loads_and_rejects_nonsense(tmp_path):
    assert list(load_reviews(write(tmp_path, GOOD))) == ["a-tob-2024-01:TOB-A-1"]
    for text in (
        GOOD + GOOD,  # decided twice
        GOOD.replace("true", '"yes"'),  # not a boolean
        GOOD.replace("Anyone can take funds.", "short"),  # a reason is required
        GOOD.replace("a-tob-2024-01:TOB-A-1", "no colon"),
        "[[review",  # not TOML
    ):
        with pytest.raises(SelectionError):
            load_reviews(write(tmp_path, text))


# --- the committed data ----------------------------------------------------------------------


def test_the_committed_findings_are_exactly_what_the_committed_review_says():
    reviews = load_reviews()
    files = load_findings()
    kept = {f.id for file in files for f in file.findings}
    not_exploits = {
        f"{file.report}:{e.finding_id}": e
        for file in files
        for e in file.excluded
        if e.reason == "not_an_exploit"
    }
    assert kept == {i for i, r in reviews.items() if r.exploit}
    assert set(not_exploits) == {i for i, r in reviews.items() if not r.exploit}
    assert all(e.why == reviews[i].why for i, e in not_exploits.items())


def test_nothing_below_the_severity_gate_is_kept_and_nothing_severe_is_dropped_unreviewed():
    for file in load_findings():
        assert all(f.severity in REVIEWED_SEVERITIES for f in file.findings)
        for e in file.excluded:
            if e.reason == "below_severity":
                assert e.severity not in REVIEWED_SEVERITIES
            else:
                assert e.severity in REVIEWED_SEVERITIES
