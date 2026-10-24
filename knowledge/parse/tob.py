"""Parser for Trail of Bits security reviews: lines of text in, findings out.

A review has the same skeleton every time, so this is a rule-based parser (no model):

  * a findings table ("Summary of Findings", or "Findings Summary" in the older template), one
    row per finding with its severity;
  * the detailed findings: for each a numbered title, a block of labels (Severity, Difficulty,
    Type, Finding ID, Target), then bold headings Description, Exploit Scenario, Recommendations,
    References;
  * the number of findings per severity, in an exposure table (newer template) or a
    "Vulnerability Summary" (older one).

The two templates differ in type sizes and wording, so headings are found by size and text, not
by one fixed layout. A "summary report" (findings table but no detailed findings) is rejected.

What is kept: title, labels, the Description and Recommendations text. What is dropped: Exploit
Scenario and References sections, code listings (a marker is left), page footers. The parser
reports what it saw (table rows, exposure counts); `check.py` decides whether it is complete.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from knowledge.parse.pdf_text import Line

SEVERITY_WORDS = ("Critical", "High", "Medium", "Low", "Informational", "Undetermined")
_SEVERITY = "|".join(SEVERITY_WORDS)
FOOTER_BELOW_Y = 65.0  # page footers sit in the bottom margin; no body text goes this low
BIG_HEADING_SIZE = 16.0  # section headings (newer template 18, older 20)
TITLE_SIZE = 11.5  # finding titles are larger than the 11-point body text (12 or 16)
RUNNING_TEXT_MIN_PAGES = 3  # text that repeats on this many pages is a footer, not content
PARAGRAPH_GAP = 22.0  # points between baselines; ordinary line spacing is about 15

CODE_MARKER = "[code listing omitted]"

_TITLE = re.compile(r"^(\d+)\.\s+(\S.*)$")
_LABEL = re.compile(r"(Severity|Difficulty|Type|Finding ID|Target):")
_TABLE_ROW = re.compile(r"^(\d+|TOB-[A-Z0-9]+-\d+)\s+\S")
_EXPOSURE = re.compile(rf"^({_SEVERITY})\s+(\d+)\b")  # newer template
# Older template: "Total High-Severity Issues 5", "Total Issues of Undetermined Severity 1".
_TOTAL = re.compile(rf"^Total (?:Issues of )?({_SEVERITY})(?:[- ]Severity)?(?: Issues)?\s+(\d+)\b")
_SUMMARY_HEADINGS = ("Summary of Findings", "Findings Summary")
_APPENDIX = re.compile(r"^[A-Z]\.\s+\S")
_DATE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+(\d{4})$"
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}
_DESCRIPTION, _RECOMMENDATION, _DROPPED = "description", "recommendation", "dropped"
_SECTION_NAMES = {
    "description": _DESCRIPTION,
    "recommendations": _RECOMMENDATION,
    "recommendation": _RECOMMENDATION,
    "exploit scenario": _DROPPED,
    "exploit scenarios": _DROPPED,
    "references": _DROPPED,
    "reference": _DROPPED,
}


class ParseError(Exception):
    """The report does not have the shape this parser knows. Safe to print."""


@dataclass(frozen=True, slots=True)
class TableRow:
    key: str  # the number or Finding ID printed in the first column
    severity: str  # lowercase


@dataclass(slots=True)
class RawFinding:
    number: int
    title: str
    severity: str
    difficulty: str | None
    category: str | None
    finding_id: str | None
    target: str | None
    description: str
    recommendation: str
    page: int
    page_end: int


@dataclass(slots=True)
class ParsedReport:
    cover_title: str | None
    cover_date: str | None  # YYYY-MM
    findings: list[RawFinding]
    table_rows: list[TableRow]
    exposure: dict[str, int]  # lowercase severity -> count, from the exposure table
    notes: list[str] = field(default_factory=list)  # things the parser could not make sense of


def body_lines(lines: list[Line]) -> tuple[list[Line], list[str]]:
    """Without page footers. A footer is whatever sits in the bottom margin; text there that does
    not repeat from page to page is reported, in case it was real content."""
    pages_seen: dict[str, set[int]] = {}
    for ln in lines:
        if ln.y < FOOTER_BELOW_Y:
            pages_seen.setdefault(re.sub(r"\d+", "#", ln.text), set()).add(ln.page)
    kept, odd = [], []
    for ln in lines:
        if ln.y >= FOOTER_BELOW_Y:
            kept.append(ln)
        elif ln.page > 1 and len(pages_seen[re.sub(r"\d+", "#", ln.text)]) < RUNNING_TEXT_MIN_PAGES:
            odd.append(f"page {ln.page}: dropped from the footer area: {ln.text[:60]!r}")
    return kept, odd


def _headings(lines: list[Line], texts: tuple[str, ...]) -> list[int]:
    return [i for i, ln in enumerate(lines) if ln.size >= BIG_HEADING_SIZE and ln.text in texts]


def read_cover(lines: list[Line]) -> tuple[str | None, str | None]:
    first = [ln for ln in lines if ln.page == 1]
    title = " ".join(ln.text for ln in first if ln.size >= 24) or None
    date = None
    for ln in first:
        match = _DATE.match(ln.text)
        if match:
            date = f"{match.group(2)}-{_MONTHS[match.group(1)]:02d}"
            break
    return title, date


def read_exposure(lines: list[Line]) -> dict[str, int]:
    """Findings per severity as the report itself states them."""
    counts: dict[str, int] = {}
    for i, ln in enumerate(lines):
        if "EXPOSURE ANALYSIS" in ln.text:
            for later in lines[i + 1 : i + 12]:
                match = _EXPOSURE.match(later.text)
                if match:
                    counts[match.group(1).lower()] = int(match.group(2))
            return counts
    for ln in lines:
        match = _TOTAL.match(ln.text)
        if match:
            counts[match.group(1).lower()] = int(match.group(2))
    return counts


def read_summary_table(lines: list[Line], start: int, end: int) -> list[TableRow]:
    """Rows of the Summary of Findings table, between its heading and the next heading."""
    header = next(
        (
            i
            for i in range(start, end)
            if lines[i].text.startswith(("ID Title", "# Title")) and lines[i].bold
        ),
        None,
    )
    if header is None:
        return []
    groups: list[list[Line]] = []
    for ln in lines[header + 1 : end]:
        if _TABLE_ROW.match(ln.text):
            groups.append([ln])
        elif groups:
            groups[-1].append(ln)
    rows = []
    for group in groups:
        key = group[0].text.split()[0]
        severity = next(
            (
                ln.text.split()[-1]
                for ln in group
                if ln.text.split()[-1].capitalize() in SEVERITY_WORDS
            ),
            None,
        )
        if severity:
            rows.append(TableRow(key, severity.lower()))
    return rows


def _paragraphs(lines: list[Line]) -> str:
    """Prose lines joined into paragraphs; each code listing becomes one marker."""
    paragraphs: list[str] = []
    previous: Line | None = None
    in_code = False
    for ln in lines:
        if ln.code:
            if not in_code:
                paragraphs.append(CODE_MARKER)
            in_code = True
            previous = ln
            continue
        starts_bullet = ln.text.startswith(("●", "•"))
        new_paragraph = (
            previous is None
            or in_code
            or starts_bullet
            or (previous.page == ln.page and previous.y - ln.y > PARAGRAPH_GAP)
        )
        text = ln.text.removeprefix("●").removeprefix("•").strip()
        if starts_bullet:
            text = f"- {text}"
        if new_paragraph or not paragraphs:
            paragraphs.append(text)
        else:
            paragraphs[-1] = f"{paragraphs[-1]} {text}"
        in_code = False
        previous = ln
    return "\n\n".join(p for p in paragraphs if p)


def _labels(text: str) -> dict[str, str]:
    parts = _LABEL.split(text)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def _finding_starts(lines: list[Line], start: int, end: int) -> list[tuple[int, int]]:
    """(index of the first title line, index of the `Severity:` line) for each finding."""
    found = []
    for a in range(start, end):
        if not lines[a].text.startswith("Severity:"):
            continue
        j = a - 1
        while j > start and lines[j].size >= TITLE_SIZE and not _TITLE.match(lines[j].text):
            j -= 1
        if _TITLE.match(lines[j].text) and lines[j].size >= TITLE_SIZE:
            found.append((j, a))
    return found


def _read_finding(lines: list[Line], first: int, anchor: int, stop: int) -> RawFinding:
    title_lines = lines[first:anchor]
    match = _TITLE.match(title_lines[0].text)
    assert match is not None
    title = " ".join([match.group(2)] + [ln.text for ln in title_lines[1:]])

    cursor = anchor
    meta_parts = []
    while cursor < stop and not (
        lines[cursor].bold and lines[cursor].text.lower() in _SECTION_NAMES
    ):
        meta_parts.append(lines[cursor].text)
        cursor += 1
    labels = _labels(" ".join(meta_parts))

    sections: dict[str, list[Line]] = {_DESCRIPTION: [], _RECOMMENDATION: [], _DROPPED: []}
    current = _DROPPED
    for ln in lines[cursor:stop]:
        name = _SECTION_NAMES.get(ln.text.lower()) if ln.bold else None
        if name:
            current = name
            continue
        sections[current].append(ln)

    severity = labels.get("Severity", "").split(" ")[0].capitalize()
    return RawFinding(
        number=int(match.group(1)),
        title=title,
        severity=severity.lower() if severity in SEVERITY_WORDS else "",
        difficulty=labels.get("Difficulty") or None,
        category=labels.get("Type") or None,
        finding_id=labels.get("Finding ID") or None,
        target=labels.get("Target") or None,
        description=_paragraphs(sections[_DESCRIPTION]),
        recommendation=_paragraphs(sections[_RECOMMENDATION]),
        page=title_lines[0].page,
        page_end=lines[stop - 1].page,
    )


def parse_report(all_lines: list[Line]) -> ParsedReport:
    lines, notes = body_lines(all_lines)
    title, date = read_cover(lines)

    first_anchor = next((i for i, ln in enumerate(lines) if ln.text.startswith("Severity:")), None)
    if first_anchor is None:
        raise ParseError("no detailed findings found (a summary report has only the table)")
    # The table of contents also lists the heading, but in a smaller font: only headings count.
    summary_candidates = [i for i in _headings(lines, _SUMMARY_HEADINGS) if i < first_anchor]
    if not summary_candidates:
        raise ParseError("no findings table heading found before the first finding")
    summary = summary_candidates[-1]
    detailed = summary + 1
    end = next(
        (
            i
            for i in range(first_anchor, len(lines))
            if lines[i].size >= BIG_HEADING_SIZE and _APPENDIX.match(lines[i].text)
        ),
        len(lines),
    )

    table_rows = read_summary_table(lines, summary, first_anchor)
    starts = _finding_starts(lines, detailed, end)
    findings = []
    for n, (first, anchor) in enumerate(starts):
        stop = starts[n + 1][0] if n + 1 < len(starts) else end
        findings.append(_read_finding(lines, first, anchor, stop))

    duplicates = [k for k, c in Counter(f.number for f in findings).items() if c > 1]
    if duplicates:
        notes.append(f"finding numbers seen twice: {duplicates}")
    return ParsedReport(
        cover_title=title,
        cover_date=date,
        findings=findings,
        table_rows=table_rows,
        exposure=read_exposure(lines),
        notes=notes,
    )
