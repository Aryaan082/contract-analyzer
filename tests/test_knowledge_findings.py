"""Findings records: validation of untrusted text, the stable ID, and the committed data."""

import re

import pytest
from pydantic import ValidationError

from knowledge.findings import (
    Exclusion,
    Finding,
    FindingsFile,
    Source,
    all_findings,
    load_findings,
    save_findings,
)
from knowledge.models import load_manifest
from knowledge.taxonomy import load_taxonomy

URL = "https://raw.githubusercontent.com/trailofbits/publications/" + "a" * 40 + "/reviews/t.pdf"


def make_finding(**changes) -> Finding:
    fields = {
        "id": "test-tob-2024-01:TOB-T-1",
        "report": "test-tob-2024-01",
        "protocol": "Test Protocol",
        "auditor": "Trail of Bits",
        "date": "2024-01",
        "finding_id": "TOB-T-1",
        "severity": "high",
        "title": "A problem in the pool",
        "category": "Data Validation",
        "difficulty": "Low",
        "affected_contract": "contracts/Pool.sol",
        "description": "The pool does not check the value it is given before it uses it.",
        "recommendation": "Short term, add a check on the value.",
        "page": 12,
        "page_end": 13,
        "url": URL,
    }
    return Finding(**{**fields, **changes})


def make_file(findings, **changes) -> FindingsFile:
    fields = {
        "report": "test-tob-2024-01",
        "source": Source(
            repository="trailofbits/publications",
            commit="a" * 40,
            file="t.pdf",
            sha256=None,
            licence="CC-BY-SA-4.0",
            licence_url="https://example.invalid/LICENSE",
        ),
        "stated_counts": {},
        "table_rows": len(findings),
        "findings": tuple(findings),
    }
    return FindingsFile(**{**fields, **changes})


def test_a_valid_record_has_a_citation_link_to_its_page():
    finding = make_finding()
    assert finding.cite_url == URL + "#page=12"


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "bad‮title"},  # bidi override
        {"description": "The text has a zero​width space in it, which hides things."},
        {"description": "A decode failure � leaves this marker in the middle of the text."},
        {"recommendation": "Short term, do\x07 something."},  # control character
        {"description": "too short"},
        {"title": "x" * 301},
        {"severity": "catastrophic"},
        {"id": "other-report:TOB-T-1"},  # id must be '<report>:<finding_id>'
        {"finding_id": "has space"},
        {"page": 0},
        {"page": 9, "page_end": 3},
        {"url": "https://example.com/t.pdf"},
        {"date": "January 2024"},
    ],
)
def test_a_record_with_a_bad_field_is_rejected(changes):
    with pytest.raises(ValidationError):
        make_finding(**changes)


def test_newlines_are_allowed_in_text():
    assert make_finding(description="First paragraph is long enough to count.\n\nSecond one.")


def test_a_file_must_agree_with_the_totals_its_report_states():
    findings = [make_finding(), make_finding(id="test-tob-2024-01:TOB-T-2", finding_id="TOB-T-2")]
    assert make_file(findings, stated_counts={"high": 2})
    with pytest.raises(ValidationError, match="report states 1, parsed 2"):
        make_file(findings, stated_counts={"high": 1})
    with pytest.raises(ValidationError, match="table rows"):
        make_file(findings, table_rows=3)
    with pytest.raises(ValidationError, match="duplicate"):
        make_file([findings[0], findings[0]])
    with pytest.raises(ValidationError, match="another report"):
        make_file([make_finding(report="other", id="other:TOB-T-1")])


def test_excluded_findings_account_for_the_rest_of_the_table():
    kept = make_finding()
    gone = Exclusion(
        finding_id="TOB-T-2", severity="low", title="A minor note", reason="below_severity"
    )
    file = make_file([kept], excluded=(gone,), table_rows=2, stated_counts={"high": 1, "low": 1})
    assert len(file.findings) == 1 and len(file.excluded) == 1
    with pytest.raises(ValidationError, match="low: report states 2, parsed 1"):
        make_file([kept], excluded=(gone,), table_rows=2, stated_counts={"high": 1, "low": 2})
    with pytest.raises(ValidationError, match="table rows"):
        make_file([kept], excluded=(gone,), table_rows=1)
    twin = gone.model_copy(update={"finding_id": kept.finding_id})
    with pytest.raises(ValidationError, match="duplicate"):
        make_file([kept], excluded=(twin,), table_rows=2)


def test_an_exclusion_gives_a_reason_only_when_the_exploit_review_made_it():
    base = {"finding_id": "TOB-T-2", "severity": "high", "title": "A reviewed note"}
    assert Exclusion(**base, reason="not_an_exploit", why="Process best practice, no attacker.")
    with pytest.raises(ValidationError, match="reason is given"):
        Exclusion(**base, reason="not_an_exploit")
    with pytest.raises(ValidationError, match="reason is given"):
        Exclusion(**base, reason="below_severity", why="because")


def test_a_file_round_trips_through_json(tmp_path):
    file = make_file([make_finding(taxonomy=("reentrancy",))])
    save_findings(file, tmp_path)
    assert load_findings(tmp_path) == [file]


def test_a_bad_file_raises_instead_of_being_skipped(tmp_path):
    (tmp_path / "bad.json").write_text('{"report": "x"}')
    with pytest.raises(ValidationError):
        load_findings(tmp_path)


# --- the committed data ----------------------------------------------------------------------


def test_every_report_in_the_manifest_has_findings_and_the_other_way_round():
    manifest = {r.slug: r for r in load_manifest().reports}
    files = {f.report: f for f in load_findings()}
    assert set(files) == set(manifest)
    for slug, file in files.items():
        assert file.source.file == manifest[slug].file
        assert file.source.sha256 == manifest[slug].sha256
        assert all(f.date == manifest[slug].date for f in file.findings)


def test_committed_findings_agree_with_the_totals_each_report_states():
    # Loading already enforces it (see FindingsFile); this pins that it is not vacuous.
    files = load_findings()
    assert sum(bool(f.stated_counts) for f in files) >= 10
    for file in files:
        assert file.table_rows == len(file.findings) + len(file.excluded) > 0


def test_the_corpus_has_unique_ids_and_only_the_severe():
    findings = all_findings()
    assert len(findings) >= 10
    ids = [f.id for f in findings]
    assert len(set(ids)) == len(ids)
    assert {f.severity for f in findings} <= {"critical", "high", "medium", "undetermined"}


def test_committed_text_is_clean():
    debris = re.compile(r"Trail of Bits\s+\d+|© \d{4} Trail of Bits|Assessment\s*\|\s*\d+")
    for f in all_findings():
        text = f"{f.title}\n{f.description}\n{f.recommendation}"
        assert "Exploit Scenario" not in text
        assert not debris.search(text), f.id
        assert f.page_end - f.page < 10
        assert f.recommendation.strip() and f.description.strip()


def test_every_tag_is_a_real_taxonomy_entry():
    entries = set(load_taxonomy())
    for f in all_findings():
        assert set(f.taxonomy) <= entries
