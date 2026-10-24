"""The vulnerability taxonomy: hand-written entries, one TOML file each in `knowledge/taxonomy/`.

An entry is a class of problem (reentrancy, a blocked sell, an upgradeable proxy), not one bug; the
audit findings are the specific instances. Each entry says what the problem is and why it matters
to someone deciding whether to approve or use a contract, which tool results point to it (its
`signals`, in the words the tools themselves emit), where it can be seen at all (`visible_in`), and
what to look for in code.

The ID of an entry is `taxonomy:<file name without .toml>` and never changes. Every entry is
validated on load. Whether its signal words exist in the tools' vocabulary is checked by
`vocabulary.py`, so the two cannot drift apart.
"""

import re
import tomllib
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, Field, ValidationError, model_validator

from knowledge.models import SLUG_PATTERN, PlainText
from knowledge.paths import TAXONOMY_DIR

Group = Literal["token_tricks", "admin_power", "upgradeability", "code_bugs", "defi"]
# external: needs data the tools do not fetch today (holder lists, liquidity-lock records, who owns
# an admin address).
Visibility = Literal["tools", "bytecode", "source", "external"]

MAX_LOOK_FOR_WORDS = 250  # the checklist text of one entry
MAX_ENTRY_WORDS = 450  # description, checks, checklist and examples together
MAX_EXAMPLE_LINES = 12

# Where references may point: stable documentation and standards, never a page that could change
# hands. A test checks that each URL still answers (see knowledge/README.md).
REFERENCE_HOSTS = frozenset(
    {
        "eips.ethereum.org",
        "swcregistry.io",
        "docs.openzeppelin.com",
        "docs.soliditylang.org",
        "consensys.github.io",
        "docs.chain.link",
        "ethereum.org",
    }
)

# The signal grammar. A categorical signal is `<source>.<field>=<value>`; the others name a field
# the tools report without fixing a value.
_SIGNAL = re.compile(r"^[a-z_]+\.[a-z_0-9]+(=[a-z_0-9]+)?$")
_OPCODE = re.compile(r"^[A-Z][A-Z0-9]+$")

OPCODES = frozenset(
    [
        "STOP",
        "ADD",
        "MUL",
        "SUB",
        "DIV",
        "SDIV",
        "MOD",
        "SMOD",
        "ADDMOD",
        "MULMOD",
        "EXP",
        "SIGNEXTEND",
        "LT",
        "GT",
        "SLT",
        "SGT",
        "EQ",
        "ISZERO",
        "AND",
        "OR",
        "XOR",
        "NOT",
        "BYTE",
        "SHL",
        "SHR",
        "SAR",
        "KECCAK256",
        "ADDRESS",
        "BALANCE",
        "ORIGIN",
        "CALLER",
        "CALLVALUE",
        "CALLDATALOAD",
        "CALLDATASIZE",
        "CALLDATACOPY",
        "CODESIZE",
        "CODECOPY",
        "GASPRICE",
        "EXTCODESIZE",
        "EXTCODECOPY",
        "RETURNDATASIZE",
        "RETURNDATACOPY",
        "EXTCODEHASH",
        "BLOCKHASH",
        "COINBASE",
        "TIMESTAMP",
        "NUMBER",
        "PREVRANDAO",
        "GASLIMIT",
        "CHAINID",
        "SELFBALANCE",
        "BASEFEE",
        "BLOBHASH",
        "BLOBBASEFEE",
        "POP",
        "MLOAD",
        "MSTORE",
        "MSTORE8",
        "SLOAD",
        "SSTORE",
        "JUMP",
        "JUMPI",
        "PC",
        "MSIZE",
        "GAS",
        "JUMPDEST",
        "TLOAD",
        "TSTORE",
        "MCOPY",
        "PUSH0",
        "LOG0",
        "LOG1",
        "LOG2",
        "LOG3",
        "LOG4",
        "CREATE",
        "CALL",
        "CALLCODE",
        "RETURN",
        "DELEGATECALL",
        "CREATE2",
        "STATICCALL",
        "REVERT",
        "INVALID",
        "SELFDESTRUCT",
    ]
)


class TaxonomyError(Exception):
    """An entry cannot be loaded. The message names the file."""


def _words(text: str) -> int:
    return len(text.split())


def _reference_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in REFERENCE_HOSTS:
        raise ValueError(f"reference host {parsed.hostname!r} is not on the allowed list")
    return url


class Reference(BaseModel, frozen=True):
    title: PlainText = Field(min_length=3, max_length=150)
    url: Annotated[str, AfterValidator(_reference_url)]


class TaxonomyEntry(BaseModel, frozen=True):
    slug: str = Field(pattern=SLUG_PATTERN, max_length=60)  # from the file name
    title: PlainText = Field(min_length=3, max_length=80)
    group: Group
    summary: PlainText = Field(min_length=20, max_length=200)  # one line, for the index
    description: PlainText = Field(min_length=80)
    # The `identify` kinds this concerns, or ["any"]. A filter for search: a contract whose kind is
    # unknown is shown every entry.
    applies_to: tuple[str, ...] = Field(min_length=1)
    visible_in: tuple[Visibility, ...] = Field(min_length=1)
    signals: tuple[str, ...] = ()  # tool words that point here, e.g. "risk.kind=mint"
    opcodes: tuple[str, ...] = ()  # EVM opcodes that point here in bytecode
    what_to_check: PlainText = Field(min_length=40)  # next steps with the tools
    look_for_in_code: PlainText = Field(min_length=80)  # the checklist for reading code
    source_keywords: tuple[PlainText, ...] = ()  # optional hints; never a gate
    example_vulnerable: PlainText | None = None  # the shape of the risky code, never an exploit
    example_safe: PlainText | None = None
    references: tuple[Reference, ...] = ()

    @property
    def id(self) -> str:
        return f"taxonomy:{self.slug}"

    @model_validator(mode="after")
    def _checks(self) -> Self:
        if "tools" in self.visible_in and not self.signals:
            raise ValueError("visible in tools, but no signals say which tool result points here")
        for signal in self.signals:
            if not _SIGNAL.match(signal):
                raise ValueError(f"signal {signal!r} is not '<source>.<field>[=<value>]'")
        for opcode in self.opcodes:
            if opcode not in OPCODES:
                raise ValueError(f"{opcode!r} is not an EVM opcode name")
        if bool(self.example_vulnerable) != bool(self.example_safe):
            raise ValueError("give both example_vulnerable and example_safe, or neither")
        for example in (self.example_vulnerable, self.example_safe):
            if example and len(example.strip().splitlines()) > MAX_EXAMPLE_LINES:
                raise ValueError(f"an example is longer than {MAX_EXAMPLE_LINES} lines")
        if _words(self.look_for_in_code) > MAX_LOOK_FOR_WORDS:
            raise ValueError(f"look_for_in_code is over {MAX_LOOK_FOR_WORDS} words")
        if self.word_count > MAX_ENTRY_WORDS:
            raise ValueError(f"entry is {self.word_count} words, over {MAX_ENTRY_WORDS}")
        if len(set(self.signals)) != len(self.signals):
            raise ValueError("a signal is listed twice")
        return self

    @property
    def word_count(self) -> int:
        texts = (
            self.description,
            self.what_to_check,
            self.look_for_in_code,
            self.example_vulnerable or "",
            self.example_safe or "",
        )
        return sum(_words(t) for t in texts)


def load_entry(path: Path) -> TaxonomyEntry:
    try:
        raw = tomllib.loads(path.read_text())
        return TaxonomyEntry.model_validate({**raw, "slug": path.stem})
    except (tomllib.TOMLDecodeError, ValidationError, OSError) as err:
        detail = (
            "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors()[:5])
            if isinstance(err, ValidationError)
            else str(err)
        )
        raise TaxonomyError(f"{path.name}: {detail}") from None


@cache
def _load_directory(directory: Path) -> dict[str, TaxonomyEntry]:
    entries = {}
    for path in sorted(directory.glob("*.toml")):
        entry = load_entry(path)
        entries[entry.slug] = entry
    return entries


def load_taxonomy(directory: Path | None = None) -> dict[str, TaxonomyEntry]:
    """Every entry by slug, validated. A bad entry raises; it is never skipped."""
    return dict(_load_directory(directory or TAXONOMY_DIR))


def taxonomy_index(entries: dict[str, TaxonomyEntry] | None = None) -> list[tuple[str, str, str]]:
    """(id, title, one-line summary) for each entry: a few words per entry, cheap to keep in a
    prompt however many entries there are."""
    return [(e.id, e.title, e.summary) for e in (entries or load_taxonomy()).values()]


def entries_for_signals(
    signals: Iterable[str],
    kinds: Iterable[str] = (),
    entries: dict[str, TaxonomyEntry] | None = None,
) -> list[TaxonomyEntry]:
    """Entries whose signals include any of the given tool words, an exact lookup with no search.

    `kinds` are the contract's `identify` kinds. If given, an entry that applies only to other
    kinds is left out; if empty (kind unknown), nothing is filtered out.
    """
    wanted, have_kinds = set(signals), set(kinds)
    found = []
    for entry in (entries or load_taxonomy()).values():
        if not wanted & set(entry.signals):
            continue
        if have_kinds and "any" not in entry.applies_to and not have_kinds & set(entry.applies_to):
            continue
        found.append(entry)
    return found
