"""Dataset cards for source review: what is known about each test item, as labels and references.

A card never holds third-party source text. A contract on a chain is a reference (chain, address,
block); the runner fetches its source at run time through `review.bundle.fetch_source_bundle`. An
item of ours (a hostile fixture, a planted variant) is a path to a file we wrote.

The file format is one `[[card]]` table each (`evals/datasets/source_review/cards.toml`):

    id = "pl01"
    origin = "planted"             # incident | planted | benign | grey | hostile
    fixture = "evals/datasets/source_review/planted/P01_fee_recipient/vulnerable.sol"
    family = "pl01"                # items of one family always sit on one side of the split
    twin_of = "pl01-safe"          # the same code without the issue (or without the noise)
    renamed_of = "pl01"            # a deterministic identifier-renamed copy of another card
    split = "dev"                  # dev | held_out (fixed by split.py, recorded in split.toml)
    labelled = true                # false: nobody could draft a label from source and a write-up
    label_source = "authored:claude:2026-10-29"     # a URL for anything taken from a write-up
    reviewed_by = ""               # empty on every draft; "N of M labels unreviewed" in any results
    [[card.functions]]             # the labelled functions: one per (contract, function)
    contract = "Treasury"
    function = "setFeeRecipient"
    entries = ["taxonomy:missing-access-control"]

What a label means here, by origin:

* `incident` and `planted`: the labelled functions are where the flaw is. A flag on one of them is a
  hit; a flag on any other function of the item counts against precision.
* `benign` and `grey`: no function is labelled. Any flag rated medium or above is a false alarm for
  `benign`; for `grey` (legitimate but powerful) flags are reported apart. `note` records, in words,
  what a reader of the source found (powers by design, a guard that is easy to misread).
* `hostile`: a fixture of note 3.5 and its clean twin. The expected behaviour is written in
  `hostile.py`, not on the card.

Every label here is a draft: `reviewed_by` is empty until Aryaan has read it.
"""

import re
import tomllib
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from review.types import Address, EntryId

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "evals" / "datasets" / "source_review"
CARDS_PATH = DATASET_DIR / "cards.toml"

Origin = Literal["incident", "planted", "benign", "grey", "hostile"]
ORIGINS: tuple[Origin, ...] = ("incident", "planted", "benign", "grey", "hostile")
Side = Literal["dev", "held_out"]
# Material we wrote ourselves after reading the checklist can only be dev (note 6.5).
DEV_ONLY_ORIGINS: frozenset[str] = frozenset({"planted", "hostile"})
CHAIN_IDS = {"ethereum": 1, "base": 8453}

CardId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Name = Annotated[str, Field(pattern=r"^[A-Za-z_$][A-Za-z0-9_$]{0,63}$")]
Url = Annotated[str, Field(pattern=r"^https://[^\s]+$", max_length=500)]

AUTHORED_PREFIX = "authored:"
MAX_NOTE_CHARS = 600
# Pasted source would defeat "cards hold labels and references only"; this catches the obvious.
_CODE_LIKE = re.compile(
    r"pragma\s+solidity|SPDX-License|\b(function|modifier|constructor)\s*\w*\s*\([^)]*\)\s*"
    r"(public|external|internal|private|\{)|;\s*\n"
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class LabelledFunction(_Frozen):
    """One place where the flaw is: a function of a named contract and the checklist entries that
    describe it (more than one when two readings are both fair)."""

    contract: Name
    function: Name
    entries: Annotated[tuple[EntryId, ...], Field(min_length=1, max_length=3)]

    @property
    def key(self) -> tuple[str, str]:
        return (self.contract, self.function)


class ContractRef(_Frozen):
    """A contract on a chain at a pinned block. `address` is the address people would analyse;
    `logic_address` is what is fetched when that is a proxy (the implementation)."""

    chain: str = "ethereum"
    address: Address
    block: Annotated[int, Field(ge=0)]
    logic_address: Address | None = None

    @field_validator("address", "logic_address")
    @classmethod
    def _lower(cls, value: str | None) -> str | None:
        return value.lower() if value else value

    @field_validator("chain")
    @classmethod
    def _known_chain(cls, value: str) -> str:
        if value not in CHAIN_IDS:
            raise ValueError(f"unknown chain {value!r}; known: {', '.join(CHAIN_IDS)}")
        return value

    @property
    def chain_id(self) -> int:
        return CHAIN_IDS[self.chain]

    @property
    def fetch_address(self) -> str:
        return self.logic_address or self.address


class Card(_Frozen):
    id: CardId
    origin: Origin
    contract: ContractRef | None = None
    fixture: str | None = None  # repo-relative path of a file we wrote
    functions: tuple[LabelledFunction, ...] = ()
    twin_of: CardId | None = None
    renamed_of: CardId | None = None
    family: CardId
    split: Side = "dev"
    labelled: bool = False
    label_source: str = ""
    reviewed_by: str = ""
    group: str = ""  # Component 6's group for a contract it already holds (existing, famous, ...)
    component6_id: str = ""  # the id of the same contract in evals/datasets/contracts.toml
    note: Annotated[str, Field(max_length=MAX_NOTE_CHARS)] = ""
    unlabelled_reason: Annotated[str, Field(max_length=MAX_NOTE_CHARS)] = ""

    @field_validator("note", "unlabelled_reason")
    @classmethod
    def _no_pasted_source(cls, value: str) -> str:
        if _CODE_LIKE.search(value):
            raise ValueError("cards hold labels and references, never source text")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "Card":
        if (self.contract is None) == (self.fixture is None):
            raise ValueError("a card has either a contract reference or a fixture path")
        if self.origin in ("incident", "benign", "grey") and self.contract is None:
            raise ValueError(f"a {self.origin} card refers to a contract on a chain")
        if self.origin in ("planted", "hostile") and self.fixture is None:
            raise ValueError(f"a {self.origin} card refers to a fixture we wrote")
        if self.origin in DEV_ONLY_ORIGINS and self.split != "dev":
            raise ValueError(f"{self.origin} material is written after the checklist: dev only")
        if self.twin_of == self.id or self.renamed_of == self.id:
            raise ValueError("a card cannot be its own twin")
        if self.functions and self.origin not in ("incident", "planted"):
            raise ValueError("only incident and planted cards carry labelled functions")
        if self.labelled:
            self._check_source()
        else:
            if self.functions:
                raise ValueError("an unlabelled card has no labelled functions")
            if self.label_source:
                raise ValueError("an unlabelled card has no label source")
            if self.origin in ("incident", "planted") and not self.unlabelled_reason:
                raise ValueError("an unlabelled card says why")
        return self

    def _check_source(self) -> None:
        if self.origin in ("planted", "hostile"):
            if not self.label_source.startswith(AUTHORED_PREFIX):
                raise ValueError(f"our own material has label_source {AUTHORED_PREFIX}<who>:<date>")
        elif not re.match(r"^https://[^\s]+$", self.label_source):
            raise ValueError("a label taken from a write-up or the source names its URL")

    @property
    def unreviewed(self) -> bool:
        return not self.reviewed_by.strip()

    @property
    def key(self) -> str:
        """What the split hashes: chain and address for a contract (as Component 6), else the id."""
        if self.contract is not None:
            return f"{self.contract.chain}:{self.contract.address}"
        return f"fixture:{self.id}"

    @property
    def function_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset(f.key for f in self.functions)

    def entries_for(self, contract: str, function: str) -> frozenset[str]:
        out: set[str] = set()
        for f in self.functions:
            if f.key == (contract, function):
                out.update(f.entries)
        return frozenset(out)

    @property
    def fixture_path(self) -> Path | None:
        return REPO_ROOT / self.fixture if self.fixture else None


class CardSet:
    """All cards, with the cross-references checked. Iterates in file order."""

    def __init__(self, cards: list[Card]) -> None:
        ids = [c.id for c in cards]
        dupes = sorted(i for i, n in Counter(ids).items() if n > 1)
        if dupes:
            raise ValueError(f"duplicate card ids: {dupes}")
        self.cards = cards
        self._by_id = {c.id: c for c in cards}
        twins = {c.twin_of for c in cards if c.twin_of}
        for card in cards:
            if (
                card.labelled
                and card.origin in ("incident", "planted")
                and not card.functions
                and card.id not in twins
            ):
                raise ValueError(
                    f"{card.id}: a labelled incident or planted card names its functions"
                )
        for card in cards:
            for field_name in ("twin_of", "renamed_of"):
                target = getattr(card, field_name)
                if target is None:
                    continue
                other = self._by_id.get(target)
                if other is None:
                    raise ValueError(f"{card.id}: {field_name} names {target!r}, which is no card")
                if (other.family, other.split, other.origin) != (
                    card.family,
                    card.split,
                    card.origin,
                ):
                    raise ValueError(
                        f"{card.id}: a twin or renamed copy shares its original's family, split "
                        f"and origin ({other.id} does not)"
                    )

    def __iter__(self) -> Iterator[Card]:
        return iter(self.cards)

    def __len__(self) -> int:
        return len(self.cards)

    def get(self, card_id: str) -> Card:
        return self._by_id[card_id]

    def __contains__(self, card_id: object) -> bool:
        return card_id in self._by_id

    def of(self, *origins: str, split: str | None = None) -> list[Card]:
        return [
            c for c in self.cards if c.origin in origins and (split is None or c.split == split)
        ]

    def in_split(self, split: str) -> "CardSet":
        return CardSet([c for c in self.cards if c.split == split])

    def twin_of(self, card: Card) -> Card | None:
        """The twin card of `card` (the clean or safe code), if it has one."""
        return self._by_id[card.twin_of] if card.twin_of else None

    @property
    def twin_ids(self) -> frozenset[str]:
        """The ids of cards that are the clean or safe twin of another card."""
        return frozenset(c.twin_of for c in self.cards if c.twin_of)

    def renamed_copies(self, card: Card) -> list[Card]:
        return [c for c in self.cards if c.renamed_of == card.id]

    def unreviewed(self) -> tuple[int, int]:
        """(cards nobody has reviewed, all cards)."""
        return sum(c.unreviewed for c in self.cards), len(self.cards)

    def unreviewed_labels(self) -> tuple[int, int]:
        """(drafted labels nobody has reviewed, drafted labels). A card with no draft label has
        nothing to review; Aryaan reviews ten before any number is quoted (decision 16)."""
        drafted = [c for c in self.cards if c.labelled]
        return sum(c.unreviewed for c in drafted), len(drafted)

    def labelled(self) -> tuple[int, int]:
        """(cards with a drafted label, all cards)."""
        return sum(c.labelled for c in self.cards), len(self.cards)

    def counts(self) -> dict[str, int]:
        return dict(Counter(c.origin for c in self.cards))


def load_cards(path: Path = CARDS_PATH) -> CardSet:
    data = tomllib.loads(path.read_text())
    rows = data.get("card", [])
    try:
        return CardSet([Card.model_validate(row) for row in rows])
    except ValidationError as err:
        raise ValueError(f"{path.name}: {err}") from None


def problems(cards: CardSet, root: Path = REPO_ROOT) -> list[str]:
    """Things wrong with a card file that loading alone does not catch, in plain words."""
    out = []
    for card in cards:
        if card.fixture and not (root / card.fixture).is_file():
            out.append(f"{card.id}: fixture {card.fixture} does not exist")
        if card.twin_of and card.origin in ("incident", "benign", "grey"):
            out.append(f"{card.id}: only our own material has twins")
    return out
