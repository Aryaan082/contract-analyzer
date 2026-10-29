"""Make the stripped view of a Solidity file: no comments, no string text, every line break kept.

The view is what the reviewer model reads, so it is built to fail closed. Three parts agree or the
file is "not checked", never best effort:

1. A small scanner of our own (`find_regions`) finds every comment and string literal. It does not
   use the parser, so it is the independent part.
2. `tree-sitter-solidity` parses the file. It supplies the definitions (contract, library,
   interface, and file-level items), the outline, and a second opinion: its comment and string nodes
   must be exactly the scanner's regions, and any ERROR or MISSING node inside a definition marks
   that definition "could not be parsed".
3. The finished view is read again by the scanner: no comment, no string but a placeholder or a
   kept reason, nothing but printable ASCII, the same number of lines as the raw file.

The one rule for the view: a comment becomes spaces; a string literal becomes `"<str:N>"`, where N
is the number of characters between its quotes, except a short plain reason that is an argument of
`require` or `revert` (at most 48 characters of letters, digits, spaces and `.,:;!?_()-`, and not
aimed at a reader), which is kept as written. Every line break survives, so line n of the view is
line n of the raw file, and each line is stripped of trailing whitespace. Bump `LEXER_VERSION` on
any change to this rule or to the checks, because it is part of every review's cache key.
"""

import re
import warnings
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator, Sequence
from functools import cache
from importlib.metadata import version
from typing import Literal

import tree_sitter_solidity as tss
from pydantic import BaseModel
from tree_sitter import Language, Node, Parser

from review.scan import aimed_at_reader
from review.types import FileId, LineClass, View

LEXER_VERSION = "1"
GRAMMAR = f"tree-sitter-solidity {version('tree-sitter-solidity')}"
MAX_KEPT_REASON = 48
MAX_SIGNATURE_CHARS = 240
MAX_FILE_CHARS = 2_000_000  # the bundle cap per address; a larger file is not parsed

DefinitionKind = Literal["contract", "library", "interface", "free_function", "declaration"]

_START = re.compile(r"//|/\*|[\"']")
_STRING = {
    '"': re.compile(r'"(?:\\.|[^"\\\n])*"', re.DOTALL),
    "'": re.compile(r"'(?:\\.|[^'\\\n])*'", re.DOTALL),
}
_LEFTOVER = re.compile(r"//|/\*|\*/|[\"']")
_NOT_PRINTABLE = re.compile(r"[^\x20-\x7e\t\n\r]")  # in code and plain strings (CR: see below)
_VIEW_NOT_PRINTABLE = re.compile(r"[^\x20-\x7e\t\n]")
# Solidity ends a `//` comment at any of these, so the scanner (LF only) would swallow code the
# compiler reads. They are refused anywhere in the file; a CR is fine only in a CRLF.
_ODD_BREAK = re.compile(r"\r(?!\n)|[\x0b\x0c\x85  ]")
_REASON_CHARS = re.compile(r"[A-Za-z0-9 .,:;!?_()\-]*")
_VIEW_STRING = re.compile(r"\"<str:[0-9]+>\"|([\"'])[A-Za-z0-9 .,:;!?_()\-]*\1")
_IDENT_CHAR = re.compile(r"[A-Za-z0-9_$]")


class Region(BaseModel, frozen=True):
    """A comment or a string literal, as character offsets `[start, end)` into the raw text. A
    string starts at its opening quote; `unicode` is true when `unicode` sits right before it."""

    start: int
    end: int
    kind: Literal["comment", "string"]
    unicode: bool = False


class Member(BaseModel, frozen=True):
    """One member of a contract, library or interface: enough for the outline and for cutting a
    long definition at member boundaries."""

    kind: str  # function, modifier, constructor, receive, fallback, state_variable, event, ...
    name: str | None
    start_line: int
    end_line: int
    has_body: bool
    visibility: str = ""  # public, external, internal or private, if the member says
    signature: str | None = None  # function-like members: name, parameter types, modifiers, returns


class Definition(BaseModel, frozen=True):
    """A top-level item of a file. `start` and `end` are character offsets into the raw text and
    `start_line`..`end_line` its lines. `parse_ok` is false when the parser put any ERROR or
    MISSING node inside it: such a definition is "not reviewed: could not be parsed"."""

    file_id: FileId
    kind: DefinitionKind
    name: str
    abstract: bool = False
    declares: str = ""  # for a file-level "declaration": struct, enum, error, event, constant, ...
    bases: tuple[str, ...] = ()
    start: int
    end: int
    start_line: int
    end_line: int
    parse_ok: bool
    has_bodies: bool
    members: tuple[Member, ...] = ()


class Lexed(BaseModel, frozen=True):
    """Everything the lexer knows about one file. `view` is None when the file could not be
    normalised safely (`problems` says why, in fixed words that never quote the file)."""

    file_id: FileId
    line_count: int
    view: View | None
    problems: tuple[str, ...]
    definitions: tuple[Definition, ...]
    regions: tuple[Region, ...]
    parse_errors: int = 0
    assembly_blocks: int = 0


# ---- the independent scanner --------------------------------------------------------------------


def find_regions(text: str) -> tuple[tuple[Region, ...], list[str]]:
    """Every comment and string literal of `text`, found without the parser, and the problems met.

    Linear time: each search moves forward, and a failed string or comment ends the scan of that
    line or file instead of being retried from the next character."""
    regions: list[Region] = []
    problems: list[str] = []
    n = len(text)
    pos = 0
    while match := _START.search(text, pos):
        i, token = match.start(), match.group()
        if token == "//":
            end = text.find("\n", i)
            end = n if end < 0 else end
            if (
                text[end - 1 : end] == "\r"
            ):  # a CR before the LF is the line break's, not the comment's
                end -= 1
            regions.append(Region(start=i, end=end, kind="comment"))
            pos = end
        elif token == "/*":
            close = text.find("*/", i + 2)
            if close < 0:
                problems.append("a block comment is not closed")
                regions.append(Region(start=i, end=n, kind="comment"))
                pos = n
            else:
                regions.append(Region(start=i, end=close + 2, kind="comment"))
                pos = close + 2
        else:
            found = _STRING[token].match(text, i)
            if found is None:
                problems.append("a string is not closed")
                line_end = text.find("\n", i)
                pos = n if line_end < 0 else line_end
                continue
            is_unicode = text[max(0, i - 7) : i] == "unicode" and (
                i == 7 or not _IDENT_CHAR.fullmatch(text[i - 8])
            )
            regions.append(Region(start=i, end=found.end(), kind="string", unicode=is_unicode))
            pos = found.end()
    return tuple(regions), problems


def code_segments(text: str, regions: Sequence[Region]) -> Iterator[tuple[int, str]]:
    """The text between regions, with its offset. Search each piece on its own: joining them would
    let `/` + comment + `/` pass for `//`."""
    pos = 0
    for region in regions:
        if region.start > pos:
            yield pos, text[pos : region.start]
        pos = max(pos, region.end)
    if pos < len(text):
        yield pos, text[pos:]


def check_raw(text: str, regions: Sequence[Region]) -> list[str]:
    """Things that make the scanner's reading of the raw text untrustworthy."""
    problems = []
    if _ODD_BREAK.search(text):
        problems.append("the file has a line break other than LF or CRLF")
    for _, segment in code_segments(text, regions):
        if _LEFTOVER.search(segment):
            problems.append("a comment or quote marker is left outside every region")
        if _NOT_PRINTABLE.search(segment):
            problems.append("non-ASCII or control character outside comments and unicode literals")
    for region in regions:
        plain = region.kind == "string" and not region.unicode
        if plain and _NOT_PRINTABLE.search(text, region.start, region.end):
            problems.append("non-ASCII or control character in a plain string")
    return problems


# ---- stripping ----------------------------------------------------------------------------------


def keepable(inner: str) -> bool:
    """May a reason string (the text between the quotes) stay in the view as written?"""
    return (
        len(inner) <= MAX_KEPT_REASON
        and _REASON_CHARS.fullmatch(inner) is not None
        and not aimed_at_reader(inner)
    )


def strip_text(text: str, regions: Sequence[Region], keep: Iterable[int] = ()) -> str:
    """The view of `text`: comments to spaces, strings to `"<str:N>"` (or kept, if their start is
    in `keep`), line breaks inside them preserved, trailing whitespace removed from every line."""
    kept = set(keep)
    out: list[str] = []
    pos = 0
    for region in regions:
        out.append(text[pos : region.start])
        body = text[region.start : region.end]
        if region.kind == "comment":
            out.append(re.sub(r"[^\n]", " ", body))
        elif region.start in kept:
            out.append(body)
        else:
            out.append(f'"<str:{len(body) - 2}>"' + "\n" * body.count("\n"))
        pos = region.end
    out.append(text[pos:])
    return "\n".join(line.rstrip(" \t\r") for line in "".join(out).split("\n"))


def check_view(view_text: str, raw_text: str) -> list[str]:
    """Read the finished view again, as if it were a new file, and compare it with the raw text."""
    problems = []
    if view_text.count("\n") != raw_text.count("\n"):
        problems.append("the view has a different number of lines than the file")
    if _VIEW_NOT_PRINTABLE.search(view_text):
        problems.append("the view has a character that is not printable ASCII")
    regions, found = find_regions(view_text)
    problems.extend(found)
    for region in regions:
        body = view_text[region.start : region.end]
        if region.kind == "comment":
            problems.append("the view still has a comment")
        elif not _VIEW_STRING.fullmatch(body) or (
            not body.startswith('"<str:') and aimed_at_reader(body)
        ):
            problems.append("the view has a string that is neither a placeholder nor a reason")
    for _, segment in code_segments(view_text, regions):
        if _LEFTOVER.search(segment):
            problems.append("the view has a comment or quote marker outside every region")
    return problems


class _Lines:
    """Character offset to 1-based line number, for lines split on LF only (the viewer's lines)."""

    def __init__(self, text: str):
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.length = len(text)

    def of(self, offset: int) -> int:
        return bisect_right(self.starts, offset)

    def span(self, start: int, end: int) -> list[tuple[int, int, int]]:
        """(line, start column, end column) for each line a region touches."""
        out = []
        first, last = self.of(start), self.of(max(start, end - 1))
        for line in range(first, last + 1):
            line_start = self.starts[line - 1]
            lo = max(start, line_start) - line_start
            line_end = self.starts[line] - 1 if line < len(self.starts) else self.length
            hi = min(end, line_end) - line_start
            if hi > lo:
                out.append((line, lo, hi))
        return out


def line_classes(
    text: str, regions: Sequence[Region]
) -> tuple[tuple[tuple[int, int, LineClass], ...], ...]:
    """For each line of the raw text, the (start, end, class) ranges of its comments and strings."""
    lines = _Lines(text)
    per_line: list[list[tuple[int, int, LineClass]]] = [[] for _ in lines.starts]
    for region in regions:
        for line, lo, hi in lines.span(region.start, region.end):
            per_line[line - 1].append((lo, hi, region.kind))
    return tuple(tuple(ranges) for ranges in per_line)


# ---- the parser's side --------------------------------------------------------------------------


@cache
def _language() -> Language:
    with warnings.catch_warnings():  # this grammar hands over a plain int pointer
        warnings.simplefilter("ignore", DeprecationWarning)
        return Language(tss.language())


class _Offsets:
    """Byte offset (what the parser reports) to character offset (what the text is indexed by)."""

    def __init__(self, text: str):
        self.starts: list[int] = []
        self.extra: list[int] = []
        total = 0
        for m in re.finditer(r"[^\x00-\x7f]", text):
            code = ord(m.group())
            width = 2 if code < 0x800 else 3 if code < 0x10000 else 4
            self.starts.append(m.start() + total)
            total += width - 1
            self.extra.append(total)

    def __call__(self, byte: int) -> int:
        k = bisect_left(self.starts, byte)
        return byte - (self.extra[k - 1] if k else 0)


_MEMBER_KINDS = {
    "function_definition": "function",
    "modifier_definition": "modifier",
    "constructor_definition": "constructor",
    "state_variable_declaration": "state_variable",
    "event_definition": "event",
    "error_declaration": "error",
    "struct_declaration": "struct",
    "enum_declaration": "enum",
    "using_directive": "using",
    "user_defined_type_definition": "type",
    "constant_variable_declaration": "constant",
}
_FUNCTION_LIKE = {"function", "modifier", "constructor", "receive", "fallback"}
_DEFINITION_KINDS: dict[str, DefinitionKind] = {
    "contract_declaration": "contract",
    "library_declaration": "library",
    "interface_declaration": "interface",
}
_NOT_DEFINITIONS = {"comment", "pragma_directive", "import_directive"}
_STRING_LITERALS = {"unicode_string_literal", "hex_string_literal", "yul_hex_string_literal"}
_UNPARSED_HEAD = re.compile(rb"\b(contract|library|interface)\s+([A-Za-z_$][A-Za-z0-9_$]*)")


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _broken(node: Node) -> bool:
    return node.has_error or node.is_missing or node.type == "ERROR"


class _Tree:
    """What the parse says, in character offsets."""

    def __init__(self, text: str, regions: Sequence[Region], file_id: str):
        self.file_id = file_id
        self.text = text
        self.regions = regions
        self.lines = _Lines(text)
        data = text.encode("utf-8")  # raises for lone surrogates; the caller refuses those
        self.data = data
        self.char = _Offsets(text) if len(data) != len(text) else (lambda b: b)
        self.root = Parser(_language()).parse(data).root_node
        self.unparsed: list[tuple[int, int]] = []
        self.comments: set[tuple[int, int]] = set()
        self.strings: set[tuple[int, int]] = set()
        self.reasons: set[int] = set()
        self.assembly_blocks = 0
        self.parse_errors = 0
        self.definitions: list[Definition] = []
        self._read()

    def _span(self, node: Node) -> tuple[int, int]:
        return self.char(node.start_byte), self.char(node.end_byte)

    def _text(self, node: Node | None) -> str | None:
        if node is None:
            return None
        return self.data[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def _read(self) -> None:
        if self.root.has_error:
            self.parse_errors = sum(
                1 for n in _walk(self.root) if n.type == "ERROR" or n.is_missing
            )
        for child in self.root.children:
            if child.type in _NOT_DEFINITIONS:
                if child.type == "comment":
                    self.comments.add(self._span(child))
                else:
                    self._collect(child)
                continue
            if _broken(child):
                self.unparsed.append(self._span(child))
                self.definitions.append(self._unparsed(child))
                continue
            self._collect(child)
            self.definitions.append(self._definition(child))

    def _collect(self, node: Node) -> None:
        """Comment and string nodes, assembly blocks and reason strings below `node`."""
        for n in _walk(node):
            kind = n.type
            if kind == "comment":
                self.comments.add(self._span(n))
            elif kind == "string" and n.is_named:  # the keyword `string` is not a literal
                self.strings.add(self._span(n))
            elif kind in _STRING_LITERALS:
                quotes = [c for c in n.children if c.type in ('"', "'")]
                for a, b in zip(quotes[0::2], quotes[1::2], strict=False):
                    self.strings.add((self.char(a.start_byte), self.char(b.end_byte)))
            elif kind == "assembly_statement":
                self.assembly_blocks += 1
            elif kind in ("call_expression", "revert_statement"):
                self._reason(n)

    def _only_string(self, expression: Node) -> int | None:
        """Where the string starts, if `expression` is nothing but one string literal."""
        if expression.type != "expression" or expression.child_count != 1:
            return None
        literal = expression.children[0]
        parts = [c for c in literal.children if c.type == "string"]
        if literal.type != "string_literal" or literal.child_count != 1 or len(parts) != 1:
            return None
        return self.char(parts[0].start_byte)

    def _reason(self, node: Node) -> None:
        """Remember the start of a string that is the message of `require(cond, "...")`,
        `revert("...")`. A custom error's arguments are not reasons and are not remembered."""
        found: list[int | None] = []
        if node.type == "call_expression":
            callee = node.children[0] if node.children else None
            if self._text(callee) == "require":
                found = [
                    self._only_string(arg.children[0])
                    for arg in node.children
                    if arg.type == "call_argument" and arg.child_count == 1
                ]
        elif len(node.children) == 3 and node.children[1].type == "expression":
            group = node.children[1].children
            if len(group) == 1 and group[0].type == "parenthesized_expression":
                found = [self._only_string(c) for c in group[0].children if c.type == "expression"]
        self.reasons.update(start for start in found if start is not None)

    def _lines_of(self, start: int, end: int) -> tuple[int, int]:
        return self.lines.of(start), self.lines.of(max(start, end - 1))

    def _unparsed(self, node: Node) -> Definition:
        start, end = self._span(node)
        head = _UNPARSED_HEAD.search(
            self.data[node.start_byte : min(node.end_byte, node.start_byte + 400)]
        )
        kind: DefinitionKind = "declaration"
        name = "<unparsed>"
        if head:
            kind = _DEFINITION_KINDS[head.group(1).decode() + "_declaration"]
            name = head.group(2).decode()
        first, last = self._lines_of(start, end)
        return Definition(
            file_id=self.file_id,
            kind=kind,
            name=name,
            start=start,
            end=end,
            start_line=first,
            end_line=last,
            parse_ok=False,
            has_bodies=True,
        )

    def _definition(self, node: Node) -> Definition:
        start, end = self._span(node)
        first, last = self._lines_of(start, end)
        name = self._text(node.child_by_field_name("name")) or node.type
        kind = _DEFINITION_KINDS.get(node.type)
        if kind is None:
            is_function = node.type == "function_definition"
            declares = "" if is_function else _MEMBER_KINDS.get(node.type, node.type)
            has_name = node.child_by_field_name("name") is not None
            return Definition(
                file_id=self.file_id,
                kind="free_function" if is_function else "declaration",
                declares=declares,
                name=name if has_name else declares,
                start=start,
                end=end,
                start_line=first,
                end_line=last,
                parse_ok=True,
                has_bodies=is_function and node.child_by_field_name("body") is not None,
            )
        bases = tuple(
            (self._text(spec.child_by_field_name("ancestor")) or "").rsplit(".", 1)[-1].strip()
            for spec in node.children
            if spec.type == "inheritance_specifier"
        )
        body = node.child_by_field_name("body")
        members = (
            tuple(self._member(m) for m in body.children if self._is_member(m)) if body else ()
        )
        return Definition(
            file_id=self.file_id,
            kind=kind,
            name=name,
            abstract=any(c.type == "abstract" for c in node.children),
            bases=tuple(b for b in bases if b),
            start=start,
            end=end,
            start_line=first,
            end_line=last,
            parse_ok=True,
            has_bodies=any(m.has_body for m in members),
            members=members,
        )

    @staticmethod
    def _is_member(node: Node) -> bool:
        return node.type not in ("{", "}", "comment")

    def _member(self, node: Node) -> Member:
        kind = _MEMBER_KINDS.get(node.type)
        if node.type == "fallback_receive_definition":
            kind = "receive" if self._text(node.children[0]) == "receive" else "fallback"
        kind = kind or node.type
        start, end = self._span(node)
        first, last = self._lines_of(start, end)
        body = node.child_by_field_name("body")
        visibility = next((self._text(c) for c in node.children if c.type == "visibility"), "")
        name = self._text(node.child_by_field_name("name"))
        signature = self._signature(node, kind, name) if kind in _FUNCTION_LIKE else None
        return Member(
            kind=kind,
            name=name,
            start_line=first,
            end_line=last,
            has_body=body is not None,
            visibility=visibility or "",
            signature=signature,
        )

    def _compact(self, node: Node | None) -> str:
        return " ".join((self._text(node) or "").split())[:40]

    def _signature(self, node: Node, kind: str, name: str | None) -> str:
        """`mint(address,uint256) external whenNotPaused,onlyMinters returns(bool)`: names and
        types only, so it stays short; the code itself is in the chunk."""
        types = ",".join(
            self._compact(p.child_by_field_name("type"))
            for p in node.children
            if p.type == "parameter"
        )
        words = [
            self._text(c) or ""
            for c in node.children
            if c.type in ("visibility", "state_mutability")
        ]
        modifiers = [
            (self._text(c.children[0]) or "")
            for c in node.children
            if c.type == "modifier_invocation"
        ]
        returns = next((c for c in node.children if c.type == "return_type_definition"), None)
        out = f"{name or kind}({types})"
        if words:
            out += " " + " ".join(words)
        if modifiers:
            out += " " + ",".join(modifiers)
        if returns is not None:
            out += (
                " returns("
                + ",".join(
                    self._compact(p.child_by_field_name("type"))
                    for p in returns.children
                    if p.type == "parameter"
                )
                + ")"
            )
        return out[:MAX_SIGNATURE_CHARS]


def _disagreements(tree: _Tree, regions: Sequence[Region]) -> list[str]:
    """Compare the parser's comment and string nodes with the scanner's regions, ignoring anything
    inside a part of the file the parser could not read."""

    def outside(start: int, end: int) -> bool:
        return not any(a <= start and end <= b for a, b in tree.unparsed)

    comments = {
        (r.start, r.end) for r in regions if r.kind == "comment" and outside(r.start, r.end)
    }
    strings = {(r.start, r.end) for r in regions if r.kind == "string" and outside(r.start, r.end)}
    problems = []
    if comments != {c for c in tree.comments if outside(*c)}:
        problems.append("the parser and the scanner disagree about comments")
    if strings != {s for s in tree.strings if outside(*s)}:
        problems.append("the parser and the scanner disagree about strings")
    return problems


# ---- the entry point ----------------------------------------------------------------------------


def lex(file_id: str, text: str) -> Lexed:
    """Strip one file and describe its definitions. Never raises on hostile text: whatever cannot
    be trusted ends up in `problems` and leaves `view` as None."""
    regions, problems = find_regions(text)
    problems.extend(check_raw(text, regions))
    line_count = text.count("\n") + 1
    if len(text) > MAX_FILE_CHARS:
        problems.append("the file is larger than the per-address cap")
    tree: _Tree | None = None
    if (
        not problems
    ):  # the parser is slow on an unclosed comment, and nothing is read from a refusal
        try:
            tree = _Tree(text, regions, file_id)
        except UnicodeEncodeError:
            problems.append("the text is not valid Unicode")
    if tree is not None:
        problems.extend(_disagreements(tree, regions))

    view = None
    if not problems:
        kept = {
            r.start
            for r in regions
            if r.kind == "string"
            and not r.unicode
            and tree is not None
            and r.start in tree.reasons
            and keepable(text[r.start + 1 : r.end - 1])
        }
        stripped = strip_text(text, regions, kept)
        problems.extend(check_view(stripped, text))
        if not problems:
            view = View(file_id=file_id, text=stripped, line_classes=line_classes(text, regions))
    return Lexed(
        file_id=file_id,
        line_count=line_count,
        view=view,
        problems=tuple(dict.fromkeys(problems)),
        definitions=tuple(tree.definitions) if tree else (),
        regions=regions,
        parse_errors=tree.parse_errors if tree else 0,
        assembly_blocks=tree.assembly_blocks if tree else 0,
    )


def definition_lines(view: View, definition: Definition) -> list[str]:
    """The view's lines for a definition, without the blank lines at its end."""
    lines = view.text.split("\n")[definition.start_line - 1 : definition.end_line]
    while lines and not lines[-1]:
        lines.pop()
    return lines
