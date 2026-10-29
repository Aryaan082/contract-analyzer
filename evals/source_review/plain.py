"""Arm P of the head-to-head: the same model read the way a person would use it, with none of our
pipeline.

One request per contract (more only if the raw source is too large for one), the RAW source exactly
as the explorer published it (comments, strings and imported libraries included), a short neutral
instruction, and a reply in a JSON shape the scorer can read. There is no checklist, no stripping,
no scan, no span check and no second pass. The prompt is below, in full, so it can be judged fair:

    system  PLAIN_SYSTEM
    user    PLAIN_INSTRUCTION, then every file as `=== file: <path> ===` and its lines numbered

Choices that keep it fair rather than sandbagged:

* Lines are numbered. Without numbers no model can give a line range, and the instruction asks for
  one. This is the only change to the source and it is the same numbering arm A sees.
* The files carry their real paths, as a person would paste them. (Arm A sees file ids instead; the
  runner gives both arms the same neutral path for our own fixtures, so a file name never says
  `vulnerable`.)
* The reply is requested with the same JSON-schema mechanism arm A uses (`review.client`), at
  temperature 0, with the same output limit and the same cost accounting.

How a reply is read, in fixed rules (nothing is guessed in the model's favour or against it):

* Text that is not JSON of the expected shape, or a reply cut off at the output limit, counts as NO
  findings and is reported as a format failure. A fenced code block around the JSON is tolerated.
* At most `MAX_FINDINGS` items are read. An item with a missing or wrong-typed field, or a severity
  other than low, medium or high, is dropped and counted by reason.
* An item is placed in a (contract, function) by its own names if they name a real function of the
  named file (the contract may be left out when only one contract in the file has that function);
  otherwise by its line range, with the parser, the way arm A's findings are placed. An item that
  cannot be placed counts against precision, as in `scoring.run_counts`.
* The model's descriptions are never stored or shown: only names, lines and severity.
"""

import json
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from evals.source_review import costs
from evals.source_review.scoring import Flag
from review.client import ModelUnavailable, ReviewerCall, ReviewerClient
from review.lexer import Lexed, Member, lex
from review.pack import Prompt

PLAIN_SYSTEM = "You are an experienced smart-contract security auditor."
PLAIN_INSTRUCTION = (
    "Review the Solidity source below for security vulnerabilities. List the vulnerabilities you "
    "find. For each one give the file, the contract and function it is in, the line range, a "
    "severity (low, medium or high) and one sentence saying what is wrong. Report real "
    "vulnerabilities only; leave out gas optimisations and style comments. If you find none, "
    'return an empty list.\n\nReply with JSON only, in this shape: {"findings": [{"file": "...", '
    '"contract": "...", "function": "...", "start_line": 1, "end_line": 1, "severity": "medium", '
    '"description": "..."}]}\n\nThe source files follow, with line numbers.'
)
PLAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "contract": {"type": "string"},
                    "function": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "description": {"type": "string"},
                },
                "required": ["file", "function", "start_line", "end_line", "severity"],
            },
        }
    },
    "required": ["findings"],
}
ENTRY = "plain"  # a plain finding names no checklist entry
MAX_FINDINGS = 50
MAX_NAME_CHARS = 120
MAX_INPUT_TOKENS = 250_000  # estimated; one request holds the whole contract below this
DEFAULT_MAX_TOKENS = 8192  # the same output limit as arm A's calls
FUNCTION_LIKE = frozenset({"function", "modifier", "constructor", "receive", "fallback"})
_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)
_SEVERITIES = ("low", "medium", "high")


# ---- the prompt ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    path: str
    first: int  # 1-based first line shown
    last: int
    text: str  # the lines first..last, as published
    whole: bool = True  # the piece is the whole file


def _pieces(files: Sequence[tuple[str, str]], max_chars: int) -> list[Piece]:
    """Whole files, except one longer than `max_chars`, which is cut at line boundaries."""
    out = []
    for path, text in files:
        lines = text.split("\n")
        if len(text) <= max_chars:
            out.append(Piece(path, 1, len(lines), text))
            continue
        start, size = 0, 0
        for i, line in enumerate(lines):
            if size + len(line) + 1 > max_chars and i > start:
                out.append(Piece(path, start + 1, i, "\n".join(lines[start:i]), False))
                start, size = i, 0
            size += len(line) + 1
        out.append(Piece(path, start + 1, len(lines), "\n".join(lines[start:]), False))
    return out


def _shown(piece: Piece) -> str:
    head = f"=== file: {piece.path} ==="
    if not piece.whole:
        head += f" (lines {piece.first} to {piece.last})"
    body = "\n".join(
        f"{piece.first + i:>5}| {line}" for i, line in enumerate(piece.text.split("\n"))
    )
    return f"{head}\n{body}"


def plain_prompts(
    files: Sequence[tuple[str, str]], *, max_input_tokens: int = MAX_INPUT_TOKENS
) -> list[Prompt]:
    """The requests for one contract: one, unless its raw source is over `max_input_tokens`, in
    which case whole files are packed into parts (a single file over the limit is cut by lines)."""
    max_chars = int(max_input_tokens * costs.CHARS_PER_TOKEN)
    parts: list[list[Piece]] = [[]]
    size = 0
    for piece in _pieces(files, max_chars):
        if parts[-1] and size + len(piece.text) > max_chars:
            parts.append([])
            size = 0
        parts[-1].append(piece)
        size += len(piece.text)
    out = []
    for k, part in enumerate(parts, 1):
        note = (
            f"\n\nThis is part {k} of {len(parts)} of a larger codebase; the other parts are "
            "reviewed separately."
            if len(parts) > 1
            else ""
        )
        shown = "\n\n".join(_shown(p) for p in part)
        out.append(Prompt(system=PLAIN_SYSTEM, user=f"{PLAIN_INSTRUCTION}{note}\n\n{shown}"))
    return out


# ---- reading a reply -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    file: str
    contract: str | None
    function: str
    start_line: int
    end_line: int
    severity: str


def parse_reply(
    text: str | None, finish_reason: str | None
) -> tuple[list[Item], list[str], Counter]:
    """(items, format failures, dropped items by reason). A reply that is not usable gives no
    items and one failure in fixed words; it never raises."""
    dropped: Counter = Counter()
    if finish_reason == "length":
        return [], ["reply cut off at the output limit"], dropped
    if text is None or not text.strip():
        return [], ["no text in the reply"], dropped
    body = text.strip()
    if fenced := _FENCE.match(body):
        body = fenced.group(1)
    try:
        data = json.loads(body)
    except ValueError:
        return [], ["reply is not JSON"], dropped
    raw = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return [], ["reply has no findings list"], dropped
    items = []
    for entry in raw[:MAX_FINDINGS]:
        item = _item(entry)
        if isinstance(item, str):
            dropped[item] += 1
        else:
            items.append(item)
    if len(raw) > MAX_FINDINGS:
        dropped["over_limit"] += len(raw) - MAX_FINDINGS
    return items, [], dropped


def _item(entry: Any) -> Item | str:
    if not isinstance(entry, dict):
        return "not_an_object"
    file, function, contract = entry.get("file"), entry.get("function"), entry.get("contract")
    start, end = entry.get("start_line"), entry.get("end_line")
    if not isinstance(file, str) or not isinstance(function, str):
        return "bad_field"
    if contract is not None and not isinstance(contract, str):
        return "bad_field"
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in (start, end)):
        return "bad_field"
    if entry.get("severity") not in _SEVERITIES:
        return "bad_severity"
    return Item(
        file=file[:MAX_NAME_CHARS],
        contract=(contract or None) and contract[:MAX_NAME_CHARS],
        function=function[:MAX_NAME_CHARS],
        start_line=max(1, min(start, end)),
        end_line=max(1, max(start, end)),
        severity=entry["severity"],
    )


# ---- placing an item in a contract and a function -----------------------------------------------


def _members(lexed: Lexed) -> list[tuple[str, Member]]:
    return [
        (d.name, m)
        for d in lexed.definitions
        if d.kind in ("contract", "library", "interface")
        for m in d.members
        if m.kind in FUNCTION_LIKE and m.name
    ]


def _file_of(name: str, lexed: dict[str, Lexed]) -> str | None:
    if name in lexed:
        return name
    tail = name.rsplit("/", 1)[-1]
    same = [p for p in lexed if p.rsplit("/", 1)[-1] == tail]
    if len(same) == 1:
        return same[0]
    return next(iter(lexed)) if len(lexed) == 1 else None


def place(item: Item, lexed: dict[str, Lexed]) -> Flag:
    """The finding as a `scoring.Flag`. `function` is None when it could not be placed."""
    path = _file_of(item.file, lexed)
    contract = function = None
    if path is not None:
        members = _members(lexed[path])
        named = [(c, m) for c, m in members if m.name == item.function]
        if item.contract is not None:
            named = [(c, m) for c, m in named if c == item.contract] or []
        if named and len({c for c, _ in named}) == 1:
            contract, function = named[0][0], item.function
        else:
            holding = [
                (c, m)
                for c, m in members
                if m.start_line <= item.start_line and item.end_line <= m.end_line
            ]
            if holding:
                contract, member = min(holding, key=lambda cm: cm[1].end_line - cm[1].start_line)
                function = member.name
    return Flag(
        contract=contract,
        function=function,
        entry=ENTRY,
        severity=item.severity,
        confidence="medium",
        confirmed=False,
        file_id=path or "",
        start_line=item.start_line,
        end_line=item.end_line,
    )


# ---- one contract --------------------------------------------------------------------------------


@dataclass
class PlainRun:
    flags: tuple[Flag, ...] = ()
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    failures: list[str] = field(default_factory=list)  # reply-format failures, in fixed words
    dropped: Counter = field(default_factory=Counter)
    unavailable: bool = False  # a request could not be answered: the contract is NOT scored
    parts: int = 0


def run_plain(
    files: Sequence[tuple[str, str]],
    client: ReviewerClient,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_input_tokens: int = MAX_INPUT_TOKENS,
    on_call: Callable[[ReviewerCall], None] | None = None,
) -> PlainRun:
    """Arm P on one contract. A request the service refuses ends the run with `unavailable` set; a
    reply that is unusable adds a failure and no findings."""
    lexed = {path: lex(f"F{i}", text) for i, (path, text) in enumerate(files, 1)}
    prompts = plain_prompts(files, max_input_tokens=max_input_tokens)
    run = PlainRun(parts=len(prompts))
    flags: list[Flag] = []
    for prompt in prompts:
        try:
            reply = client.review(prompt, PLAIN_SCHEMA, max_tokens=max_tokens)
        except ModelUnavailable:
            run.unavailable = True
            break
        run.calls += 1
        run.prompt_tokens += reply.prompt_tokens
        run.completion_tokens += reply.completion_tokens
        run.cost_usd += reply.cost_usd
        if on_call:
            on_call(reply)
        items, failures, dropped = parse_reply(reply.text, reply.finish_reason)
        run.failures += failures
        run.dropped.update(dropped)
        flags += [place(i, lexed) for i in items]
    run.flags = tuple(dict.fromkeys(flags))  # the same finding twice (overlapping parts) is one
    return run
