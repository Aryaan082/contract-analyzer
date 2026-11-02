"""The third stage of the layered run: credit the places, have the reasoning verifier read the
rest, order the output (Aryaan, 2026-11-06). It only ever ANNOTATES: no place is dropped, merged
away or demoted out of the output.

The order of the output is the system's own and is made WITHOUT the answer key: in an evaluation
the auditors' findings must never decide which flags come first. `annotate` takes `matched` only
for production use (a known audit that matches), and an evaluation passes none.

    tier 0  matched to a known audit finding (`matched`)
    tier 1  confirmed by a deterministic check
    tier 2  the reasoning verifier says valid (a scenario and a harm, nothing refuting it)
    tier 3  the reasoning verifier says uncertain
    tier 4  not checked by the verifier (it did not answer, or none was given)
    tier 5  the reasoning verifier says invalid (it found a reason the claim is wrong)

and inside a tier: both layers agreeing first, then severity, then id. `review_list` is what a
person is shown: tiers 0 to 2 at severity low or up, with the rest counted and kept, never deleted.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from review.client import ReviewerClient
from review.layered import LAYERS, Place
from verify import funnel, reason
from verify.reason import Reasoned
from verify.source import function_source
from verify.types import Candidate, Credit

_LEVEL = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Annotated:
    planned: funnel.Planned
    reasoned: Reasoned | None = None  # the reasoning verifier's answer; its sentences are untrusted

    @property
    def candidate(self) -> Candidate:
        return self.planned.candidate

    @property
    def credit(self) -> Credit | None:
        return self.planned.credit

    @property
    def tier(self) -> int:
        credit = self.credit
        if credit is not None:
            return 0 if credit.kind == "report" else 1
        if self.reasoned is None:
            return 4
        return {"valid": 2, "uncertain": 3}.get(self.reasoned.verdict, 5)

    @property
    def status(self) -> str:
        """What a person is told: credited (a report or a check), valid, uncertain, rejected (the
        verifier refuted it) or unchecked (the verifier did not answer)."""
        if self.credit is not None:
            return "credited"
        if self.reasoned is None:
            return "unchecked"
        return {"valid": "valid", "uncertain": "uncertain"}.get(self.reasoned.verdict, "rejected")


def candidates_of(report: str, places: Sequence[Place]) -> list[Candidate]:
    """One candidate per place: the lines and sentence of its most severe flag, and both layers."""
    out = []
    for p in places:
        top = min(p.flags, key=lambda f: _LEVEL.get(f.severity, 3))
        out.append(Candidate(report, p.file, p.contract, p.function, top.start_line, top.end_line,
                             top.note, p.severity, p.layers & set(LAYERS)))  # fmt: skip
    return out


def order_key(a: Annotated) -> tuple:
    c = a.candidate
    return (a.tier, -len(c.layers & set(LAYERS)), _LEVEL.get(c.severity, 3), c.id)


def output_order(annotated: Sequence[Annotated]) -> list[Annotated]:
    """The output list: the system's order, with no access to the answer key."""
    return sorted(annotated, key=order_key)


def annotate(
    candidates: Sequence[Candidate],
    files: Mapping[str, str],
    *,
    matched: Mapping[tuple, str] | None = None,
    workers: int = 1,
    reasoner: ReviewerClient | None = None,
) -> list[Annotated]:
    """Plan the funnel, then have the reasoning verifier read every place that no report or check
    already vouches for (with no `reasoner` the plan alone). Returns one Annotated per place."""
    merged = funnel.merge_by_place(candidates)
    codes = {c.id: function_source(files, c) for c in merged}
    planned = funnel.plan(merged, matched=matched or {}, codes=codes)
    annotated = [Annotated(p) for p in planned]
    if reasoner is not None:
        todo = [a for a in annotated if a.credit is None]
        got = reason.reason_all(reasoner, [a.candidate for a in todo], files, workers)
        for a in todo:
            a.reasoned = got.get(a.candidate.id)
    return annotated


def record(a: Annotated) -> dict:
    """What is kept: the funnel record, the tier and the verdict words; never the reader's sentence
    or any sentence of the verifier's."""
    out = funnel.record(a.planned)
    out["tier"] = a.tier
    out["reasoned"] = None if a.reasoned is None else {
        "verdict": a.reasoned.verdict, "confidence": a.reasoned.confidence,
        "downgraded": a.reasoned.downgraded,
    }  # fmt: skip
    out["status"] = a.status
    cr = a.credit
    out["credit"] = {"kind": cr.kind, "ref": cr.ref} if cr else None
    return out


SHOWN_SEVERITIES = (
    "low",
    "medium",
    "high",
)  # the verifier's severity a valid flag needs to be shown


@dataclass(frozen=True)
class ReviewList:
    """What the user is shown and what is only counted. Every place is in exactly one list, in the
    system's output order; none is deleted."""

    shown: list[
        Annotated
    ]  # credited by a report, a check or a test, or valid at severity low or up
    minor: list[Annotated]  # valid, but informational or of undetermined size
    uncertain: list[Annotated]
    rejected: list[Annotated]  # the verifier found a reason the claim is wrong
    unchecked: list[Annotated]  # the verifier did not answer (or was not run on it)


def review_list(annotated: Sequence[Annotated]) -> ReviewList:
    """Split the places for a person. A valid place whose severity is informational or undetermined
    goes to `minor`: on the dev runs, showing every valid place left the list as long as plain's,
    and the cut at severity low kept 94% of the findings the union finds in a list 41% shorter."""
    groups: dict[str, list[Annotated]] = {
        "shown": [], "minor": [], "uncertain": [], "rejected": [], "unchecked": []
    }  # fmt: skip
    for a in output_order(annotated):
        key = {"credited": "shown", "valid": "shown"}.get(a.status, a.status)
        if key == "shown" and a.status == "valid" and a.reasoned.severity not in SHOWN_SEVERITIES:  # type: ignore[union-attr]
            key = "minor"
        groups[key].append(a)
    return ReviewList(**groups)
