"""The shared shapes of source review, defined once.

The validator, the API (`api/schemas.py` imports these, never copies them) and the web viewer all
read the same records. Golden JSON files and their JSON Schema (`tests/fixtures/review/golden/`) are
made from these models, so a change here breaks the web tests the same day.

Two kinds of type live here:

* **Records that leave the system** (`SourceSpan`, `PrecedentCard`, `SourceFileView`,
  `ReviewSummary`). Their JSON keys are snake_case, except `schema`, `from` and `to`, which have
  Python-safe field names and an alias; always write them with `to_json`.
* **Records that stay inside** (`SourceFile`, `Bundle`, `View`, and the reviewer's reply,
  `ReviewerReply`).

Everything fetched from an explorer, every path and every reviewer string is untrusted text. These
models bound it (length, pattern, shape) but never make it safe to obey: the viewer renders it as
text only, and the writer model sees closed fields only.
"""

import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1

# The ordering words of agent/report.py's Severity, without "info": a source finding is never info.
Severity = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

SpanKind = Literal["span", "scan"]  # "fact" is reserved and has no shape in v1 (decision 8)
Relation = Literal["same_class", "same_code_family"]
Closeness = Literal["close", "related"]
LineClass = Literal["comment", "string", "code"]
ScanFlag = Literal["hidden_character", "non_ascii", "instruction_like", "long_identifier"]
ReviewStatus = Literal["ran", "partial", "refused", "skipped", "failed"]
Language = Literal["solidity", "vyper", "yul", "unknown"]
DroppedReason = Literal[
    "urls_only", "too_many_files", "too_large", "linked_library", "diamond_facet"
]

FileId = Annotated[str, Field(pattern=r"^F[1-9][0-9]*$")]
SpanId = Annotated[str, Field(pattern=r"^S[1-9][0-9]*$")]
EntryId = Annotated[str, Field(pattern=r"^taxonomy:[a-z0-9][a-z0-9-]*$")]
FindingId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]*:[A-Za-z0-9][A-Za-z0-9._-]*$")]
Address = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]
Hex32 = Annotated[str, Field(pattern=r"^0x[0-9a-f]{64}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_$][A-Za-z0-9_$]*$", max_length=40)]
HttpsUrl = Annotated[str, Field(pattern=r"^https://", max_length=500)]
Month = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}$")]

MAX_PATH_CHARS = 200
MAX_NOTE_CHARS = 200  # the reviewer's one line
MAX_EXCERPT_CHARS = 280  # one verbatim sentence of an audit finding (decision 22)
MAX_PRECEDENTS = 3  # per span (decision 21)
MAX_SPAN_LINES = 200  # a flagged place is a few lines, not a file
MAX_REVIEWER_ITEMS = 50  # per reply; a proposal, the span validator drops the rest and counts them


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


# ---- helpers every consumer must agree on -----------------------------------------------------


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def span_sha256(stripped_lines: Sequence[str]) -> str:
    """The hash of a span: its stripped lines joined with a newline, no trailing newline."""
    return sha256_hex("\n".join(stripped_lines))


def bundle_sha256(files: Sequence["SourceFile"], language: str, compiler: str | None) -> str:
    """The hash that fixes the exact files: sorted (path, file hash) pairs, language and compiler.

    Content addressed, so it is the cache key of the bundle; it ignores file ids and order."""
    pairs = sorted((f.path, f.sha256) for f in files)
    body = {"files": pairs, "language": language, "compiler": compiler}
    return sha256_hex(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def to_json(model: BaseModel) -> dict[str, Any]:
    """A record as the JSON that goes on the wire and into the golden files."""
    return model.model_dump(mode="json", by_alias=True)


# ---- what was fetched (inside the system) -----------------------------------------------------


class SourceFile(_Model):
    """One file as the explorer published it. `path` is display text; the reviewer sees an id."""

    file_id: FileId
    path: Annotated[str, Field(max_length=MAX_PATH_CHARS)]
    content: str
    sha256: Sha256

    @model_validator(mode="after")
    def _hash_matches(self) -> "SourceFile":
        if self.sha256 != sha256_hex(self.content):
            raise ValueError("sha256 is not the hash of content")
        return self


class DroppedFile(_Model):
    """A file the bundle does not hold. One entry can stand for several files (`count`), such as
    "137 more files" past the file cap, so the list stays short."""

    path: Annotated[str, Field(max_length=MAX_PATH_CHARS)]
    reason: DroppedReason
    count: Annotated[int, Field(ge=1)] = 1


class Bundle(_Model):
    """Everything fetched for one logic address, fixed by hash. `code_hash` is the on-chain code at
    the pinned block, so a redeploy at the same address cannot be served this bundle."""

    chain: str
    chain_id: int
    address: Address
    block: int
    code_hash: Hex32
    language: Language
    compiler: str | None = None
    files: tuple[SourceFile, ...]
    dropped: tuple[DroppedFile, ...] = ()
    bundle_sha256: Sha256

    @model_validator(mode="after")
    def _consistent(self) -> "Bundle":
        ids = [f.file_id for f in self.files]
        if len(set(ids)) != len(ids):
            raise ValueError("file ids are not unique")
        if self.bundle_sha256 != bundle_sha256(self.files, self.language, self.compiler):
            raise ValueError("bundle_sha256 does not match the files")
        return self


class View(_Model):
    """The stripped view of one file: comments and strings removed, every line break kept, so line n
    is line n in the raw file. `line_classes[n - 1]` are the (start, end, class) spans of line n in
    the *published* text, for the viewer's colours; text outside every listed range is code."""

    file_id: FileId
    text: str
    line_classes: tuple[tuple[tuple[int, int, LineClass], ...], ...]

    @model_validator(mode="after")
    def _same_line_count(self) -> "View":
        if len(self.line_classes) != self.text.count("\n") + 1:
            raise ValueError("line_classes needs one entry per line of text")
        return self


# ---- a finding (leaves the system) ------------------------------------------------------------


class Subject(_Model):
    """What code this is: the logic contract (the implementation, for a proxy) at a pinned block."""

    chain: str
    chain_id: int
    address: Address
    block: int
    code_hash: Hex32
    bundle_sha256: Sha256


class FileRef(_Model):
    file_id: FileId
    path: Annotated[str, Field(max_length=MAX_PATH_CHARS)]  # display only, cleaned
    sha256: Sha256


class LineRange(_Model):
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    span_sha256: Sha256
    function: Identifier | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "LineRange":
        if self.end_line < self.start_line:
            raise ValueError("end_line is before start_line")
        return self


class Match(_Model):
    """What the reviewer matched the span to. `severity` is after the validator's cap; `high` exists
    only with `confirmed_by`, the id of the blind second pass that agreed (decision 11)."""

    entry: EntryId
    # index into the entry's "Vulnerable when" clauses
    clause: Annotated[int, Field(ge=0)] | None = None
    severity: Severity
    confirmed_by: SpanId | None = None
    severity_claimed: Severity  # what the reviewer wrote, kept for the audit trail
    confidence: Confidence

    @model_validator(mode="after")
    def _cap_only_lowers(self) -> "Match":
        if SEVERITY_RANK[self.severity] > SEVERITY_RANK[self.severity_claimed]:
            raise ValueError("a cap can lower a severity, never raise it")
        if self.severity == "high" and self.confirmed_by is None:
            raise ValueError("high needs confirmed_by (a blind second pass agreed)")
        return self


class PrecedentRef(_Model):
    """An audit finding attached by our code, never named by the model: an id, no URL or title."""

    finding_id: FindingId
    relation: Relation
    closeness: Closeness  # set by the hand-checked precedent map (decision 21)


class Explanation(_Model):
    """The reviewer's one line: shown on screen as text, labelled a model's note, never given to the
    writer."""

    text: Annotated[str, Field(max_length=MAX_NOTE_CHARS)]
    by: Literal["reviewer_model"] = "reviewer_model"
    checked: bool = False


class ReviewerInfo(_Model):
    model: str
    prompt_sha: Sha256
    checklist_sha: Sha256
    lexer_version: str  # bump on any change to the stripping step (our wrapper around the parser)


class ScanInfo(_Model):
    """A deterministic observation of text aimed at a reader (no model involved)."""

    flag: ScanFlag
    count: Annotated[int, Field(ge=1)]


class SourceSpan(_Model):
    """The `S` evidence record. `kind="span"` is a reviewer item that passed validation;
    `kind="scan"` is a deterministic scan flag. Created only by the runner; the writer cannot create
    or alter one."""

    schema_version: Literal[1] = Field(SCHEMA_VERSION, alias="schema")
    id: SpanId
    kind: SpanKind
    subject: Subject
    file: FileRef
    range: LineRange
    match: Match | None = None
    precedents: Annotated[list[PrecedentRef], Field(max_length=MAX_PRECEDENTS)] = []
    explanation: Explanation | None = None
    reviewer: ReviewerInfo | None = None
    scan: ScanInfo | None = None

    @model_validator(mode="after")
    def _shape_follows_kind(self) -> "SourceSpan":
        if self.kind == "span":
            if self.match is None or self.explanation is None or self.reviewer is None:
                raise ValueError("a span needs match, explanation and reviewer")
            if self.scan is not None:
                raise ValueError("a span carries no scan")
            if self.match.confirmed_by == self.id:
                raise ValueError("a span cannot confirm itself")
        else:
            if self.scan is None:
                raise ValueError("a scan record needs scan")
            if self.match is not None or self.precedents or self.explanation is not None:
                raise ValueError("a scan record carries no match, precedents or explanation")
        return self


class Credit(_Model):
    """Everything a card says about an audit finding, built by `credit_for(finding_id)` from our own
    records. `link` is the report's GitHub page for the pinned commit; `page` is printed beside it
    because the page anchor is not honoured there (decision 22)."""

    auditor: str
    report_title: str
    protocol: str
    date: Month
    finding_label: str  # the report's own id, e.g. TOB-BALV3-12
    # the auditor's word, never ours: "rated {x} by {auditor} in that report"
    severity_by_auditor: str
    title: str
    page: Annotated[int, Field(ge=1)]
    page_end: Annotated[int, Field(ge=1)] | None = None
    excerpt: Annotated[str, Field(max_length=MAX_EXCERPT_CHARS)]
    link: HttpsUrl
    licence: str
    licence_url: HttpsUrl
    changes: str


class PrecedentCard(_Model):
    finding_id: FindingId
    relation: Relation
    closeness: Closeness
    credit: Credit


# ---- the viewer ---------------------------------------------------------------------------------


class Line(_Model):
    """One line as published, with the comment and string ranges in it; the rest is code."""

    n: Annotated[int, Field(ge=1)]
    text: str  # as published (untrusted); render as text only
    classes: tuple[tuple[int, int, LineClass], ...] = ()

    @model_validator(mode="after")
    def _classes_fit(self) -> "Line":
        end = 0
        for start, stop, _ in self.classes:
            if not (end <= start < stop <= len(self.text)):
                raise ValueError("classes must be sorted, non-overlapping and inside the line")
            end = stop
        return self


class Highlight(_Model):
    span_id: SpanId
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    entry: EntryId
    group: str  # the entry's group (upgradeability, admin power, ...)
    confidence: Confidence


class SourceFileView(_Model):
    """The viewer's payload for one file, fetched lazily in line ranges."""

    schema_version: Literal[1] = Field(SCHEMA_VERSION, alias="schema")
    file_id: FileId
    path: Annotated[str, Field(max_length=MAX_PATH_CHARS)]
    sha256: Sha256
    language: Language
    line_count: Annotated[int, Field(ge=0)]
    from_line: Annotated[int, Field(ge=1)] = Field(alias="from")
    to_line: Annotated[int, Field(ge=1)] = Field(alias="to")
    lines: tuple[Line, ...]
    highlights: tuple[Highlight, ...] = ()
    stripped_available: bool

    @model_validator(mode="after")
    def _range_matches_lines(self) -> "SourceFileView":
        if self.to_line < self.from_line or self.to_line > self.line_count:
            raise ValueError("the range must be inside the file")
        if [x.n for x in self.lines] != list(range(self.from_line, self.to_line + 1)):
            raise ValueError("lines must be consecutive and fill the range")
        for h in self.highlights:
            if h.end_line < h.start_line or h.end_line > self.line_count:
                raise ValueError("a highlight must be inside the file")
        return self


# ---- coverage -----------------------------------------------------------------------------------


class KnownLibrary(_Model):
    """A library that matched the pinned list by hash and so was not reviewed (decision 14)."""

    library: str
    version: str
    definitions: Annotated[int, Field(ge=1)]


class ScanCount(_Model):
    flag: ScanFlag
    count: Annotated[int, Field(ge=1)]


class ReviewSummary(_Model):
    """The coverage block, written by the runner and never by a model. The first group of fields has
    the approach note's names (5.4); the group after it follows decisions 10, 11 and 14."""

    schema_version: Literal[1] = Field(SCHEMA_VERSION, alias="schema")
    status: ReviewStatus
    reason: str | None = None  # plain words, required unless the review ran
    reviewer_model: str | None = None
    files_total: Annotated[int, Field(ge=0)] = 0
    files_reviewed: Annotated[int, Field(ge=0)] = 0
    chars_total: Annotated[int, Field(ge=0)] = 0
    chars_reviewed: Annotated[int, Field(ge=0)] = 0
    dropped: tuple[DroppedFile, ...] = ()
    unsupported: str | None = None  # e.g. "language: vyper"
    assembly_blocks: Annotated[int, Field(ge=0)] = 0
    scan_flags: tuple[ScanCount, ...] = ()
    cost_usd: Annotated[float, Field(ge=0)] = 0.0
    # decisions 10, 11 and 14
    definitions_known: Annotated[int, Field(ge=0)] = 0
    definitions_new: Annotated[int, Field(ge=0)] = 0
    definitions_reviewed: Annotated[int, Field(ge=0)] = 0
    # definitions the lexer or parser could not read; they make a review partial, whatever else ran
    definitions_not_reviewed: Annotated[int, Field(ge=0)] = 0
    # files that could not be normalised safely and so were not read at all
    files_not_checked: Annotated[int, Field(ge=0)] = 0
    new_tokens: Annotated[int, Field(ge=0)] = 0
    ceiling_tokens: Annotated[int, Field(ge=0)] = 0
    known_libraries: tuple[KnownLibrary, ...] = ()
    confirmation_model: str | None = None

    @model_validator(mode="after")
    def _coverage_is_honest(self) -> "ReviewSummary":
        if self.status != "ran" and not self.reason:
            raise ValueError("a review that did not fully run needs a reason")
        if self.definitions_reviewed > self.definitions_new:
            raise ValueError("cannot review more definitions than are new")
        if self.files_reviewed > self.files_total or self.chars_reviewed > self.chars_total:
            raise ValueError("cannot review more than was fetched")
        reviewed, new = self.definitions_reviewed, self.definitions_new
        unread = self.definitions_not_reviewed + self.files_not_checked
        if self.status == "ran" and (reviewed != new or unread):
            raise ValueError(
                "a review that ran covered all the new definitions and could read every file"
            )
        if self.status == "partial" and not (reviewed < new or unread):
            raise ValueError("a partial review left some definitions or files unreviewed or unread")
        if self.status in ("refused", "skipped", "failed") and reviewed != 0:
            raise ValueError("a review that did not run covered nothing")
        return self


# ---- the reviewer's reply -----------------------------------------------------------------------


class ReviewerItem(_Model):
    """One flagged place. Everything is a closed field or bounded text; `entry` and `clause` are
    checked against the pack the reviewer was given, `quote` against the stripped view."""

    file_id: FileId
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    quote: Annotated[str, Field(max_length=2000)]
    # only compared with the parser's own answer, never shown: a loose name must not cost a finding
    function: Annotated[str, Field(max_length=100)] | None = None
    entry: EntryId
    clause: Annotated[int, Field(ge=0)] | None = None
    severity: Severity
    confidence: Confidence
    note: Annotated[str, Field(max_length=MAX_NOTE_CHARS)]

    @model_validator(mode="after")
    def _a_few_lines_in_order(self) -> "ReviewerItem":
        if not 0 <= self.end_line - self.start_line < MAX_SPAN_LINES:
            raise ValueError(f"a place is 1 to {MAX_SPAN_LINES} lines, in order")
        return self


class ReviewerReply(_Model):
    """The whole reply, as a JSON schema (`REVIEWER_SCHEMA`). An empty `items` is "flagged nothing".

    The span validator parses `items` one at a time, so one bad item is dropped and counted and does
    not discard the rest (fixture H18)."""

    items: Annotated[list[ReviewerItem], Field(max_length=MAX_REVIEWER_ITEMS)]


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """The JSON Schema with each `$ref` replaced by its definition (for providers without it)."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _without_docs(node: Any) -> Any:
    """The schema without the class docstrings pydantic copies in as descriptions: they are notes
    for us, not rules, and the model must not be sent them."""
    if isinstance(node, dict):
        return {
            k: _without_docs(v)
            for k, v in node.items()
            if not (k == "description" and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_without_docs(v) for v in node]
    return node


REVIEWER_SCHEMA: dict[str, Any] = _without_docs(_inline_refs(ReviewerReply.model_json_schema()))

# What the golden JSON Schema files cover: the records that leave the system, and the reply.
PUBLIC_MODELS: dict[str, type[BaseModel]] = {
    "SourceSpan": SourceSpan,
    "PrecedentCard": PrecedentCard,
    "SourceFileView": SourceFileView,
    "ReviewSummary": ReviewSummary,
    "ReviewerReply": ReviewerReply,
}


def schema_for(name: str) -> dict[str, Any]:
    return _without_docs(PUBLIC_MODELS[name].model_json_schema(by_alias=True))


def write_schemas(directory: Path) -> list[Path]:
    """Write one `<Name>.schema.json` per public model. Run it after any change to a model:
    `uv run python -m review.types tests/fixtures/review/golden/schema`."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name in PUBLIC_MODELS:
        path = directory / f"{name}.schema.json"
        path.write_text(json.dumps(schema_for(name), indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/review/golden/schema")
    for written in write_schemas(target):
        print(written)
