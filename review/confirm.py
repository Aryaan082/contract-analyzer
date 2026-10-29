"""The blind confirmation pass (layer L8, decision 11) (R3).

Before a finding may be rated `high`, a second reviewer re-reads the definitions that hold a high
candidate. It is not told what the first found, it is a different model from a different company,
and it reads the code with every identifier renamed (`ID1`, `ID2`, ...) and without the outline, so
the same words do not reach both. The runner, not a model, decides whether they agree: the same
checklist entry, overlapping lines, the same function by the parser's reckoning, and the second
pass rates it high too.

Agreement is evidence, not proof: two models can share a mistake. Whether a confirmed finding
reaches `high_risk` is a separate switch (`review_may_set_high_risk`) that the validator checks.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from review.client import CallRecord, ReviewerClient, call_once, company_of
from review.known import Chunk, Piece, Split, plan_chunks
from review.lexer import Lexed
from review.pack import Checklist, build_prompt
from review.spans import Accepted, CallContext

# Words that keep their name when identifiers are renamed: the language's own vocabulary, so the
# second reader can still read the code (keywords, types, globals, members of builtin types, Yul).
_KEEP = frozenset(
    [
        "abstract",
        "after",
        "alias",
        "anonymous",
        "as",
        "assembly",
        "break",
        "calldata",
        "catch",
        "constant",
        "constructor",
        "continue",
        "contract",
        "delete",
        "do",
        "else",
        "emit",
        "enum",
        "error",
        "event",
        "external",
        "fallback",
        "false",
        "for",
        "from",
        "function",
        "global",
        "if",
        "immutable",
        "import",
        "indexed",
        "interface",
        "internal",
        "is",
        "layout",
        "library",
        "mapping",
        "memory",
        "modifier",
        "new",
        "override",
        "payable",
        "pragma",
        "private",
        "public",
        "pure",
        "receive",
        "return",
        "returns",
        "revert",
        "storage",
        "struct",
        "super",
        "this",
        "throw",
        "transient",
        "true",
        "try",
        "type",
        "unchecked",
        "using",
        "view",
        "virtual",
        "while",
        "require",
        "assert",
        "selfdestruct",
        "suicide",
        "gasleft",
        "msg",
        "block",
        "tx",
        "abi",
        "now",
        "keccak256",
        "sha256",
        "ripemd160",
        "ecrecover",
        "addmod",
        "mulmod",
        "address",
        "bool",
        "string",
        "bytes",
        "byte",
        "int",
        "uint",
        "fixed",
        "ufixed",
        "ether",
        "wei",
        "gwei",
        "finney",
        "szabo",
        "seconds",
        "minutes",
        "hours",
        "days",
        "weeks",
        "years",
        "unicode",
        "hex",
        "_",
        "sender",
        "value",
        "data",
        "sig",
        "origin",
        "gasprice",
        "timestamp",
        "number",
        "difficulty",
        "prevrandao",
        "chainid",
        "coinbase",
        "gaslimit",
        "basefee",
        "blobbasefee",
        "blockhash",
        "balance",
        "code",
        "codehash",
        "call",
        "delegatecall",
        "staticcall",
        "send",
        "transfer",
        "length",
        "push",
        "pop",
        "selector",
        "encode",
        "encodePacked",
        "encodeWithSelector",
        "encodeWithSignature",
        "encodeCall",
        "decode",
        "min",
        "max",
        "creationCode",
        "runtimeCode",
        "name",
        "interfaceId",
        "wrap",
        "unwrap",
        "slot",
        "offset",
        "stop",
        "add",
        "sub",
        "mul",
        "div",
        "sdiv",
        "mod",
        "smod",
        "exp",
        "not",
        "lt",
        "gt",
        "slt",
        "sgt",
        "eq",
        "iszero",
        "and",
        "or",
        "xor",
        "shl",
        "shr",
        "sar",
        "signextend",
        "pc",
        "mload",
        "mstore",
        "mstore8",
        "sload",
        "sstore",
        "msize",
        "gas",
        "selfbalance",
        "caller",
        "callvalue",
        "calldataload",
        "calldatasize",
        "calldatacopy",
        "codesize",
        "codecopy",
        "extcodesize",
        "extcodecopy",
        "returndatasize",
        "returndatacopy",
        "extcodehash",
        "create",
        "create2",
        "callcode",
        "invalid",
        "log0",
        "log1",
        "log2",
        "log3",
        "log4",
        "tload",
        "tstore",
        "mcopy",
        "blobhash",
        "let",
        "switch",
        "case",
        "default",
        "leave",
    ]
)
_TYPE_WORD = re.compile(
    r"^(u?int(8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)?|bytes([1-9]|[12][0-9]|3[0-2])?|u?fixed[0-9x]*)$"
)
_TOKEN = re.compile(
    r"\"(?:[^\"\\\n]|\\.)*\""
    r"|'(?:[^'\\\n]|\\.)*'"
    r"|0[xX][0-9a-fA-F_]+"
    r"|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"|(?P<id>[A-Za-z_$][A-Za-z0-9_$]*)"
)


def rename_identifiers(text: str, table: dict[str, str]) -> str:
    """The text with each identifier that is not the language's own replaced by `ID<n>`, numbered in
    order of first use. `table` is shared across the pieces of one call. Strings and numbers are
    left alone, and every line break stays where it was."""

    def replace(match: re.Match) -> str:
        word = match.group("id")
        if word is None or word in _KEEP or _TYPE_WORD.match(word):
            return match.group(0)
        if word not in table:
            table[word] = f"ID{len(table) + 1}"
        return table[word]

    return _TOKEN.sub(replace, text)


def rename_chunk(chunk: Chunk) -> Chunk:
    """The chunk as the second reader sees it: same files, same line numbers, renamed code."""
    table: dict[str, str] = {}
    pieces = tuple(
        Piece(
            file_id=p.file_id,
            definition="ID",  # never printed; the confirmation prompt has no outline
            start_line=p.start_line,
            end_line=p.end_line,
            text=rename_identifiers(p.text, table),
        )
        for p in chunk.pieces
    )
    return chunk.model_copy(update={"pieces": pieces})


def agree(first: Accepted, second: Accepted) -> bool:
    """The runner's test of agreement (never a model's)."""
    return (
        first.entry == second.entry
        and first.file_id == second.file_id
        and first.start_line <= second.end_line
        and second.start_line <= first.end_line
        and first.function == second.function
        and second.severity_claimed == "high"
    )


@dataclass
class Confirmation:
    agreed: dict[int, Accepted] = field(default_factory=dict)  # candidate index -> agreeing item
    calls: list[CallRecord] = field(default_factory=list)
    disagreed: int = 0  # candidates the second pass did not confirm
    not_read: int = 0  # candidates in a call that gave no usable reply
    skipped: str | None = None  # why no second pass was run, in fixed words

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)


def candidates_of(items: Sequence[Accepted]) -> list[int]:
    """Which accepted items ask to be rated high: the ones the confirmation pass is for."""
    return [i for i, item in enumerate(items) if item.severity_claimed == "high"]


def confirm(
    items: Sequence[Accepted],
    candidates: Sequence[int],
    *,
    split: Split,
    lexed: Mapping[str, Lexed],
    checklist: Checklist,
    second: ReviewerClient,
    first_model: str,
    max_tokens: int = 8192,
    spent_cap_usd: float | None = None,
) -> Confirmation:
    """Run the second pass over the definitions that hold the candidates and compare."""
    result = Confirmation()
    if not candidates:
        return result
    if company_of(second.name) == company_of(first_model):
        result.skipped = "the second reader is from the same company as the first"
        return result
    wanted = {
        id(d)
        for d in split.with_status("new")
        for i in candidates
        if _holds(d.definition, items[i])
    }
    sub = Split(items=tuple(d for d in split.with_status("new") if id(d) in wanted))
    chunks = plan_chunks(sub, lexed)
    second_items: list[tuple[Chunk, Accepted]] = []
    unread: list[Chunk] = []
    for chunk in chunks:
        if spent_cap_usd is not None and result.cost_usd >= spent_cap_usd:
            result.skipped = "the cost cap was reached before the second pass finished"
            unread.append(chunk)
            continue
        seen = rename_chunk(chunk)
        ctx = CallContext.for_chunk(chunk, lexed, shown=seen)
        prompt = build_prompt(checklist, seen, outline=None, total_parts=len(chunks))
        checked, record = call_once(
            second, prompt, ctx, checklist, purpose="confirm", max_tokens=max_tokens
        )
        result.calls.append(record)
        if checked.unreadable:
            unread.append(chunk)
        second_items.extend((chunk, a) for a in checked.accepted)
    for i in candidates:
        hit = next((a for _, a in second_items if agree(items[i], a)), None)
        if hit is not None:
            result.agreed[i] = hit
        elif any(_covers(c, items[i]) for c in unread):
            result.not_read += 1
        else:
            result.disagreed += 1
    return result


def _holds(definition, item: Accepted) -> bool:
    return (
        definition.file_id == item.file_id
        and definition.start_line <= item.start_line
        and item.end_line <= definition.end_line
    )


def _covers(chunk: Chunk, item: Accepted) -> bool:
    return any(
        p.file_id == item.file_id
        and p.start_line <= item.start_line
        and item.end_line <= p.end_line
        for p in chunk.pieces
    )
