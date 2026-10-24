"""Completeness check: did the parser get every finding of the report, intact?

The report is checked against itself, with three independent counts that must agree:
  1. the rows of its findings table (each row lists a finding and its severity),
  2. the findings the parser read from the detailed section,
  3. the per-severity totals the report states near the front, when it states them.
A parser that drops or invents a finding, or misreads a severity, fails here with the IDs involved.
It also looks for debris that would make a record unclean: page footers, leftover decode failures,
dropped-section headings, and fields that came out empty.
"""

import re
from dataclasses import dataclass, field

from knowledge.models import SEVERITIES
from knowledge.parse.tob import CODE_MARKER, ParsedReport

_DEBRIS = (
    (re.compile(r"Trail of Bits\s+\d+|Assessment\s*\|\s*\d+|^PUBLIC$", re.M), "page footer text"),
    (re.compile(r"^(Exploit Scenarios?|References?)$", re.M), "a dropped section heading"),
    (re.compile(r"�"), "an undecoded character"),
)


@dataclass(slots=True)
class CheckResult:
    problems: list[str] = field(default_factory=list)  # any of these means: do not use
    notes: list[str] = field(default_factory=list)  # worth knowing, not wrong

    @property
    def ok(self) -> bool:
        return not self.problems


def check_parsed(parsed: ParsedReport, unrepaired_characters: int = 0) -> CheckResult:
    result = CheckResult(notes=list(parsed.notes))
    findings = parsed.findings
    if not findings:
        result.problems.append("no findings were read")
        return result

    # A report that follows an earlier assessment may continue its numbering (24, 25, ...), so the
    # numbers must run on without a gap or repeat, not start at 1.
    numbers = [f.number for f in findings]
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        result.problems.append(f"finding numbers have a gap or repeat: {numbers}")

    by_number = {str(f.number): f for f in findings}
    by_id = {f.finding_id: f for f in findings if f.finding_id}
    rows = {row.key: row.severity for row in parsed.table_rows}
    if len(rows) != len(parsed.table_rows):
        result.problems.append("the findings table lists a key twice")
    if len(rows) != len(findings):
        result.problems.append(f"table has {len(rows)} rows, parser read {len(findings)} findings")
    for key, severity in rows.items():
        finding = by_number.get(key) or by_id.get(key)
        if finding is None:
            result.problems.append(f"table row {key} has no finding")
        elif finding.severity != severity:
            result.problems.append(
                f"finding {key}: table says {severity}, detail says {finding.severity or 'nothing'}"
            )

    if parsed.exposure:
        for severity in SEVERITIES:
            parsed_count = sum(f.severity == severity for f in findings)
            stated = parsed.exposure.get(severity, 0)
            if parsed_count != stated:
                result.problems.append(
                    f"{severity}: report states {stated}, parser read {parsed_count}"
                )
    else:
        result.notes.append("the report states no per-severity totals; only the table was checked")

    if unrepaired_characters:
        result.problems.append(f"{unrepaired_characters} characters could not be decoded")

    for f in findings:
        label = f.finding_id or f"#{f.number}"
        if not f.finding_id:
            result.notes.append(f"{label}: no Finding ID printed; one is made from the number")
        if not f.severity:
            result.problems.append(f"{label}: no severity")
        if len(f.title) < 3:
            result.problems.append(f"{label}: no title")
        prose = f.description.replace(CODE_MARKER, "").strip()
        if len(prose) < 40:
            result.problems.append(f"{label}: description is empty or only code")
        if not f.recommendation.strip():
            result.problems.append(f"{label}: no recommendation")
        for text in (f.description, f.recommendation, f.title):
            for pattern, what in _DEBRIS:
                if pattern.search(text):
                    result.problems.append(f"{label}: contains {what}")
    return result
