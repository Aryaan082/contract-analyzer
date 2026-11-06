"""Showing the code of a flagged place to a model: the function it names, numbered, and an outline
of the other files. The raw source is used, as the explorer published it; it is untrusted text and
every caller puts it inside fences (`review.pack.fence`)."""

from collections.abc import Mapping

from review.lexer import lex
from verify.types import Candidate

FUNCTION_LIKE = frozenset({"function", "modifier", "constructor", "receive", "fallback"})
CONTEXT_LINES = 3  # lines either side when the function cannot be found by name
MAX_FUNCTION_CHARS = 12_000
MAX_OUTLINE_CHARS = 8_000


def numbered(text: str, first: int, last: int) -> str:
    lines = text.split("\n")
    first, last = max(first, 1), min(last, len(lines))
    return "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1))


def function_lines(text: str, candidate: Candidate) -> tuple[int, int]:
    """The first and last line of the function the candidate names, or the candidate's own lines
    with a little context when the parser does not find it."""
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
                return member.start_line, member.end_line
    return candidate.start_line - CONTEXT_LINES, candidate.end_line + CONTEXT_LINES


def function_source(files: Mapping[str, str], candidate: Candidate) -> str:
    """The flagged function with line numbers, cut at `MAX_FUNCTION_CHARS`; '' if the file is not
    in the bundle."""
    text = files.get(candidate.file)
    if text is None:
        return ""
    first, last = function_lines(text, candidate)
    return numbered(text, first, last)[:MAX_FUNCTION_CHARS]


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
