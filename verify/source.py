"""Showing the code of a flagged place to a model: the function it names, numbered, and an outline
of the other files. The raw source is used, as the explorer published it; it is untrusted text and
every caller puts it inside fences (`review.pack.fence`)."""

from collections.abc import Mapping, Sequence

from review.lexer import lex
from verify.types import Candidate

FUNCTION_LIKE = frozenset({"function", "modifier", "constructor", "receive", "fallback"})
CONTEXT_LINES = 3  # lines either side when the function cannot be found by name
MAX_FUNCTION_CHARS = 12_000
MAX_OUTLINE_CHARS = 8_000
MAX_BASE_CHARS = 60_000


def numbered(text: str, first: int, last: int) -> str:
    lines = text.split("\n")
    first, last = max(first, 1), min(last, len(lines))
    return "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1))


_COMMENT_START = ("//", "/*", "*")


def comment_start(text: str, line: int) -> int:
    """The first line of the comment block (NatSpec, `//`, `/* */`) that sits directly above `line`,
    or `line` when there is none. A function's comments are where a documented design choice lives,
    and the parser's member starts at the `function` keyword, below them."""
    lines = text.split("\n")
    first = line
    while first > 1 and lines[first - 2].strip().startswith(_COMMENT_START):
        first -= 1
    return first


def function_lines(text: str, candidate: Candidate) -> tuple[int, int]:
    """The first and last line of the function the candidate names (with the comments directly above
    it), or the candidate's own lines with a little context when the parser does not find it."""
    for definition in lex("F1", text).definitions:
        if candidate.contract and definition.name != candidate.contract:
            continue
        for member in definition.members:
            if (
                member.name == candidate.function
                and member.kind in FUNCTION_LIKE
                and member.has_body
                and member.start_line <= candidate.end_line
                and member.end_line >= candidate.start_line
            ):
                return comment_start(text, member.start_line), member.end_line
    return candidate.start_line - CONTEXT_LINES, candidate.end_line + CONTEXT_LINES


def function_source(files: Mapping[str, str], candidate: Candidate) -> str:
    """The flagged function with line numbers, cut at `MAX_FUNCTION_CHARS`; '' if the file is not
    in the bundle."""
    text = files.get(candidate.file)
    if text is None:
        return ""
    first, last = function_lines(text, candidate)
    return numbered(text, first, last)[:MAX_FUNCTION_CHARS]


def numbered_files(files: Sequence[tuple[str, str]], max_chars: int = MAX_BASE_CHARS) -> str:
    """Each (path, text) as a header and its numbered lines, cut at `max_chars`."""
    return "\n\n".join(
        f"=== {path} ===\n{numbered(text, 1, text.count(chr(10)) + 1)}" for path, text in files
    )[:max_chars]


def outline(files: Mapping[str, str], *, skip: str = "") -> str:
    """Every file but `skip`: its path and the signature of each member, cut at
    `MAX_OUTLINE_CHARS`. Enough to import and call another contract; not its code."""
    out: list[str] = []
    for path, text in files.items():
        if path == skip:
            continue
        out.append(f"{path}")
        for definition in lex("F1", text).definitions:
            out.append(f"  {definition.kind} {definition.name}")
            for m in definition.members:
                if m.kind in FUNCTION_LIKE:
                    out.append(f"    {m.signature or m.name}")
    return "\n".join(out)[:MAX_OUTLINE_CHARS]


def inheritance_files(
    files: Mapping[str, str], candidate: Candidate, *, max_chars: int = MAX_BASE_CHARS
) -> list[tuple[str, str]]:
    """The files that define the contracts the flagged contract inherits from, nearest first, as
    (path, text); never the flagged file itself. A flaw in a function usually depends on the state
    and the modifiers of its bases, which an outline of signatures does not show. Whole files are
    taken in order until `max_chars`; one that does not fit is left out (the outline still lists
    it). A base that is not in the bundle (a library's) is ignored. With no contract named, every
    contract of the flagged file counts."""
    lexed = {path: lex(f"F{i}", text) for i, (path, text) in enumerate(files.items(), 1)}
    where: dict[str, tuple[str, list[str]]] = {}
    for path, lx in lexed.items():
        for d in lx.definitions:
            if d.kind != "declaration":
                where.setdefault(d.name, (path, list(d.bases)))
    own = lexed.get(candidate.file)
    names = [d.name for d in own.definitions if d.kind == "contract"] if own else []
    start = [candidate.contract] if candidate.contract in where else names
    queue, seen, out, used = list(start), set(start), [], 0
    while queue:
        name = queue.pop(0)
        path, bases = where.get(name, ("", []))
        for base in bases:
            if base in seen or base not in where:
                continue
            seen.add(base)
            queue.append(base)
            base_path = where[base][0]
            if base_path == candidate.file or any(base_path == p for p, _ in out):
                continue
            text = files[base_path]
            if used + len(text) <= max_chars:
                out.append((base_path, text))
                used += len(text)
    return out
