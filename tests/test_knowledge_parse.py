"""The PDF text layer, the Trail of Bits parser and the completeness check, on made-up pages."""

import dataclasses

import pytest

from knowledge.parse.check import check_parsed
from knowledge.parse.pdf_text import (
    UNKNOWN,
    Line,
    clean_text,
    read_lines,
    repair_unknown_characters,
)
from knowledge.parse.tob import CODE_MARKER, ParseError, body_lines, parse_report
from tests.knowledge_support import (
    BOLD,
    COURIER,
    HELVETICA,
    Page,
    finding_block,
    make_pdf,
    newer_template_report,
)

# --- reading a PDF ---------------------------------------------------------------------------


def test_lines_are_rebuilt_from_positions_with_font_flags(tmp_path):
    pdf = make_pdf(
        [
            [
                (72, 700, BOLD, 18, "Detailed Findings"),
                (72, 660, HELVETICA, 11, "Hello"),
                (110, 660, HELVETICA, 11, "world"),
                (72, 640, COURIER, 9, "x = y + 1;"),
            ],
            [(72, 700, HELVETICA, 11, "second page")],
        ]
    )
    path = tmp_path / "t.pdf"
    path.write_bytes(pdf)
    lines = read_lines(path)
    assert [(ln.page, ln.text) for ln in lines] == [
        (1, "Detailed Findings"),
        (1, "Hello world"),
        (1, "x = y + 1;"),
        (2, "second page"),
    ]
    heading, prose, code, _ = lines
    assert heading.bold and not heading.code and heading.size == 18
    assert not prose.bold and not prose.code
    assert code.code and not code.bold
    assert heading.y > prose.y > code.y


def test_a_pdf_with_too_many_pages_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr("knowledge.parse.pdf_text.MAX_PAGES", 1)
    path = tmp_path / "t.pdf"
    path.write_bytes(make_pdf([[(72, 700, HELVETICA, 11, "a")], [(72, 700, HELVETICA, 11, "b")]]))
    with pytest.raises(ValueError, match="more than"):
        read_lines(path)


def test_clean_text_expands_ligatures_and_removes_hidden_characters():
    assert clean_text("oﬃce ﬁx") == "office fix"
    assert clean_text("a​b‮c\x07d") == "abcd"  # zero-width, bidi override, control
    assert clean_text("( MAX_FEE ) , then  ok .") == "(MAX_FEE), then ok."
    assert clean_text("Insu\x00cient") == f"Insu{UNKNOWN}cient"  # kept, to be repaired


def _line(text):
    return Line(1, 500.0, 72.0, text, False, False, 11.0)


def test_ligature_gaps_are_repaired_from_words_seen_elsewhere():
    lines = [
        _line("An insufficient measure"),
        _line(f"Insu{UNKNOWN}cient"),
        _line("differential testing"),
        _line(f"Di{UNKNOWN}erential"),
        _line(f"Zq{UNKNOWN}x unknown word"),
    ]
    repaired, unrepaired = repair_unknown_characters(lines)
    assert [ln.text for ln in repaired][1] == "Insufficient"
    assert [ln.text for ln in repaired][3] == "Differential"
    assert repaired[4].text == "Zqx unknown word"  # nothing matches: gap removed and counted
    assert unrepaired == 1


# --- the parser ------------------------------------------------------------------------------


def test_a_report_is_read_into_findings():
    parsed = parse_report(newer_template_report(("High", "Low")))
    assert parsed.cover_title == "Test Protocol"
    assert parsed.cover_date == "2024-09"
    assert [r.severity for r in parsed.table_rows] == ["high", "low"]
    assert parsed.exposure == {
        "high": 1,
        "medium": 0,
        "low": 1,
        "informational": 0,
        "undetermined": 0,
    }
    first, second = parsed.findings
    assert (first.number, first.title, first.severity) == (1, "Missing check number 1", "high")
    assert first.finding_id == "TOB-TEST-1" and first.category == "Data Validation"
    assert first.difficulty == "Low" and first.target == "contracts/Pool.sol"
    assert second.finding_id == "TOB-TEST-2"
    assert first.page == 4 and second.page == 5


def test_description_keeps_prose_marks_code_and_drops_exploit_scenarios():
    first = parse_report(newer_template_report(("High",))).findings[0]
    paragraphs = first.description.split("\n\n")
    assert paragraphs[0].startswith("The function in") and "second sentence" in paragraphs[0]
    assert CODE_MARKER in paragraphs
    assert "function f(" not in first.description  # code listing text is gone
    assert any(p.startswith("Figure 1.1:") for p in paragraphs)
    assert "Alice" not in first.description and "Alice" not in first.recommendation
    assert "Exploit Scenario" not in first.description
    assert first.recommendation == "Short term, add a check on the argument. Long term, add tests."
    assert "Trail of Bits" not in first.description  # page footer removed


def test_footers_are_dropped_and_unusual_footer_text_is_reported():
    lines = newer_template_report(("High",))
    odd = Line(2, 30.0, 72.0, "Something that is not a footer", False, False, 9.0)
    kept, notes = body_lines([*lines, odd])
    assert all(ln.y >= 65 for ln in kept)
    assert any("Something that is not a footer" in n for n in notes)
    assert not any("PUBLIC" in n for n in notes)  # repeats on every page: a footer


def test_a_summary_report_without_detailed_findings_is_rejected():
    page = Page(1)
    page.add("Summary of Findings", size=18, bold=True)
    page.add("ID Title Type Severity", size=10, bold=True)
    page.add("1 Something Data Validation Low", size=10)
    with pytest.raises(ParseError, match="summary report"):
        parse_report(page.lines)


def test_the_older_template_with_unbold_headings_and_total_lines_is_read():
    cover = Page(1)
    cover.add("Old Protocol", size=38, bold=True, gap=0)
    cover.add("December 13, 2021")
    dashboard = Page(2)
    dashboard.add("Vulnerability Summary", bold=True)
    dashboard.add("Total High-Severity Issues 1 ##")
    dashboard.add("Total Informational Issues 1 #")
    dashboard.add("Total Issues of Undetermined Severity 0")
    table = Page(3)
    table.add("Findings Summary", size=20)  # not bold, 20 points
    table.add("# Title Type Severity", bold=True)
    table.add("1 First thing Data Validation High")
    table.add("2 Second thing Data Validation Informational")
    one = Page(4)
    one.add("1. First thing", size=16, gap=30)  # title: 16 points, not bold
    one.add("Severity: High Difficulty: Low")
    one.add("Type: Data Validation Finding ID: TOB-OLD-001")
    one.add("Target: a.sol")
    one.add("Description", bold=True, gap=30)
    one.add("The first description is long enough to be a description of a problem.")
    one.add("Recommendations", bold=True, gap=30)
    one.add("Short term, fix it properly.")
    two = Page(5)
    two.add("2. Second thing", size=16, gap=30)
    two.add("Severity: Informational Difficulty: Low")
    two.add("Type: Data Validation Finding ID: TOB-OLD-002")
    two.add("Target: b.sol")
    two.add("Description", bold=True, gap=30)
    two.add("The second description is long enough to be a description too.")
    two.add("Recommendations", bold=True, gap=30)
    two.add("Short term, fix it too.")
    appendix = Page(6)
    appendix.add("A. Vulnerability Classifications", size=20)
    lines = [ln for p in (cover, dashboard, table, one, two, appendix) for ln in p.lines]
    parsed = parse_report(lines)
    assert [f.finding_id for f in parsed.findings] == ["TOB-OLD-001", "TOB-OLD-002"]
    assert parsed.exposure == {"high": 1, "informational": 1, "undetermined": 0}
    assert [r.severity for r in parsed.table_rows] == ["high", "informational"]
    assert check_parsed(parsed).ok


def test_numbering_may_continue_from_an_earlier_assessment():
    page = Page(4)
    page.add("Detailed Findings", size=18, bold=True)
    for number in (24, 25):
        finding_block(page, number, f"Thing {number}", "Low")
    summary = Page(3)
    summary.add("Summary of Findings", size=18, bold=True)
    summary.add("ID Title Type Severity", size=10, bold=True)
    summary.add("24 Thing 24 Data Validation Low", size=10)
    summary.add("25 Thing 25 Data Validation Low", size=10)
    parsed = parse_report([*summary.lines, *page.lines])
    assert [f.number for f in parsed.findings] == [24, 25]
    assert check_parsed(parsed).ok


# --- the completeness check ------------------------------------------------------------------


def test_a_consistent_report_passes():
    assert check_parsed(parse_report(newer_template_report(("High", "Low")))).ok


def test_a_finding_missing_from_the_detailed_section_is_caught():
    lines = newer_template_report(("High", "Low"), table_severities=("High", "Low", "Low"))
    result = check_parsed(parse_report(lines))
    assert not result.ok
    assert any("table has 3 rows, parser read 2" in p for p in result.problems)
    assert any("table row 3 has no finding" in p for p in result.problems)


def test_a_severity_that_disagrees_with_the_table_is_caught():
    lines = newer_template_report(("High", "Low"), table_severities=("High", "High"))
    problems = check_parsed(parse_report(lines)).problems
    assert any("finding 2: table says high, detail says low" in p for p in problems)


def test_totals_the_report_states_must_match_what_was_read():
    lines = newer_template_report(("High", "Low"), exposure={"high": 2, "low": 1})
    problems = check_parsed(parse_report(lines)).problems
    assert "high: report states 2, parser read 1" in problems


def test_a_report_with_no_stated_totals_is_checked_against_its_table_only():
    parsed = parse_report(newer_template_report(("High",)))
    parsed.exposure.clear()
    result = check_parsed(parsed)
    assert result.ok
    assert any("states no per-severity totals" in n for n in result.notes)


def test_debris_and_empty_fields_are_caught():
    parsed = parse_report(newer_template_report(("High",)))
    finding = parsed.findings[0]
    finding.description += "\n\nTrail of Bits 30 Uniswap v4 Core Security Assessment"
    finding.recommendation = ""
    problems = check_parsed(parsed, unrepaired_characters=2).problems
    assert any("page footer text" in p for p in problems)
    assert any("no recommendation" in p for p in problems)
    assert "2 characters could not be decoded" in problems


def test_a_numbering_gap_is_caught():
    parsed = parse_report(newer_template_report(("High", "Low", "Low")))
    parsed.findings[2] = dataclasses.replace(parsed.findings[2], number=5)
    assert any("gap or repeat" in p for p in check_parsed(parsed).problems)
