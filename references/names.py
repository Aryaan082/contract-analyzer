"""Names for selectors that come from outside the library.

They are untrusted: a selector database is open to anyone, and a function name only has to hash to
the 4 bytes it claims. So a candidate is kept only if it is a plain signature (no prose, no
spaces) AND it really hashes to the selector it was offered for. The rest is dropped and counted.
"""

from collections.abc import Iterable, Mapping, Sequence

from references.abi import is_signature, selector_of
from references.models import NameCandidate

MAX_CANDIDATES_PER_SELECTOR = 50


def clean_candidates(
    names: Mapping[str, Sequence[NameCandidate | str]] | None, selectors: Iterable[str]
) -> tuple[dict[str, tuple[NameCandidate, ...]], int]:
    """Keep valid candidates for the wanted selectors. Returns (kept, number dropped)."""
    wanted = set(selectors)
    kept: dict[str, tuple[NameCandidate, ...]] = {}
    dropped = 0
    for selector, items in (names or {}).items():
        if selector not in wanted:
            continue
        accepted: dict[str, NameCandidate] = {}
        for index, item in enumerate(items):
            candidate = item if isinstance(item, NameCandidate) else NameCandidate(name=str(item))
            valid = is_signature(candidate.name) and selector_of(candidate.name) == selector
            if index >= MAX_CANDIDATES_PER_SELECTOR or not valid or candidate.name in accepted:
                dropped += 1
                continue
            accepted[candidate.name] = candidate
        if accepted:
            kept[selector] = tuple(accepted.values())
    return kept, dropped
