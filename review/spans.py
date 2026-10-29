"""Checking what the reviewer said (layer L5) and turning it into `SourceSpan` records (R3).

The reviewer's reply is untrusted: it is a model's reading of text a stranger wrote. Each item is
checked on its own and a bad one is dropped and counted, never repaired by guessing:

* the file must be one shown in this call, and every line of the span must have been shown;
* the quote must be text of the lines as the reviewer saw them (so it cannot invent code);
* the entry must be in the checklist the reviewer was given, the clause an index of that entry;
* the function is not taken from the model at all: it is the function the parser says contains the
  lines, and it is shown only if it is a plain identifier of at most 40 characters that does not
  read as an instruction to a reader.

What survives is a closed set of fields plus the reviewer's one-line note (cleaned and cut to 200
characters), which is stored and shown on screen as text and never given to the writer.
"""

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ValidationError

from review.known import Chunk
from review.lexer import Lexed, Member
from review.pack import Checklist
from review.scan import aimed_at_reader, split_words
from review.types import (
    MAX_NOTE_CHARS,
    MAX_REVIEWER_ITEMS,
    SEVERITY_RANK,
    Bundle,
    Explanation,
    FileRef,
    LineRange,
    Match,
    PrecedentRef,
    ReviewerInfo,
    ReviewerItem,
    Severity,
    SourceSpan,
    Subject,
    span_sha256,
)

DropReason = Literal[
    "bad_item",
    "unknown_file",
    "lines_not_shown",
    "quote_not_in_code",
    "unknown_entry",
    "duplicate",
    "too_many",
]
Adjustment = Literal["clause_out_of_range", "function_replaced", "function_not_shown"]

# Severity a reviewer item may carry on its own. High needs a blind second pass (decision 11).
SELF_CAP: Severity = "medium"

_FUNCTION_LIKE = frozenset({"function", "modifier", "constructor", "receive", "fallback"})
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,39}$")
_SPACE = re.compile(r"\s+")


class Accepted(BaseModel, frozen=True):
    """One reviewer item that passed. `function` comes from the parser, not from the model."""

    file_id: str
    start_line: int
    end_line: int
    function: str | None
    entry: str
    clause: int | None
    severity_claimed: Severity
    confidence: Literal["low", "medium", "high"]
    note: str
    span_sha256: str
    call_index: int


@dataclass
class Checked:
    accepted: list[Accepted] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)
    adjusted: Counter = field(default_factory=Counter)
    unreadable: str | None = None  # why the whole reply could not be used, in fixed words


@dataclass(frozen=True)
class CallContext:
    """What was shown in one call, and what the parser knows about it."""

    call_index: int
    shown: Mapping[str, Mapping[int, str]]  # file id -> line number -> the text the model saw
    stripped: Mapping[
        str, Sequence[str]
    ]  # file id -> the file's stripped lines, as the lexer made them
    members: Mapping[str, Sequence[Member]]  # file id -> every function-like member

    @classmethod
    def for_chunk(
        cls, chunk: Chunk, lexed: Mapping[str, Lexed], *, shown: Chunk | None = None
    ) -> "CallContext":
        """`shown` is the chunk as the model saw it, when that differs from the real one (the
        confirmation pass renames identifiers); line numbers are the same in both."""
        seen: dict[str, dict[int, str]] = {}
        for piece in (shown or chunk).pieces:
            lines = seen.setdefault(piece.file_id, {})
            for offset, text in enumerate(piece.text.split("\n")):
                lines[piece.start_line + offset] = text
        stripped: dict[str, Sequence[str]] = {}
        members: dict[str, list[Member]] = {}
        for file_id in seen:
            result = lexed[file_id]
            stripped[file_id] = result.view.text.split("\n") if result.view else []
            members[file_id] = [
                m for d in result.definitions for m in d.members if m.kind in _FUNCTION_LIKE
            ]
        return cls(call_index=chunk.index, shown=seen, stripped=stripped, members=members)


# ---- the text fields ----------------------------------------------------------------------------


def clean_note(text: str) -> str:
    """The note with control, invisible and line-separating characters turned into spaces,
    whitespace collapsed and the length cut. It stays untrusted text: shown, never obeyed."""
    spaced = "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text
    )
    flat = _SPACE.sub(" ", spaced).strip()
    return flat if len(flat) <= MAX_NOTE_CHARS else flat[: MAX_NOTE_CHARS - 1].rstrip() + "…"


def squash(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def safe_function_name(name: str | None) -> str | None:
    """A function name fit to show: a plain identifier of at most 40 characters that does not read
    as an instruction once its camelCase is split (fixture H6)."""
    if not name or not _IDENTIFIER.match(name) or aimed_at_reader(split_words(name)):
        return None
    return name


def containing_function(members: Sequence[Member], start: int, end: int) -> str | None:
    """The innermost function-like member that holds all of lines `start`..`end`, if one does."""
    best: Member | None = None
    for m in members:
        if not (m.start_line <= start and end <= m.end_line):
            continue
        if best is None or (m.end_line - m.start_line) < (best.end_line - best.start_line):
            best = m
    return best.name if best else None


# ---- one reply ----------------------------------------------------------------------------------


def check_reply(text: str | None, ctx: CallContext, checklist: Checklist) -> Checked:
    """Parse the reply and check its items one by one. A reply that is not the expected JSON gives
    no items and says so; it never raises."""
    out = Checked()
    if text is None or not text.strip():
        out.unreadable = "the reviewer returned no text"
        return out
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        out.unreadable = "the reviewer's reply was not JSON"
        return out
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        out.unreadable = "the reviewer's reply had no list of items"
        return out
    seen: set[tuple[str, int, int, str]] = set()
    for raw in raw_items:
        if len(out.accepted) >= MAX_REVIEWER_ITEMS:
            out.dropped["too_many"] += 1
            continue
        item = _parse(raw)
        if item is None:
            out.dropped["bad_item"] += 1
            continue
        accepted = _check_item(item, ctx, checklist, out)
        if accepted is None:
            continue
        key = (accepted.file_id, accepted.start_line, accepted.end_line, accepted.entry)
        if key in seen:
            out.dropped["duplicate"] += 1
            continue
        seen.add(key)
        out.accepted.append(accepted)
    return out


def _parse(raw: object) -> ReviewerItem | None:
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("note"), str):  # a long note costs the note, not the finding
        raw = {**raw, "note": clean_note(raw["note"])}
    try:
        return ReviewerItem.model_validate(raw)
    except ValidationError:
        return None


def _check_item(
    item: ReviewerItem, ctx: CallContext, checklist: Checklist, out: Checked
) -> Accepted | None:
    shown = ctx.shown.get(item.file_id)
    if shown is None:
        out.dropped["unknown_file"] += 1
        return None
    wanted = range(item.start_line, item.end_line + 1)
    if any(n not in shown for n in wanted):
        out.dropped["lines_not_shown"] += 1
        return None
    quote = squash(item.quote)
    seen_text = squash("\n".join(shown[n] for n in wanted))
    if not quote or quote not in seen_text:
        out.dropped["quote_not_in_code"] += 1
        return None
    entry = checklist.entry(item.entry)
    if entry is None:
        out.dropped["unknown_entry"] += 1
        return None
    clause = item.clause
    if clause is not None and clause >= len(entry.clauses):
        out.adjusted["clause_out_of_range"] += 1
        clause = None
    function = safe_function_name(
        containing_function(ctx.members.get(item.file_id, ()), item.start_line, item.end_line)
    )
    if item.function is not None and item.function != function:
        out.adjusted["function_replaced" if function else "function_not_shown"] += 1
    lines = [
        ctx.stripped[item.file_id][n - 1] for n in wanted if n <= len(ctx.stripped[item.file_id])
    ]
    return Accepted(
        file_id=item.file_id,
        start_line=item.start_line,
        end_line=item.end_line,
        function=function,
        entry=item.entry,
        clause=clause,
        severity_claimed=item.severity,
        confidence=item.confidence,
        note=clean_note(item.note),
        span_sha256=span_sha256(lines),
        call_index=ctx.call_index,
    )


# ---- what the writer may see --------------------------------------------------------------------


def writer_fields(span: SourceSpan, checklist: Checklist) -> dict[str, object]:
    """The closed fields of an `S` record that the report writer is shown (layer L6): ids, file id,
    lines, enum values, a function name re-checked here, and our own taxonomy title and clause
    text. Never the path, the quote, the reviewer's note or anything else the model wrote."""
    out: dict[str, object] = {
        "id": span.id,
        "kind": span.kind,
        "file_id": span.file.file_id,
        "start_line": span.range.start_line,
        "end_line": span.range.end_line,
    }
    if span.kind == "scan" and span.scan:
        return {**out, "flag": span.scan.flag, "count": span.scan.count}
    if span.match is None:
        return out
    entry = checklist.entry(span.match.entry)
    clause = span.match.clause
    out.update(
        function=safe_function_name(span.range.function),
        entry=span.match.entry,
        entry_title=entry.title if entry else None,
        clause_text=entry.clauses[clause]
        if entry and clause is not None and clause < len(entry.clauses)
        else None,
        severity=span.match.severity,
        confidence=span.match.confidence,
        precedents=[
            {"finding_id": p.finding_id, "relation": p.relation, "closeness": p.closeness}
            for p in span.precedents
        ],
    )
    return out


# ---- the records --------------------------------------------------------------------------------


def subject_of(bundle: Bundle) -> Subject:
    return Subject(
        chain=bundle.chain,
        chain_id=bundle.chain_id,
        address=bundle.address,
        block=bundle.block,
        code_hash=bundle.code_hash,
        bundle_sha256=bundle.bundle_sha256,
    )


def capped(claimed: Severity, cap: Severity = SELF_CAP) -> Severity:
    """What a severity is after the cap: lowered if above it, never raised."""
    return claimed if SEVERITY_RANK[claimed] <= SEVERITY_RANK[cap] else cap


def to_span(
    accepted: Accepted,
    span_id: str,
    bundle: Bundle,
    reviewer: ReviewerInfo,
    *,
    precedents: Sequence[PrecedentRef] = (),
    severity: Severity | None = None,
    confirmed_by: str | None = None,
) -> SourceSpan:
    """The evidence record. Severity is the claimed one capped at medium, unless the caller passes
    a confirmed one (decision 11); `confirmed_by` is the id of the agreeing second pass."""
    file = next(f for f in bundle.files if f.file_id == accepted.file_id)
    return SourceSpan(
        id=span_id,
        kind="span",
        subject=subject_of(bundle),
        file=FileRef(file_id=file.file_id, path=file.path, sha256=file.sha256),
        range=LineRange(
            start_line=accepted.start_line,
            end_line=accepted.end_line,
            span_sha256=accepted.span_sha256,
            function=accepted.function,
        ),
        match=Match(
            entry=accepted.entry,
            clause=accepted.clause,
            severity=severity or capped(accepted.severity_claimed),
            confirmed_by=confirmed_by,
            severity_claimed=accepted.severity_claimed,
            confidence=accepted.confidence,
        ),
        precedents=list(precedents),
        explanation=Explanation(text=accepted.note),
        reviewer=reviewer,
    )
