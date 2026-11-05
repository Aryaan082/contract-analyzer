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
    MAX_LEADS,
    MAX_NOTE_CHARS,
    MAX_PATH_NOTE_CHARS,
    MAX_REVIEWER_ITEMS,
    SEVERITY_RANK,
    Bundle,
    Explanation,
    FileRef,
    LeadItem,
    LineRange,
    Match,
    PrecedentRef,
    ReviewerInfo,
    ReviewerItem,
    Severity,
    SourceSpan,
    Subject,
    VerdictItem,
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
    "unknown_function",  # a lead that names a function the parser does not find in the code shown
    "not_applicable",  # second pass: the lead was checked and does not apply (a decision)
    "unsure",  # second pass: the code shown cannot settle it (a decision; not flagged)
]
Adjustment = Literal["clause_out_of_range", "function_replaced", "function_not_shown"]

# Severity a reviewer item may carry on its own. High needs a blind second pass (decision 11).
SELF_CAP: Severity = "medium"

_FUNCTION_LIKE = frozenset({"function", "modifier", "constructor", "receive", "fallback"})
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,39}$")
_SPACE = re.compile(r"\s+")
# The whole reply is ONE Markdown code fence (```json or ```) around the JSON: the one wrapper a
# chat model puts round JSON it was asked for. The same rule as the plain arm's reader.
_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)

# Why a whole reply could not be used, in fixed words.
NO_TEXT = "the reviewer returned no text"
NOT_JSON = "the reviewer's reply was not JSON"
NO_ITEMS = "the reviewer's reply had no list of items"
CUT_OFF = "the reviewer's reply was cut off"
# Replies that may be asked for once more (`review.client.call_once`): the model answered, but not
# in the shape asked for. A cut-off reply is not one of them: asking again would be cut off again.
RETRYABLE = frozenset({NO_TEXT, NOT_JSON, NO_ITEMS})


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


class Lead(BaseModel, frozen=True):
    """A checked lead of the two-pass review: this function may be affected by this checklist
    entry. `reason` is the model's own words, cleaned; it is shown to the second pass as data."""

    file_id: str
    function: str
    entry: str
    reason: str


@dataclass
class Checked:
    accepted: list[Accepted] = field(default_factory=list)
    leads: list[Lead] = field(default_factory=list)  # first pass of the two-pass review only
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


def clean_note_to(text: str, limit: int) -> str:
    """Text with control, invisible and line-separating characters turned into spaces, whitespace
    collapsed and the length cut to `limit`. It stays untrusted text: shown, never obeyed."""
    spaced = "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text
    )
    flat = _SPACE.sub(" ", spaced).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def clean_note(text: str) -> str:
    """The reviewer's one-line note, cleaned and cut to `MAX_NOTE_CHARS`."""
    return clean_note_to(text, MAX_NOTE_CHARS)


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


def strip_fence(text: str) -> str:
    """The reply with surrounding whitespace removed and, if the whole reply is one Markdown code
    fence, the fence taken off. Anything else (words round the fence, two fences) is left as it is
    and will not parse."""
    body = text.strip()
    fenced = _FENCE.match(body)
    return fenced.group(1) if fenced else body


def check_reply(text: str | None, ctx: CallContext, checklist: Checklist) -> Checked:
    """Parse the reply and check its items one by one. A reply that is not the expected JSON gives
    no items and says so; it never raises. The reply must be the JSON object and nothing else,
    except that one Markdown code fence around it is taken off. JSON is never searched for in
    prose: text before or after it makes the reply unreadable."""
    out = Checked()
    raw_items = _list_of(text, "items", out)
    if raw_items is None:
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


def _list_of(text: str | None, key: str, out: Checked) -> list | None:
    """The list under `key` of the reply's JSON object, or None with `out.unreadable` set."""
    if text is None or not text.strip():
        out.unreadable = NO_TEXT
        return None
    try:
        data = json.loads(strip_fence(text))
    except (ValueError, RecursionError):
        out.unreadable = NOT_JSON
        return None
    raw = data.get(key) if isinstance(data, dict) else None
    if not isinstance(raw, list):
        out.unreadable = NO_ITEMS
        return None
    return raw


def check_leads(text: str | None, ctx: CallContext, checklist: Checklist) -> Checked:
    """First pass of the two-pass review. Each lead must name a file shown, a function the parser
    finds in that file among the lines shown, and an entry of the checklist; a bad one is dropped
    and counted. Nothing here is a finding."""
    out = Checked()
    raw_leads = _list_of(text, "leads", out)
    if raw_leads is None:
        return out
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_leads:
        if len(out.leads) >= MAX_LEADS:
            out.dropped["too_many"] += 1
            continue
        try:
            lead = LeadItem.model_validate(
                {**raw, "reason": clean_note(raw.get("reason", ""))}
                if isinstance(raw, dict)
                else raw
            )
        except (ValidationError, AttributeError):
            out.dropped["bad_item"] += 1
            continue
        shown = ctx.shown.get(lead.file_id)
        if shown is None:
            out.dropped["unknown_file"] += 1
        elif not any(
            m.name == lead.function and m.start_line in shown
            for m in ctx.members.get(lead.file_id, ())
        ):
            out.dropped["unknown_function"] += 1
        elif checklist.entry(lead.entry) is None:
            out.dropped["unknown_entry"] += 1
        elif (key := (lead.file_id, lead.function, lead.entry)) in seen:
            out.dropped["duplicate"] += 1
        else:
            seen.add(key)
            out.leads.append(Lead(**lead.model_dump()))
    return out


def check_verdicts(text: str | None, ctx: CallContext, checklist: Checklist) -> Checked:
    """Second pass. Only a verdict of `applies` is flagged, and then it is checked exactly like a
    single-pass item (file shown, every line shown, the quote in the code, the entry in the
    checklist); `not_applicable` and `unsure` are counted as decisions and flag nothing. The
    model's account of how the problem happens becomes the note."""
    out = Checked()
    raw_verdicts = _list_of(text, "verdicts", out)
    if raw_verdicts is None:
        return out
    seen: set[tuple[str, int, int, str]] = set()
    for raw in raw_verdicts:
        if len(out.accepted) >= MAX_REVIEWER_ITEMS:
            out.dropped["too_many"] += 1
            continue
        try:
            verdict = VerdictItem.model_validate(
                {**raw, "path": clean_note_to(raw.get("path", ""), MAX_PATH_NOTE_CHARS)}
                if isinstance(raw, dict)
                else raw
            )
        except (ValidationError, AttributeError):
            out.dropped["bad_item"] += 1
            continue
        if verdict.verdict != "applies":
            out.dropped[verdict.verdict] += 1
            continue
        assert verdict.start_line is not None and verdict.end_line is not None
        item = ReviewerItem(
            file_id=verdict.file_id,
            start_line=verdict.start_line,
            end_line=verdict.end_line,
            quote=verdict.quote,
            function=verdict.function,
            entry=verdict.entry,
            clause=None,
            severity=verdict.severity,
            confidence=verdict.confidence,
            note=clean_note(verdict.path),
        )
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
