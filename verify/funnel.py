"""Which flags are simulated (Aryaan, 2026-11-05: only logic bugs, and not every unmatched flag).

`plan` takes the flags of a report and sorts each into one of five places, in this order:

    report      it matches an auditor's finding (the judge panel's call, passed in): credited by the
                report, no simulation. On a new contract there is no such list, and every flag
                starts at the next step
    check       a deterministic rule confirms the construct it names (`verify.mechanical`)
    skipped     triage says it is not a testable logic bug (a by-design admin power, a best-practice
                note, unclear)
    queued      triage says logic; ranked, and the first `per_report` of each report are simulated
    unverified  not triaged, or testable but over the cap

Ranking: flagged by both layers first (the best free signal: 19% of such places matched an auditor
finding against 7% to 9% for one layer, on the 2026-11-05 dev runs), then triage confidence, then
severity, then id (so a plan is the same every time). Nothing is dropped: every flag is returned
with the place it was sorted into and why.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from verify.mechanical import mechanical_rule
from verify.triage import Triage
from verify.types import LAYERS, Candidate, Credit

DISPOSITIONS = ("report", "check", "skipped", "queued", "unverified")
DEFAULT_PER_REPORT = 10
_LEVEL = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Planned:
    candidate: Candidate
    disposition: str
    reason: str  # words of ours
    credit: Credit | None = None
    rank: int | None = None  # 1 is first among the queued flags of its report


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


def rank_key(candidate: Candidate, triage: Triage) -> tuple[int, int, int, str]:
    agreed = len(candidate.layers & set(LAYERS))
    return (
        -agreed,
        _LEVEL.get(triage.confidence, 3),
        _LEVEL.get(candidate.severity, 3),
        candidate.id,
    )


def plan(
    candidates: Sequence[Candidate],
    *,
    matched: Mapping[tuple, str],
    codes: Mapping[str, str],
    triage: Mapping[str, Triage],
    per_report: int = DEFAULT_PER_REPORT,
) -> list[Planned]:
    """`matched` maps a place to the auditor's finding id the judge panel matched it to; `codes`
    maps a candidate id to its function's code (for the checks); `triage` maps a candidate id to its
    triage."""
    merged = merge_by_place(candidates)
    placed: dict[str, Planned] = {}
    queue: dict[str, list[tuple[Candidate, Triage]]] = {}
    for c in merged:
        if c.place in matched:
            placed[c.id] = Planned(c, "report", "matches an auditor's finding",
                                   Credit("report", matched[c.place]))  # fmt: skip
        elif rule := mechanical_rule(c, codes.get(c.id, "")):
            placed[c.id] = Planned(c, "check", f"rule {rule}", Credit("check", rule))
        elif (t := triage.get(c.id)) is None:
            placed[c.id] = Planned(c, "unverified", "not triaged")
        elif not t.testable:
            placed[c.id] = Planned(c, "skipped", f"triage: {t.kind}")
        else:
            queue.setdefault(c.report, []).append((c, t))
    for items in queue.values():
        for rank, (c, _) in enumerate(sorted(items, key=lambda x: rank_key(*x)), 1):
            if rank <= per_report:
                placed[c.id] = Planned(c, "queued", "testable logic bug", rank=rank)
            else:
                placed[c.id] = Planned(c, "unverified", "testable but over the cap")
    return [placed[c.id] for c in merged]


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
        "disposition": planned.disposition, "reason": planned.reason, "rank": planned.rank,
        "credit": {"kind": planned.credit.kind, "ref": planned.credit.ref}
        if planned.credit else None,
    }  # fmt: skip
