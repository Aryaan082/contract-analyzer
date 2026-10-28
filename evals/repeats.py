"""Many runs of the same contract: how to count them and how sure we can be.

The agent is not repeatable to the letter, even at temperature 0, so each contract is run R times
and three views are reported, never one:
- single-run accuracy: the share of ALL runs that are right (what one random run is worth);
- majority: the share of contracts where more than half of the runs are right;
- all-correct: the share of contracts where every run is right (the strict, reliability view).
A contract is `stable_pass` (all runs right), `unstable` (some) or `stable_fail` (none).

Intervals resample CONTRACTS, keeping each contract's runs together (a cluster bootstrap), because
three runs of one contract are not three independent pieces of evidence.
"""

import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from evals.metrics import Interval, percentile

NAN = float("nan")


def cluster_bootstrap[T](
    clusters: Sequence[Sequence[T]],
    statistic: Callable[[list[T]], float],
    *,
    resamples: int = 10_000,
    seed: int = 0,
    level: float = 0.95,
) -> Interval:
    """`statistic` of all the items, with a percentile interval from resampling the clusters.
    A statistic may return NaN when a resample leaves it undefined (no warnings, say); those
    resamples are dropped."""
    if not clusters:
        raise ValueError("no clusters")
    point = statistic([item for cluster in clusters for item in cluster])
    rng = random.Random(seed)
    n = len(clusters)
    draws = []
    for _ in range(resamples):
        value = statistic([item for cluster in rng.choices(clusters, k=n) for item in cluster])
        if value == value:
            draws.append(value)
    if not draws:
        return Interval(point, point, point)
    tail = (1 - level) / 2
    return Interval(point, percentile(draws, tail), percentile(draws, 1 - tail))


def _share(items: list[bool]) -> float:
    return sum(items) / len(items) if items else NAN


@dataclass(frozen=True)
class Rates:
    contracts: int
    runs: int
    single_run: Interval
    majority: Interval
    all_correct: Interval
    stable_pass: int
    unstable: int
    stable_fail: int


def contract_rates(outcomes: dict[str, list[bool]], *, resamples: int = 10_000) -> Rates:
    """`outcomes` maps a contract to whether each of its runs was right."""
    if not outcomes:
        raise ValueError("no contracts")
    per_run = [list(runs) for runs in outcomes.values()]
    majority = [[sum(runs) * 2 > len(runs)] for runs in per_run]
    strict = [[all(runs)] for runs in per_run]
    status = Counter("pass" if all(r) else "fail" if not any(r) else "mixed" for r in per_run)
    return Rates(
        contracts=len(per_run),
        runs=sum(len(r) for r in per_run),
        single_run=cluster_bootstrap(per_run, _share, resamples=resamples),
        majority=cluster_bootstrap(majority, _share, resamples=resamples),
        all_correct=cluster_bootstrap(strict, _share, resamples=resamples),
        stable_pass=status["pass"],
        unstable=status["mixed"],
        stable_fail=status["fail"],
    )


@dataclass(frozen=True)
class Flips:
    rate: float | None  # share of contracts run at least twice whose verdicts are not all alike
    contracts: dict[str, dict[str, int]]  # the contracts that flipped, with their verdict counts


def flips(verdicts: dict[str, list[str]]) -> Flips:
    """How often the verdict changes between runs of the same contract. A contract run once cannot
    flip and is left out of the denominator."""
    repeated = {c: v for c, v in verdicts.items() if len(v) >= 2}
    changed = {c: dict(Counter(v)) for c, v in repeated.items() if len(set(v)) > 1}
    return Flips(len(changed) / len(repeated) if repeated else None, changed)
