"""PDF to lines of text, with the page each line is on and whether it is code.

pypdf's own text output loses spaces in these reports, so this works from the individual text
runs instead: each run carries its position on the page and its font. Runs on the same baseline
become one line; a run in a monospaced font marks the line as code (a listing, not prose).

The PDF is untrusted input. Nothing here follows links or executes anything; every string leaves
this module as plain text with control characters removed.
"""

import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from pypdf import PdfReader

BASELINE_TOLERANCE = 2.5  # points: runs this close vertically are on the same line
MONOSPACE_FONT = re.compile(r"mono|consolas|courier|inconsolata|menlo", re.I)
BOLD_FONT = re.compile(r"bold|semibold|medium", re.I)
_CONTROL = re.compile(r"[\x01-\x08\x0b-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿]")
_LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
# Some fonts have no Unicode mapping for a ligature glyph and the PDF yields NUL ("Insu\x00cient").
# NUL becomes U+FFFD here and is repaired later from the document's own vocabulary.
UNKNOWN = "\ufffd"
_LIGATURE_CANDIDATES = ("ffi", "ffl", "ff", "fi", "fl", "f", "ft", "tt", "ti")
_LETTER = r"(?:[^\W\d_]|�)+"
_WORD = re.compile(_LETTER + r"(?:[’']" + _LETTER + r")*")
# Inline code in prose arrives as separate words: "( MAX_LP_FEE )" becomes "(MAX_LP_FEE)".
_SPACE_INSIDE_BRACKETS = re.compile(r"(?<=[(\[]) | (?=[)\]])")
_SPACE_BEFORE_PUNCTUATION = re.compile(r" (?=[,.;:!?])")
MAX_PAGES = 1000


@dataclass(frozen=True, slots=True)
class Line:
    page: int  # 1-based physical page of the PDF
    y: float  # distance from the bottom of the page, in points
    x: float  # left edge of the line
    text: str
    code: bool  # every word is in a monospaced font
    bold: bool  # every word is in a bold-ish font
    size: float  # font size of the first word


@dataclass(frozen=True, slots=True)
class _Run:
    text: str
    x: float
    y: float
    font: str
    size: float


def clean_text(text: str) -> str:
    """Plain, single-line text: ligatures expanded, odd characters removed, spaces collapsed."""
    text = unicodedata.normalize("NFKC", text.replace("\x00", UNKNOWN).translate(_LIGATURES))
    text = " ".join(_CONTROL.sub("", text).split())
    return _SPACE_BEFORE_PUNCTUATION.sub("", _SPACE_INSIDE_BRACKETS.sub("", text))


def _runs(page) -> list[_Run]:
    runs: list[_Run] = []

    def visit(text, cm, tm, font_dict, font_size):
        if not text or not text.strip():
            return
        # Position of the run's origin in page space: text matrix applied through the CTM.
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        font = str((font_dict or {}).get("/BaseFont", "")).split("+")[-1]
        size = abs(float(font_size or 0) * float(cm[0]))
        runs.append(_Run(text, x, y, font, size))

    page.extract_text(visitor_text=visit)
    return runs


def _lines_of_page(page_number: int, page) -> list[Line]:
    runs = _runs(page)
    rows: list[list[_Run]] = []
    for run in sorted(runs, key=lambda r: (-r.y, r.x)):
        if rows and abs(rows[-1][0].y - run.y) <= BASELINE_TOLERANCE:
            rows[-1].append(run)
        else:
            rows.append([run])
    lines = []
    for row in rows:
        row.sort(key=lambda r: r.x)
        text = clean_text(" ".join(r.text for r in row))
        if not text:
            continue
        lines.append(
            Line(
                page=page_number,
                y=row[0].y,
                x=row[0].x,
                text=text,
                code=all(MONOSPACE_FONT.search(r.font) for r in row),
                bold=all(BOLD_FONT.search(r.font) for r in row),
                size=row[0].size,
            )
        )
    return lines


def read_lines(path: Path) -> list[Line]:
    """Every line of the PDF, page by page, top to bottom."""
    reader = PdfReader(path)
    if len(reader.pages) > MAX_PAGES:
        raise ValueError(f"{path.name}: more than {MAX_PAGES} pages")
    lines: list[Line] = []
    for number, page in enumerate(reader.pages, start=1):
        lines.extend(_lines_of_page(number, page))
    return lines


def repair_unknown_characters(lines: list[Line]) -> tuple[list[Line], int]:
    """Put back ligatures the PDF could not map (see UNKNOWN), using words seen elsewhere.

    A word with a gap is completed with whichever of ffi, ffl, ff, fi, fl makes a word that occurs
    in the document with no gap. Returns the lines and how many gaps could not be repaired (those
    are removed from the text).
    """
    known = {
        w.lower() for ln in lines for w in _WORD.findall(ln.text) if UNKNOWN not in w and len(w) > 2
    }
    unrepaired = 0

    def fix(match: re.Match[str]) -> str:
        nonlocal unrepaired
        word = match.group(0)
        if UNKNOWN not in word:
            return word
        for candidate in _LIGATURE_CANDIDATES:
            if word.replace(UNKNOWN, candidate).lower() in known:
                return word.replace(UNKNOWN, candidate)
        unrepaired += word.count(UNKNOWN)
        return word.replace(UNKNOWN, "")

    repaired = []
    for ln in lines:
        if UNKNOWN in ln.text:
            ln = replace(ln, text=_WORD.sub(fix, ln.text))
        repaired.append(ln)
    return repaired, unrepaired
