"""`match`: which reference contracts does a target resemble, and how closely.

Evidence, in order of strength:
1. Exact copy: the code is the reference's code (ignoring the metadata tail, or bytes that vary
   between copies). Identical code behaves identically, so this cannot be faked.
2. Function list: the target implements the reference's functions. Robust to the compiler used,
   but it shows the same INTERFACE, not the same behaviour.
3. Code constants: a rough hint, weakened by any change of compiler or optimizer.

The thresholds below are provisional. They are named here, in one place, so they can be measured
and adjusted once Component 6 has labelled contracts; until then they are judgement calls.
"""

from dataclasses import dataclass

from references.abi import keccak_hex
from references.bytecode import split_metadata
from references.library import Library
from references.models import (
    Candidate,
    CodeComparison,
    MatchQuality,
    MatchResult,
    SelectorOverlap,
    TargetSummary,
)
from references.similarity import compare_code, selector_overlap

# "close": the target implements at least this share of a reference's functions.
CLOSE_COVERAGE = 0.9
# Below this share of a reference's functions it is not a candidate at all.
PARTIAL_COVERAGE = 0.5
# A reference with fewer functions than this cannot make a match "close": every contract has
# `owner()`-sized subsets of something.
MIN_FUNCTIONS_FOR_CLOSE = 4
# A reference with no functions (a proxy) is a candidate only if this share of its constants
# appears in the target.
CODE_ONLY_CONSTANTS = 0.8
DEFAULT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class Target:
    selectors: frozenset[str]
    code: bytes | None = None


def _summarise(target: Target) -> TargetSummary:
    if target.code is None:
        return TargetSummary(
            selector_count=len(target.selectors),
            code_size=None,
            code_hash=None,
            solc=None,
            metadata_found=None,
        )
    _, metadata = split_metadata(target.code)
    return TargetSummary(
        selector_count=len(target.selectors),
        code_size=len(target.code),
        code_hash=keccak_hex(target.code),
        solc=metadata.solc if metadata else None,
        metadata_found=metadata is not None,
    )


def _is_exact(code: CodeComparison | None) -> bool:
    return code is not None and code.relation != "different"


def _is_candidate(overlap: SelectorOverlap, code: CodeComparison | None) -> bool:
    if _is_exact(code):
        return True
    if overlap.reference_total:
        return (overlap.reference_coverage or 0.0) >= PARTIAL_COVERAGE
    return code is not None and (code.reference_constants_found or 0.0) >= CODE_ONLY_CONSTANTS


def _quality(top: Candidate | None) -> MatchQuality:
    if top is None:
        return "none"
    if _is_exact(top.code):
        return "exact"
    coverage = top.selectors.reference_coverage or 0.0
    if top.selectors.reference_total >= MIN_FUNCTIONS_FOR_CLOSE and coverage >= CLOSE_COVERAGE:
        return "close"
    return "partial"


def _notes(target: Target, quality: MatchQuality, top: Candidate | None) -> tuple[str, ...]:
    notes = []
    if quality == "none":
        if not target.selectors and target.code is None:
            notes.append("Nothing to compare: no selectors and no code were given.")
        else:
            notes.append(
                "No reference has at least half of its functions in this contract, and none is an "
                "exact copy. Insufficient evidence to name a known contract."
            )
        return tuple(notes)
    assert top is not None
    if target.code is None:
        notes.append("No code was given, so only function lists were compared.")
    elif not _is_exact(top.code):
        notes.append(
            "The code is not a copy of the best candidate. A matching function list shows the "
            "same interface, not that the functions behave the same."
        )
        if top.code and top.code.reference_solc != top.code.target_solc:
            notes.append(
                f"Compiler versions differ (reference {top.code.reference_solc or 'unknown'}, "
                f"target {top.code.target_solc or 'unknown'}), so the code scores are rough."
            )
    elif top.code and top.code.relation == "identical_ignoring_variable_bytes":
        notes.append(
            "The code equals the reference except for bytes that differ between copies "
            "(immutables or an embedded address)."
        )
    return tuple(notes)


def match(target: Target, library: Library, *, limit: int = DEFAULT_LIMIT) -> MatchResult:
    candidates = []
    for prepared in library.contracts:
        overlap = selector_overlap(target.selectors, prepared.selectors)
        code = compare_code(target.code, prepared) if target.code else None
        if _is_candidate(overlap, code):
            candidates.append(
                Candidate(
                    reference_id=prepared.reference.id,
                    name=prepared.reference.name,
                    family=prepared.reference.family,
                    origin=prepared.reference.origin,
                    selectors=overlap,
                    code=code,
                )
            )
    candidates.sort(
        key=lambda c: (
            not _is_exact(c.code),
            -c.selectors.jaccard,
            -(c.selectors.reference_coverage or 0.0),
            -((c.code.reference_constants_found or 0.0) if c.code else 0.0),
            c.reference_id,
        )
    )
    top = candidates[0] if candidates else None
    quality = _quality(top)
    return MatchResult(
        target=_summarise(target),
        quality=quality,
        candidates=tuple(candidates[:limit]),
        notes=_notes(target, quality, top),
    )
