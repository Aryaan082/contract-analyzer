"""Known libraries, new code, the outline, the chunks and the size check (section 2.5 of the note).

Every definition of a bundle is one of four things, decided in this order:

* **not reviewed**: its file could not be normalised safely, or the parser found an ERROR or
  MISSING node inside it;
* **known**: its normalised hash is in a pinned list of official library releases (decisions 10
  and 14). Only the hash decides: never a name, a path, a comment or anything the file says about
  itself. One changed token is a different hash, and a different hash is new code;
* **outline only**: no function has a body (interfaces, plain declarations); nothing to review;
* **new**: everything else. This is what the reviewer reads, all of it.

The hash is taken over the definition's token stream: comments and whitespace gone, strings kept as
written, operators cut the way the compiler cuts them (longest match, so `i++ + j` and `i + ++j`
differ). It covers the definition only, not the `pragma`, `import` and licence lines around it.

The size check runs before any model call: new code over the ceiling is refused, never half
reviewed. Tokens are characters divided by 3.5, counted on the stripped text.
"""

import hashlib
import json
import math
import re
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from review.lexer import GRAMMAR, Definition, Lexed, Member, Region, definition_lines, lex
from review.types import Bundle, FileId, KnownLibrary, Sha256, SourceFile

NORMALISER_VERSION = "1"
LIST_FORMAT = 1
DEFAULT_LIST_PATH = Path(__file__).parent / "data" / "known_libraries.json"

CHARS_PER_TOKEN = 3.5
CEILING_TOKENS = 300_000  # new code over this is refused (decision 10)
CHUNK_TOKENS = 12_000  # one reviewer call, about (decision 10)
MAX_NAME_CHARS = 64  # longer names are shown as <id:N> in the outline
MAX_LISTED = 12  # names per kind listed for a file's plain declarations
MAX_MEMBERS_LISTED = 60  # signatures per definition in the outline

Status = Literal["known", "new", "outline_only", "not_reviewed"]
HASHABLE_KINDS = ("contract", "library", "interface", "free_function")

_TOKEN = re.compile(
    r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'"  # a string, whole
    r"|[A-Za-z0-9_$]+"  # a word or the digits of a number
    r"|>>>=|<<=|>>=|>>>|\*\*|\+\+|--|==|!=|<=|>=|&&|\|\||\+=|-=|\*=|/=|%=|\|=|&=|\^=|<<|>>|=>|->|:="
    r"|\S",
    re.DOTALL,
)
_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def tokens_for(chars: int) -> int:
    """Characters divided by 3.5, rounded up, in integers so the boundary is exact."""
    return math.ceil(chars * 2 / 7)


# ---- hashing a definition -----------------------------------------------------------------------


def normalised_tokens(text: str, regions: Sequence[Region], definition: Definition) -> list[str]:
    """The token stream of `definition`: no comments, no whitespace, strings as written."""
    start, end = definition.start, definition.end
    piece = []
    pos = start
    for region in regions[bisect_left([r.start for r in regions], start) :]:
        if region.start >= end:
            break
        if region.kind == "comment":
            piece.append(text[pos : region.start])
            piece.append(" ")
            pos = region.end
    piece.append(text[pos:end])
    return _TOKEN.findall("".join(piece))


def definition_hash(text: str, regions: Sequence[Region], definition: Definition) -> str:
    tokens = normalised_tokens(text, regions, definition)
    body = json.dumps(tokens, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("ascii")).hexdigest()


# ---- the pinned list ----------------------------------------------------------------------------


class KnownListError(ValueError):
    """The list on disk is not one this code may use: wrong format, grammar or normaliser."""


class KnownEntry(BaseModel, frozen=True):
    """One definition of one official release. `name` and `path` are labels for a human; matching
    uses `sha256` alone."""

    sha256: Sha256
    kind: Literal["contract", "library", "interface", "free_function"]
    name: str
    library: str  # e.g. "OpenZeppelin Contracts"
    version: str  # the release, e.g. "4.9.3"
    path: str  # inside that release, e.g. "contracts/access/Ownable.sol"


class KnownList(BaseModel, frozen=True):
    """The file format of the pinned list (`review/data/known_libraries.json`, built later from
    official releases, only after Aryaan says so). `grammar` and `normaliser` say which parser and
    which normalisation made the hashes; a list made by another one is refused, not guessed at."""

    format: Literal[1] = LIST_FORMAT
    grammar: str = GRAMMAR
    normaliser: str = NORMALISER_VERSION
    entries: tuple[KnownEntry, ...] = ()

    @cached_property
    def _index(self) -> dict[str, tuple[KnownEntry, ...]]:
        found: dict[str, list[KnownEntry]] = defaultdict(list)
        for entry in self.entries:
            found[entry.sha256].append(entry)
        return {sha: tuple(entries) for sha, entries in found.items()}

    def lookup(self, sha256: str) -> tuple[KnownEntry, ...]:
        return self._index.get(sha256, ())

    def dump(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=1, sort_keys=True) + "\n"


def load_known(path: Path | None = None) -> KnownList:
    """The pinned list at `path` (default: the shipped file). No file means an empty list, so
    everything is new code: dearer, never unsafe. A file made by another grammar or normaliser
    raises `KnownListError`."""
    path = path or DEFAULT_LIST_PATH
    if not path.exists():
        return KnownList()
    known = KnownList.model_validate_json(path.read_text())
    if known.grammar != GRAMMAR:
        raise KnownListError(f"list was made with {known.grammar}, this is {GRAMMAR}: rebuild it")
    if known.normaliser != NORMALISER_VERSION:
        raise KnownListError(f"list was made by normaliser {known.normaliser}: rebuild it")
    return known


def entries_for(text: str, *, library: str, version: str, path: str) -> list[KnownEntry]:
    """The list entries of one library file: what the (later) list builder calls per file of an
    official release. Refuses a file that does not lex cleanly."""
    lexed = lex("F1", text)
    if lexed.view is None:
        raise ValueError(f"{path}: {'; '.join(lexed.problems)}")
    entries = []
    for d in lexed.definitions:
        if d.kind in HASHABLE_KINDS and d.parse_ok:
            sha = definition_hash(text, lexed.regions, d)
            entries.append(
                KnownEntry(
                    sha256=sha,
                    kind=d.kind,
                    name=d.name,
                    library=library,
                    version=version,
                    path=path,
                )
            )
    return entries


# ---- known or new -------------------------------------------------------------------------------


class Classified(BaseModel, frozen=True):
    definition: Definition
    status: Status
    sha256: Sha256 | None = None
    known: tuple[KnownEntry, ...] = ()
    reason: str | None = None  # why it was not reviewed
    chars: int = 0  # characters of its stripped text, for a new definition


class Split(BaseModel, frozen=True):
    """Every definition of a bundle, in file order then line order, each with its status."""

    items: tuple[Classified, ...]

    def with_status(self, status: Status) -> tuple[Classified, ...]:
        return tuple(i for i in self.items if i.status == status)

    @property
    def new_chars(self) -> int:
        return sum(i.chars for i in self.items if i.status == "new")

    @property
    def new_tokens(self) -> int:
        return tokens_for(self.new_chars)

    def known_libraries(self) -> tuple[KnownLibrary, ...]:
        """How many definitions matched each (library, version). A definition that is in several
        releases counts under each of them."""
        count: Counter[tuple[str, str]] = Counter()
        for item in self.with_status("known"):
            for lib_version in {(e.library, e.version) for e in item.known}:
                count[lib_version] += 1
        return tuple(
            KnownLibrary(library=lib, version=ver, definitions=n)
            for (lib, ver), n in sorted(count.items())
        )


def classify(files: Sequence[SourceFile], lexed: Mapping[str, Lexed], known: KnownList) -> Split:
    items: list[Classified] = []
    for file in files:
        result = lexed[file.file_id]
        for d in result.definitions:
            items.append(_classify(file.content, result, d, known))
    return Split(items=tuple(items))


def _classify(text: str, result: Lexed, d: Definition, known: KnownList) -> Classified:
    if result.view is None:
        return Classified(
            definition=d, status="not_reviewed", reason="source could not be normalised safely"
        )
    if not d.parse_ok:
        return Classified(definition=d, status="not_reviewed", reason="could not be parsed")
    if d.kind == "declaration":
        if d.declares == "constant":
            # a file-level constant's value is code the reviewer reads (Aryaan, 2026-10-29)
            chars = len("\n".join(definition_lines(result.view, d)))
            return Classified(definition=d, status="new", chars=chars)
        return Classified(definition=d, status="outline_only")
    sha = definition_hash(text, result.regions, d)
    if matches := known.lookup(sha):
        return Classified(definition=d, status="known", sha256=sha, known=matches)
    if not d.has_bodies:
        return Classified(definition=d, status="outline_only", sha256=sha)
    chars = len("\n".join(definition_lines(result.view, d)))
    return Classified(definition=d, status="new", sha256=sha, chars=chars)


# ---- the size check -----------------------------------------------------------------------------


class SizeCheck(BaseModel, frozen=True):
    new_chars: int
    new_tokens: int
    ceiling_tokens: int
    ok: bool
    reason: str | None = None


def size_check(split: Split, ceiling_tokens: int = CEILING_TOKENS) -> SizeCheck:
    """Decide before any model call: the new code is reviewed whole, or not at all."""
    tokens = split.new_tokens
    if tokens > ceiling_tokens:
        reason = f"the new code is {tokens} tokens, over the review ceiling of {ceiling_tokens}"
        return SizeCheck(
            new_chars=split.new_chars,
            new_tokens=tokens,
            ceiling_tokens=ceiling_tokens,
            ok=False,
            reason=reason,
        )
    return SizeCheck(
        new_chars=split.new_chars, new_tokens=tokens, ceiling_tokens=ceiling_tokens, ok=True
    )


# ---- chunks -------------------------------------------------------------------------------------


class Piece(BaseModel, frozen=True):
    """Whole lines `start_line`..`end_line` of a file's view; line numbers are the raw file's."""

    file_id: FileId
    definition: str
    start_line: int
    end_line: int
    text: str


class Chunk(BaseModel, frozen=True):
    """What one reviewer call reads. `oversize` marks a single function larger than the budget,
    which is never cut."""

    index: int
    pieces: tuple[Piece, ...]
    chars: int
    tokens: int
    oversize: bool = False


def plan_chunks(
    split: Split,
    lexed: Mapping[str, Lexed],
    entry: str | None = None,
    max_tokens: int = CHUNK_TOKENS,
) -> tuple[Chunk, ...]:
    """Cut the new definitions into calls of about `max_tokens` code tokens each.

    Order: the deployed contract (`entry`, the name the caller knows it by, never printed) with the
    new contracts it inherits, then the new definitions those mention, then the rest in file order.
    A definition goes whole into a call if it fits; a larger one is cut between members, never
    inside a function."""
    new = list(split.with_status("new"))
    texts = [_text(lexed, i.definition) for i in new]
    budget = int(max_tokens * CHARS_PER_TOKEN)
    pieces: list[Piece] = []
    for index in _order(new, texts, entry):
        pieces.extend(_pieces(new[index].definition, texts[index], lexed, budget))

    chunks: list[list[Piece]] = []
    sizes: list[int] = []
    for piece in pieces:
        size = len(piece.text) + 1
        if chunks and sizes[-1] + size <= budget:
            chunks[-1].append(piece)
            sizes[-1] += size
        else:
            chunks.append([piece])
            sizes.append(size)
    return tuple(
        Chunk(
            index=i,
            pieces=tuple(c),
            chars=sizes[i],
            tokens=tokens_for(sizes[i]),
            oversize=sizes[i] > budget,
        )
        for i, c in enumerate(chunks)
    )


def _text(lexed: Mapping[str, Lexed], d: Definition) -> str:
    view = lexed[d.file_id].view
    assert view is not None  # only new definitions get here, and they have a view
    return "\n".join(definition_lines(view, d))


def _order(new: Sequence[Classified], texts: Sequence[str], entry: str | None) -> list[int]:
    by_name: dict[str, list[int]] = defaultdict(list)
    for i, item in enumerate(new):
        by_name[item.definition.name].append(i)
    placed: dict[int, None] = {}

    def neighbours(i: int, references: bool) -> list[int]:
        names = list(new[i].definition.bases)
        if references:
            names += [n for n in _IDENTIFIER.findall(texts[i]) if n in by_name]
        return [j for name in names for j in by_name.get(name, []) if j != i and j not in placed]

    def visit(queue: list[int], references: bool) -> None:
        while queue:
            i = queue.pop(0)
            if i not in placed:
                placed[i] = None
                queue.extend(neighbours(i, references))

    if entry is not None:
        visit(list(by_name.get(entry, [])), references=False)
        mentioned = [j for i in list(placed) for j in neighbours(i, True)]
        visit(mentioned, references=True)
    return list(placed) + [i for i in range(len(new)) if i not in placed]


def _pieces(d: Definition, text: str, lexed: Mapping[str, Lexed], budget: int) -> list[Piece]:
    """The definition as one piece, or as several cut at member boundaries when it is too big."""
    whole = Piece(
        file_id=d.file_id,
        definition=d.name,
        start_line=d.start_line,
        end_line=d.start_line + text.count("\n"),
        text=text,
    )
    if len(text) + 1 <= budget or not d.members:
        return [whole]
    lines = text.split("\n")
    out: list[Piece] = []
    current: list[tuple[int, int]] = []
    size = 0
    for first, last in _blocks(d):
        block = "\n".join(lines[first - d.start_line : last - d.start_line + 1])
        if current and size + len(block) + 1 > budget:
            out.append(_piece(d, lines, current))
            current, size = [], 0
        current.append((first, last))
        size += len(block) + 1
    if current:
        out.append(_piece(d, lines, current))
    return out


def _piece(d: Definition, lines: Sequence[str], blocks: Sequence[tuple[int, int]]) -> Piece:
    first, last = blocks[0][0], blocks[-1][1]
    body = lines[first - d.start_line : last - d.start_line + 1]
    while body and not body[-1]:
        body.pop()
        last -= 1
    return Piece(
        file_id=d.file_id, definition=d.name, start_line=first, end_line=last, text="\n".join(body)
    )


def _blocks(d: Definition) -> list[tuple[int, int]]:
    """Line ranges that cover the definition and never fall inside a member: members that share
    a line are merged, the header goes with the first block and the closing brace with the last."""
    merged: list[list[int]] = []
    for m in sorted(d.members, key=lambda m: (m.start_line, m.end_line)):
        if merged and m.start_line <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], m.end_line)
        else:
            merged.append([m.start_line, m.end_line])
    merged[0][0] = d.start_line
    merged[-1][1] = d.end_line
    for prev, nxt in zip(merged, merged[1:], strict=False):
        nxt[0] = prev[1] + 1
    return [(a, b) for a, b in merged]


# ---- the outline --------------------------------------------------------------------------------


def _name(name: str) -> str:
    return name if len(name) <= MAX_NAME_CHARS else f"<id:{len(name)}>"


def _listed(names: Sequence[str], limit: int) -> str:
    more = f" +{len(names) - limit} more" if len(names) > limit else ""
    return ", ".join(names[:limit]) + more


def _member_names(members: Sequence[Member], kinds: tuple[str, ...]) -> list[str]:
    return [_name(m.name) for m in members if m.kind in kinds and m.name]


def build_outline(bundle: Bundle, lexed: Mapping[str, Lexed], split: Split) -> str:
    """A table of contents of the whole bundle, known and new, for every reviewer call.

    It names files by id only (never by path), definitions by their identifiers, and lists a
    signature for each function of new code. Known libraries and interfaces get names only."""
    by_file: dict[str, list[Classified]] = defaultdict(list)
    for item in split.items:
        by_file[item.definition.file_id].append(item)
    out = ["Outline of the bundle (file ids; stripped view; line numbers are the file's)"]
    for file in bundle.files:
        items = by_file.get(file.file_id, [])
        result = lexed[file.file_id]
        if result.view is None:
            out.append(f"{file.file_id}: not checked (source could not be normalised safely)")
            continue
        out.append(f"{file.file_id}:")
        declared: dict[str, list[str]] = defaultdict(list)
        constants = 0
        for item in items:
            if item.definition.kind == "declaration" and item.status == "new":
                constants += 1  # its value is in the code, not in the outline
            elif item.definition.kind == "declaration" and item.status != "not_reviewed":
                declared[item.definition.declares].append(_name(item.definition.name))
            else:
                out.extend(_definition_lines(item))
        if constants:
            out.append(f"  file-level constants: {constants}, their values are in the code")
        if declared:
            parts = []
            for kind, names in declared.items():
                parts.append(f"{kind}: {_listed(names, MAX_LISTED)}")
            out.append("  declarations: " + "; ".join(parts))
    dropped = Counter()
    for d in bundle.dropped:
        dropped[d.reason] += d.count
    for reason, count in sorted(dropped.items()):
        out.append(f"Not fetched: {count} file(s), {reason}")
    return "\n".join(out)


def _definition_lines(item: Classified) -> list[str]:
    d = item.definition
    kind = ("abstract " if d.abstract else "") + d.kind.replace("_", " ")
    head = f"  {kind} {_name(d.name)}"
    if d.bases:
        head += " is " + ", ".join(_name(b) for b in d.bases)
    if item.status == "known":
        first = item.known[0]
        more = f" and {len(item.known) - 1} more releases" if len(item.known) > 1 else ""
        label = f"{first.library} {first.version}{more}, hash-matched, unmodified, not reviewed"
        names = _member_names(d.members, ("function", "modifier", "constructor", "receive"))
        return [f"{head} [known: {label}]: " + _listed(names, MAX_MEMBERS_LISTED)]
    if item.status == "not_reviewed":
        return [f"{head} [not reviewed: {item.reason}]"]
    if item.status == "outline_only":
        names = _member_names(d.members, ("function", "modifier"))
        listed = ": " + _listed(names, MAX_MEMBERS_LISTED) if names else ""
        return [f"{head} [outline only, no function bodies]{listed}"]
    lines = [f"{head} [new, lines {d.start_line}-{d.end_line}]"]
    state = _member_names(d.members, ("state_variable",))
    if state:
        lines.append("    state: " + _listed(state, MAX_LISTED * 3))
    other = _member_names(d.members, ("event", "error", "struct", "enum"))
    if other:
        lines.append("    types and events: " + _listed(other, MAX_LISTED * 3))
    shown = [
        m
        for m in d.members
        if m.signature
        and (
            m.kind in ("modifier", "constructor", "receive", "fallback")
            or m.visibility in ("public", "external")
        )
    ]
    for m in shown[:MAX_MEMBERS_LISTED]:
        lines.append(f"    {m.kind} {m.signature}")
    hidden = sum(1 for m in d.members if m.signature) - min(len(shown), MAX_MEMBERS_LISTED)
    if hidden:
        lines.append(f"    ... and {hidden} internal or private functions, see the code")
    return lines
