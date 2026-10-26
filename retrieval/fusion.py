"""Merging ranked lists: reciprocal rank fusion.

Each list gives a chunk `1 / (k + rank)` points (rank starting at 1) and the points are added up.
A chunk near the top of both lists wins, one near the top of just one still scores, and because
only ranks are used there are no scores to rescale or tune. `k = 60` is the value from the
original paper and is not tuned here.
"""

from collections.abc import Sequence


def rrf(rankings: Sequence[Sequence[int]], *, k: int = 60) -> list[tuple[int, float]]:
    """(item, fused score), best first. Ties keep the lower item number first."""
    points: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            points[item] = points.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(points.items(), key=lambda pair: (-pair[1], pair[0]))
