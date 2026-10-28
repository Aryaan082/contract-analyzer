"""The dev / held-out split and the tool-use set: fixed by rule, reproducible from the cards.

The split rule (also in `label_rules.toml`): within each class, units are ordered by the SHA-256 of
`chain:address`; a unit is a code family (all members of one family sit on one side); the first
third, rounded to the nearest whole unit, is held out. Contracts in `dev_only` are always dev, and
a family containing one of them is dev as a whole. A family that appears in two classes keeps the
side it was given the first time (classes are taken in a fixed order).
"""

import math
from dataclasses import dataclass

from evals.collect.scan import split_key

CLASS_ORDER = ("flagged", "benign", "grey", "beyond_reach")


@dataclass(frozen=True)
class Item:
    id: str
    klass: str
    chain: str
    address: str
    family: str  # code hash; empty if unknown (the item is then its own family)
    dev_only: bool = False

    @property
    def key(self) -> str:
        return split_key(self.chain, self.address)

    @property
    def unit(self) -> str:
        return self.family or f"single:{self.id}"


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def assign(items: list[Item], heldout_share: float) -> dict[str, str]:
    """Maps item id to "dev" or "heldout"."""
    side: dict[str, str] = {}  # unit -> side
    for item in items:
        if item.dev_only:
            side[item.unit] = "dev"
    for klass in CLASS_ORDER:
        units: dict[str, list[Item]] = {}
        for item in items:
            if item.klass == klass and item.unit not in side:
                units.setdefault(item.unit, []).append(item)
        ordered = sorted(units, key=lambda u: min(m.key for m in units[u]))
        held = round_half_up(len(ordered) * heldout_share)
        for position, unit in enumerate(ordered):
            side[unit] = "heldout" if position < held else "dev"
    return {item.id: side.get(item.unit, "dev") for item in items}


# The trajectories the tool-use set must cover, with how many of each (they add up to 20).
TOOL_USE_QUOTAS = (
    ("proxy", 3),
    ("official", 2),
    ("not_a_token", 2),
    ("honeypot", 4),
    ("powers", 3),
    ("benign_token", 4),
    ("beyond_reach", 2),
)


def trajectory_of(klass: str, traits: tuple[str, ...], reasons: tuple[str, ...]) -> str:
    if "proxy" in traits:
        return "proxy"
    if "official" in traits:
        return "official"
    if "not_a_token" in traits:
        return "not_a_token"
    if klass == "beyond_reach":
        return "beyond_reach"
    if klass == "flagged" and "honeypot-sell-blocked" in reasons:
        return "honeypot"
    if klass == "flagged":
        return "powers"
    return "benign_token"


def tool_use_set(rows: list[tuple[str, str, str]], size: int) -> set[str]:
    """Pick the tool-use set from dev contracts. `rows` are (id, trajectory, split key). From each
    trajectory the first contracts in split-key order up to its quota; if a trajectory has too few,
    the gap is filled with the next unpicked contracts in key order. Always `size` or all."""
    quota = dict(TOOL_USE_QUOTAS)
    by_group: dict[str, list[tuple[str, str]]] = {}
    for ident, group, key in rows:
        by_group.setdefault(group, []).append((key, ident))
    chosen: list[str] = []
    for group, count in quota.items():
        chosen += [i for _, i in sorted(by_group.get(group, []))[:count]]
    if len(chosen) < size:
        picked = set(chosen)
        rest = sorted((key, ident) for ident, _, key in rows if ident not in picked)
        chosen += [ident for _, ident in rest][: size - len(chosen)]
    return set(chosen[:size])
