"""The dev / held-out split of approach note 6.5: fixed by rule, written to a file before any run.

The rule, the same as Component 6's: within each origin, units (a family, all its members together)
are ordered by the SHA-256 of an item key, and the first third, rounded to the nearest whole unit,
is held out. Three exceptions, each written in `split.toml`:

* material we wrote ourselves after reading the checklist (`planted`, `hostile`) is dev only;
* a contract Component 6 already holds as one of its eight `existing` contracts is dev only, and a
  family containing one is dev as a whole (Component 6's own rule);
* a family that appears under two origins is split per origin, so the rule never mixes them.

`split.toml` is the record. `python -m evals.source_review split` checks it against the cards and
never rewrites an existing file; the test does the same. With about 50 items the held-out third is a
sanity check, not a measuring instrument, and it gets totals only, at declared looks.
"""

import hashlib
import math
import tomllib
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from evals.source_review.cards import (
    DATASET_DIR,
    DEV_ONLY_ORIGINS,
    ORIGINS,
    Card,
    CardSet,
)

SPLIT_PATH = DATASET_DIR / "split.toml"
HELDOUT_SHARE = 1 / 3
LOOK_BUDGET = 3  # held-out looks, declared in evals/results.md (note 6.5)
RULE = (
    "Within each origin, units (a family, whole) ordered by SHA-256 of the item key "
    "(chain:address for a contract, 'fixture:<id>' otherwise); the first third, rounded half up, "
    "is held out. planted and hostile are dev only; a family holding one of Component 6's "
    "'existing' contracts is dev as a whole."
)


def item_key_hash(card: Card) -> str:
    """The hash that orders units. For a contract it equals Component 6's `split_key`."""
    return hashlib.sha256(card.key.encode()).hexdigest()


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def dev_only(card: Card) -> bool:
    return card.origin in DEV_ONLY_ORIGINS or card.group == "existing"


def assign(cards: Sequence[Card], share: float = HELDOUT_SHARE) -> dict[str, str]:
    """Maps card id to "dev" or "held_out". Pure: the order of `cards` does not matter."""
    out: dict[str, str] = {}
    for origin in ORIGINS:
        members = [c for c in cards if c.origin == origin]
        units: dict[str, list[Card]] = defaultdict(list)
        for card in members:
            units[card.family].append(card)
        pinned = {u for u, cs in units.items() if any(dev_only(c) for c in cs)}
        ordered = sorted(
            (u for u in units if u not in pinned),
            key=lambda u: (min(item_key_hash(c) for c in units[u]), u),
        )
        held = round_half_up(len(ordered) * share)
        side = {u: "dev" for u in pinned}
        side.update({u: "held_out" if i < held else "dev" for i, u in enumerate(ordered)})
        for unit, unit_cards in units.items():
            for card in unit_cards:
                out[card.id] = side[unit]
    return out


def render(sides: dict[str, str], cards: CardSet) -> str:
    """The text of `split.toml`: the rule, the counts, and every card's side."""
    held = sorted(i for i, s in sides.items() if s == "held_out")
    dev = sorted(i for i, s in sides.items() if s == "dev")
    per_origin = {
        o: sum(1 for c in cards if c.origin == o and sides[c.id] == "held_out") for o in ORIGINS
    }
    lines = [
        "# The source-review split (approach note 6.5). Written once, before any run.",
        "# `python -m evals.source_review split` checks it against cards.toml, never rewrites it.",
        "# Adding cards means a new split generation: say so in evals/results.md first.",
        f"rule = {_q(RULE)}",
        f"look_budget = {LOOK_BUDGET}",
        "",
        "[counts]",
        f"dev = {len(dev)}",
        f"held_out = {len(held)}",
        *(f"held_out_{o} = {n}" for o, n in per_origin.items()),
        "",
        "[sides]",
        f"held_out = {_array(held)}",
        f"dev = {_array(dev)}",
        "",
    ]
    return "\n".join(lines)


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _array(items: Sequence[str]) -> str:
    if not items:
        return "[]"
    return "[\n" + "".join(f"  {_q(i)},\n" for i in items) + "]"


def read_split(path: Path = SPLIT_PATH) -> dict[str, str]:
    data = tomllib.loads(path.read_text())
    sides = data["sides"]
    out = {i: "held_out" for i in sides["held_out"]}
    out.update({i: "dev" for i in sides["dev"]})
    if len(out) != len(sides["held_out"]) + len(sides["dev"]):
        raise ValueError("a card is on both sides of the split")
    return out


def check(cards: CardSet, path: Path = SPLIT_PATH) -> list[str]:
    """Problems between the cards, the rule and the written file, in plain words."""
    recomputed = assign(cards.cards)
    problems = [
        f"{c.id}: the card says {c.split}, the rule says {recomputed[c.id]}"
        for c in cards
        if c.split != recomputed[c.id]
    ]
    if not path.exists():
        return [*problems, f"{path.name} has not been written"]
    written = read_split(path)
    problems += [
        f"{i}: in the file but not in the cards" for i in sorted(set(written) - set(recomputed))
    ]
    problems += [
        f"{i}: in the cards but not in the file" for i in sorted(set(recomputed) - set(written))
    ]
    problems += [
        f"{i}: the file says {written[i]}, the rule says {recomputed[i]}"
        for i in sorted(set(written) & set(recomputed))
        if written[i] != recomputed[i]
    ]
    return problems
