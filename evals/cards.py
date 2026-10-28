"""Cards: what is known about each test contract, in a TOML file the scorers read.

A card says which class a contract is in, why, and what a good run looks like. Cards are the labels.
For now they are computed by script and nobody has read them (`reviewed_by` is empty), and every
results file says how many are unreviewed. The file format (one `[[contract]]` table each):

    id = "groklet"
    chain = "ethereum"
    address = "0x656b..."
    block = 26280500
    class = "flagged"            # flagged | benign | grey | beyond_reach
    split = "dev"                # dev | heldout
    reasons = ["honeypot-sell-blocked"]       # why it is flagged (taxonomy ids)
    traits = ["token"]           # proxy | token | not_a_token | official
    implementation = "0x..."     # a proxy's implementation, if known
    acceptable_verdicts = ["high_risk"]       # default by class if left out
    requires = ["simulate_sell"] # tools a good run must call, besides the derived ones
    forbids = []                 # tools it must not call
    facts = ["sell.*(revert|block|fail)"]     # regexes the report must match (reason recall)
    must_not_say = ["\\\\bCircle\\\\b"]       # regexes the report must not match
    min_not_checked = 3          # a grey contract's report must list at least this many gaps
    label_source = "script:outcome-v1"
    reviewed_by = ""

`flagged` and `benign` are scored on the verdict and in precision and recall. `grey` (legitimate
but powerful: USDC, Seaport) is not scored on the verdict, only on its citations, its not-checked
list and what it must not say. `beyond_reach` (bugs in a protocol's logic, which the tools cannot
see) is reported but not scored.
"""

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.report import Verdict
from evals.runs import RunRecord

Klass = Literal["flagged", "benign", "grey", "beyond_reach"]
SCORED = ("flagged", "benign")
WARNING_VERDICTS = ("elevated_risk", "high_risk")


class Card(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str
    address: str
    chain: str = "ethereum"
    block: int | None = None
    klass: Klass = Field(alias="class")
    split: Literal["dev", "heldout"] = "dev"
    reasons: tuple[str, ...] = ()
    traits: tuple[str, ...] = ()
    implementation: str | None = None
    acceptable_verdicts: tuple[Verdict, ...] = ()
    requires: tuple[str, ...] = ()
    forbids: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    must_not_say: tuple[str, ...] = ()
    min_not_checked: int = 0
    label_source: str = "hand"
    reviewed_by: str = ""

    @field_validator("address", "implementation")
    @classmethod
    def _lower(cls, value: str | None) -> str | None:
        return value.lower() if value else value

    @field_validator("facts", "must_not_say")
    @classmethod
    def _patterns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in values:
            try:
                re.compile(pattern)  # a bad pattern fails when the file is read, not mid-score
            except re.error as err:
                raise ValueError(f"not a regular expression: {pattern!r} ({err})") from None
        return values

    @property
    def acceptable(self) -> tuple[str, ...]:
        """The verdicts that count as right: the card's own list, else by class."""
        if self.acceptable_verdicts:
            return tuple(self.acceptable_verdicts)
        if self.klass == "flagged":
            return WARNING_VERDICTS
        if self.klass == "benign":
            return ("no_red_flags_found",)
        return ()

    @property
    def scored(self) -> bool:
        return self.klass in SCORED

    @property
    def unreviewed(self) -> bool:
        return not self.reviewed_by.strip()

    def matches(self, run: RunRecord) -> bool:
        return (
            run.chain == self.chain
            and run.address == self.address
            and (self.block is None or run.block == self.block)
        )


class CardSet:
    def __init__(self, cards: list[Card]) -> None:
        ids = [c.id for c in cards]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate card ids: {sorted({i for i in ids if ids.count(i) > 1})}")
        self.cards = cards

    def __iter__(self):
        return iter(self.cards)

    def __len__(self) -> int:
        return len(self.cards)

    def find(self, run: RunRecord) -> Card | None:
        return next((c for c in self.cards if c.matches(run)), None)

    def in_split(self, split: str) -> "CardSet":
        return CardSet([c for c in self.cards if c.split == split])

    def unreviewed(self) -> tuple[int, int]:
        """(cards nobody has reviewed, all cards)."""
        return sum(c.unreviewed for c in self.cards), len(self.cards)


def load_cards(path: Path) -> CardSet:
    data = tomllib.loads(path.read_text())
    return CardSet([Card.model_validate(row) for row in data.get("contract", [])])
