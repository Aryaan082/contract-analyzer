"""Scores and honest error bars, standard library only.

recall@k   of the chunks marked right for a question, how many are in the top k (the divisor is
           capped at k, so a question with six right answers cannot score below 1.0 for a full page)
RR         1 / the position of the first right chunk (0 if none is returned); its mean is MRR
interval   percentile bootstrap: resample the questions with replacement 10,000 times and take the
           2.5th and 97.5th percentile of the mean. With few questions the interval is wide, and
           that is the point: a difference smaller than it is not shown.
paired     two variants answer the same questions, so the per-question differences are resampled;
           this is tighter than comparing two separate intervals.
"""

import random
from collections.abc import Collection, Sequence
from dataclasses import dataclass


def recall_at_k(ranked: Sequence[str], relevant: Collection[str], k: int = 5) -> float:
    right = set(relevant)
    if not right:
        raise ValueError("a question needs at least one right answer")
    return len(right & set(ranked[:k])) / min(len(right), k)


def reciprocal_rank(ranked: Sequence[str], relevant: Collection[str]) -> float:
    right = set(relevant)
    for position, chunk_id in enumerate(ranked, start=1):
        if chunk_id in right:
            return 1.0 / position
    return 0.0


@dataclass(frozen=True)
class Interval:
    mean: float
    low: float
    high: float

    def __str__(self) -> str:
        return f"{self.mean:.2f} [{self.low:.2f}–{self.high:.2f}]"


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))]


def bootstrap_ci(
    values: Sequence[float], *, resamples: int = 10_000, seed: int = 0, level: float = 0.95
) -> Interval:
    if not values:
        raise ValueError("no values")
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    tail = (1 - level) / 2
    return Interval(sum(values) / n, _percentile(means, tail), _percentile(means, 1 - tail))


@dataclass(frozen=True)
class Difference:
    """mean(a - b) with its interval, and on how many questions a was better, worse or equal."""

    interval: Interval
    wins: int
    losses: int
    ties: int

    @property
    def shown(self) -> bool:
        """True if the whole interval lies on one side of zero."""
        return self.interval.low > 0 or self.interval.high < 0


def paired_difference(
    a: Sequence[float], b: Sequence[float], *, resamples: int = 10_000, seed: int = 0
) -> Difference:
    if len(a) != len(b):
        raise ValueError("paired scores need the same questions")
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    return Difference(
        bootstrap_ci(diffs, resamples=resamples, seed=seed),
        wins=sum(d > 1e-12 for d in diffs),
        losses=sum(d < -1e-12 for d in diffs),
        ties=sum(abs(d) <= 1e-12 for d in diffs),
    )


def percentile(values: Sequence[float], q: float) -> float:
    return _percentile(sorted(values), q)
