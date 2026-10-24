"""`diff`: what a target has that a reference lacks (extras), and the reverse (missing).

Extras are the core signal for an unverified contract, but extras alone are weak: `permit` and
`burn` are legitimate. So each extra is named wherever possible. Names for the reference's own
functions come from its ABI. Names for extras come, in order of trust, from other library entries
(`also_in`: e.g. `owner()` is OpenZeppelin's Ownable) and then from outside candidates, which are
checked to hash to the selector but remain suggestions.
"""

from collections.abc import Mapping, Sequence

from references.library import Library
from references.models import DiffResult, DiffRow, KnownAs, NameCandidate
from references.names import clean_candidates


def diff(
    target: frozenset[str],
    reference_id: str,
    library: Library,
    names: Mapping[str, Sequence[NameCandidate | str]] | None = None,
) -> DiffResult:
    reference = library.get(reference_id)
    signatures = {f.selector: f.signature for f in reference.reference.functions}
    extras_set = target - reference.selectors
    candidates, dropped = clean_candidates(names, extras_set)

    def row(selector: str, status: str) -> DiffRow:
        others = tuple(
            KnownAs(reference_id=rid, signature=sig)
            for rid, sig in library.names_for(selector)
            if rid != reference_id
        )
        return DiffRow(
            selector=selector,
            status=status,
            reference_signature=signatures.get(selector),
            also_in=others if status == "extra" else (),
            candidates=candidates.get(selector, ()) if status == "extra" else (),
        )

    extras = tuple(row(s, "extra") for s in sorted(extras_set))
    return DiffResult(
        reference_id=reference_id,
        extras=extras,
        missing=tuple(row(s, "missing") for s in sorted(reference.selectors - target)),
        shared=tuple(row(s, "shared") for s in sorted(target & reference.selectors)),
        unnamed_extras=tuple(r.selector for r in extras if not r.also_in and not r.candidates),
        names_dropped=dropped,
    )
