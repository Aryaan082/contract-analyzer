"""Comparing a target with one reference: function lists, then code.

Two numbers describe a function-list comparison, because one score hides the interesting part. A
token with 11 functions against a 9-function ERC20 scores 0.82 "similar" on Jaccard, which says
nothing about which 2 differ. Coverage says the target has all 9 (1.0); `extras` says it adds 2.
"""

from collections.abc import Collection

from references.bytecode import push_constants, split_metadata
from references.library import Prepared
from references.models import CodeComparison, CodeRelation, SelectorOverlap


def jaccard(a: Collection, b: Collection) -> float:
    """Shared over everything either has. Two empty sets share nothing, so 0.0, not 1.0: no
    evidence is not a match."""
    union = len(set(a) | set(b))
    return len(set(a) & set(b)) / union if union else 0.0


def selector_overlap(target: frozenset[str], reference: frozenset[str]) -> SelectorOverlap:
    shared = len(target & reference)
    return SelectorOverlap(
        shared=shared,
        reference_total=len(reference),
        target_total=len(target),
        jaccard=round(jaccard(target, reference), 4),
        reference_coverage=round(shared / len(reference), 4) if reference else None,
        extras=len(target - reference),
        missing=len(reference - target),
    )


def _equal_outside(a: bytes, b: bytes, ranges: tuple[tuple[int, int], ...]) -> bool:
    cursor = 0
    for start, length in sorted(ranges):
        if a[cursor:start] != b[cursor:start]:
            return False
        cursor = start + length
    return a[cursor:] == b[cursor:]


def code_relation(target: bytes, reference: Prepared) -> CodeRelation:
    """How strongly the target's code is the reference's code, strongest first.

    The variable ranges are the REFERENCE's. If the target is the same program its layout is the
    same, so ignoring those spans on both sides is enough; nothing needs to be guessed about the
    target's own immutables.
    """
    if reference.code is None or reference.body is None:
        return "different"
    if target == reference.code:
        return "identical"
    body, _ = split_metadata(target)
    if body == reference.body:
        return "identical_ignoring_metadata"
    ranges = reference.reference.variable_ranges
    if ranges and len(body) == len(reference.body) and _equal_outside(body, reference.body, ranges):
        return "identical_ignoring_variable_bytes"
    return "different"


def compare_code(target: bytes, reference: Prepared) -> CodeComparison:
    body, metadata = split_metadata(target)
    # The reference's own selectors are left out of both sides: the function list is scored
    # separately, and counting selectors again would make the code look more alike than it is.
    exclude = frozenset(bytes.fromhex(s[2:]) for s in reference.selectors)
    target_constants = push_constants(body, exclude=exclude)
    return CodeComparison(
        relation=code_relation(target, reference),
        reference_constants_found=(
            round(len(reference.constants & target_constants) / len(reference.constants), 4)
            if reference.constants
            else None
        ),
        constants_jaccard=(
            round(jaccard(reference.constants, target_constants), 4)
            if reference.constants
            else None
        ),
        reference_solc=reference.metadata.solc if reference.metadata else None,
        target_solc=metadata.solc if metadata else None,
    )
