"""Which contracts the noise study runs on: a rule fixed before any run, applied by code.

The study asks how much one contract's result changes between runs of the same agent. It needs a
handful of contracts, and the choice must not depend on how any contract behaved. So the choice is a
rule, the rule is this code, its result is written to `datasets/noise_study.toml`, and a test (and
`python -m evals.run noise`) recompute that result from the cards and refuse a list that is not what
the rule gives. The list cannot be edited by hand to include a contract that looked interesting. The
rule, in words, is in `noise-study.md`:

1. The pool is the dev cards in the tool-use set, plus the cards named in the file (Seaport, whose
   verdict is the one flip seen so far, which is why the study exists). Never held-out.
2. Named cards go in first.
3. Every trajectory trait (proxy, official, not a token, token) is covered once: if no chosen card
   has it, the best-ranked card with it joins, from a class that still has room.
4. Each class is filled to its quota, preferring a trait mix not yet chosen in that class.
5. "Best-ranked" is the smallest SHA-256 of `<salt>:<card id>`: fixed, not tuned.

The files that make up the registration (this rule's result, the note with the decision rule) must
be committed before the run: `registration` reads that from git, and the run-set's manifest records
the commit and a hash of both files.
"""

import hashlib
import json
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from evals.cards import Card, CardSet

EVALS_DIR = Path(__file__).resolve().parent
STUDY_PATH = EVALS_DIR / "datasets" / "noise_study.toml"
NOTE_PATH = EVALS_DIR / "noise-study.md"
TRAIT_ORDER = ("proxy", "official", "not_a_token", "token")


@dataclass(frozen=True)
class Decision:
    """The thresholds of the decision rule for R, in contracts (of the study's ten) and runs."""

    calm_direction_max: int  # contracts whose runs disagree on warn / clear: at most this, for calm
    calm_exact_max: int  # contracts with any difference in verdict: at most this, for calm
    noisy_direction_min: int  # at least this many direction flips make the study noisy
    noisy_exact_min: int  # ... or at least this many contracts with any difference in verdict
    abstain_max: int  # more abstaining runs than this: find the cause before choosing R
    tools_share: float  # this share of differing-verdict pairs with differing tool results


@dataclass(frozen=True)
class Study:
    version: int
    salt: str
    repeats: int
    named: tuple[str, ...]
    quota: dict[str, int]
    population_sha256: str
    ids: tuple[str, ...]
    decision: Decision

    @property
    def contracts(self) -> int:
        return len(self.ids)


def load_study(path: Path = STUDY_PATH) -> Study:
    data = tomllib.loads(path.read_text())
    try:
        decision = data["decision"]
        return Study(
            version=int(data["version"]),
            salt=data["salt"],
            repeats=int(data["repeats"]),
            named=tuple(data["named"]),
            quota={str(k): int(v) for k, v in data["quota"].items()},
            population_sha256=data["registered"]["population_sha256"],
            ids=tuple(data["registered"]["ids"]),
            decision=Decision(
                calm_direction_max=int(decision["calm_direction_max"]),
                calm_exact_max=int(decision["calm_exact_max"]),
                noisy_direction_min=int(decision["noisy_direction_min"]),
                noisy_exact_min=int(decision["noisy_exact_min"]),
                abstain_max=int(decision["abstain_max"]),
                tools_share=float(decision["tools_share"]),
            ),
        )
    except KeyError as err:
        raise ValueError(f"noise_study.toml is missing {err}") from None


def rank(salt: str, card_id: str) -> str:
    return hashlib.sha256(f"{salt}:{card_id}".encode()).hexdigest()


def signature(card: Card) -> tuple[str, ...]:
    return tuple(sorted(card.traits))


def pool_of(cards: CardSet, named: Sequence[str]) -> list[Card]:
    return [c for c in cards if c.split == "dev" and (c.tool_use or c.id in named)]


def population_sha256(pool: Sequence[Card]) -> str:
    """A hash of the facts of the pool the rule reads (not of labels or sources, which may be
    edited without changing the choice)."""
    rows = sorted([c.id, c.klass, list(signature(c)), c.tool_use, c.split] for c in pool)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def choose(cards: CardSet, *, salt: str, named: Sequence[str], quota: dict[str, int]) -> list[str]:
    """The ids the rule gives, in the order of the cards file."""
    pool = pool_of(cards, named)
    by_id = {c.id: c for c in pool}
    for card_id in named:
        if card_id not in by_id:
            raise ValueError(f"named card {card_id!r} is not a dev card")
    ranked = sorted(pool, key=lambda c: rank(salt, c.id))
    room = dict(quota)
    chosen: dict[str, Card] = {}

    def take(card: Card) -> None:
        if room.get(card.klass, 0) <= 0:
            raise ValueError(f"no room left for class {card.klass!r} ({card.id})")
        chosen[card.id] = card
        room[card.klass] -= 1

    for card_id in named:
        take(by_id[card_id])
    for trait in TRAIT_ORDER:
        if any(trait in c.traits for c in chosen.values()):
            continue
        pick = next(
            (
                c
                for c in ranked
                if trait in c.traits and c.id not in chosen and room.get(c.klass, 0) > 0
            ),
            None,
        )
        if pick is not None:
            take(pick)
    for klass in quota:
        while room[klass] > 0:
            seen = {signature(c) for c in chosen.values() if c.klass == klass}
            left = [c for c in ranked if c.klass == klass and c.id not in chosen]
            if not left:
                raise ValueError(f"fewer than {quota[klass]} {klass!r} cards in the pool")
            fresh = [c for c in left if signature(c) not in seen]
            take((fresh or left)[0])
    return [c.id for c in pool if c.id in chosen]


def verify(study: Study, cards: CardSet) -> list[str]:
    """Why the registered list is not what the rule gives from these cards (empty if it is)."""
    try:
        pool = pool_of(cards, study.named)
        expected = choose(cards, salt=study.salt, named=study.named, quota=study.quota)
    except ValueError as err:
        return [str(err)]
    problems = []
    if population_sha256(pool) != study.population_sha256:
        problems.append(
            "the pool the rule reads changed since the study was registered (a card was added, "
            "removed, or its class, traits or tool-use flag edited): re-run the rule and register "
            "the study again as a new version"
        )
    if list(study.ids) != expected:
        problems.append(
            f"the registered list is not what the rule gives: registered {list(study.ids)}, "
            f"the rule gives {expected}"
        )
    if sum(study.quota.values()) != len(study.ids):
        problems.append("the quotas do not add up to the number of registered contracts")
    return problems


def registered_cards(study: Study, cards: CardSet) -> list[Card]:
    by_id = {c.id: c for c in cards}
    return [by_id[i] for i in study.ids]


def files_sha256(paths: Sequence[Path] = (STUDY_PATH, NOTE_PATH)) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


class Registration(NamedTuple):
    commit: str | None  # the commit that last changed the registration files
    uncommitted: list[str]  # files changed or not yet added since: a run must wait for these


def git_registration(paths: Sequence[Path] = (STUDY_PATH, NOTE_PATH)) -> Registration:
    """Which commit registered the study, and whether the files have changed since. A run is only
    allowed when nothing is uncommitted, so the commit predates the run."""
    names = [str(p) for p in paths]
    where = paths[0].parent
    try:
        last = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", *names],
            cwd=where,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", *names],
            cwd=where,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return Registration(None, ["git could not be asked about the registration files"])
    changed = [line[3:] for line in status.splitlines() if line.strip()]
    return Registration(last or None, changed or ([] if last else ["never committed"]))
