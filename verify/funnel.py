"""Which flags need the reasoning verifier: the ones nothing else already vouches for.

`plan` takes the flags of a report and sorts each into one of three places, in this order:

    report      it matches an auditor's finding (the judge panel's call, passed in): credited by the
                report. On a new contract there is no such list, and every flag starts at the
                next step
    check       a deterministic rule confirms the construct it names (`verify.mechanical`)
    unverified  everything else: it goes to the verifier (`verify.reason`)

Nothing is dropped: every flag is returned with the place it was sorted into and why.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from verify.mechanical import mechanical_rule
from verify.types import Candidate, Credit

_LEVEL = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Planned:
    candidate: Candidate
    disposition: str  # report, check or unverified
    reason: str  # words of ours
    credit: Credit | None = None


def merge_by_place(candidates: Sequence[Candidate]) -> list[Candidate]:
    """One candidate per place: the first one seen, with the layers of all of them and the highest
    severity. Plain flags are nearly all at distinct places already (146 flags, 142 places), so this
    mostly merges the two layers' flags at the same function."""
    merged: dict[tuple, Candidate] = {}
    for c in candidates:
        first = merged.get(c.place)
        if first is None:
            merged[c.place] = c
            continue
        severity = min((first.severity, c.severity), key=lambda s: _LEVEL.get(s, 3))
        merged[c.place] = Candidate(
            first.report, first.file, first.contract, first.function, first.start_line,
            first.end_line, first.note, severity, first.layers | c.layers,
        )  # fmt: skip
    return list(merged.values())


def plan(
    candidates: Sequence[Candidate], *, matched: Mapping[tuple, str], codes: Mapping[str, str]
) -> list[Planned]:
    """`matched` maps a place to the auditor's finding id the judge panel matched it to; `codes`
    maps a candidate id to its function's code (for the checks)."""
    out = []
    for c in merge_by_place(candidates):
        if c.place in matched:
            out.append(Planned(c, "report", "matches an auditor's finding",
                               Credit("report", matched[c.place])))  # fmt: skip
        elif rule := mechanical_rule(c, codes.get(c.id, "")):
            out.append(Planned(c, "check", f"rule {rule}", Credit("check", rule)))
        else:
            out.append(Planned(c, "unverified", "nothing vouches for it"))
    return out


def summary(planned: Sequence[Planned]) -> Counter[str]:
    return Counter(p.disposition for p in planned)


def record(planned: Planned) -> dict:
    """What is kept about a flag: where it is, where the funnel put it and who vouches for it. The
    reviewer's sentence is untrusted text and is not part of it."""
    c = planned.candidate
    return {
        "id": c.id, "report": c.report, "file": c.file, "contract": c.contract,
        "function": c.function, "start_line": c.start_line, "end_line": c.end_line,
        "severity": c.severity, "layers": sorted(c.layers),
        "disposition": planned.disposition, "reason": planned.reason,
        "credit": {"kind": planned.credit.kind, "ref": planned.credit.ref}
        if planned.credit else None,
    }  # fmt: skip
