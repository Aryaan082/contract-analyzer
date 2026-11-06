"""The third stage of the layered run: credit the places, simulate the testable ones, order the
output (Aryaan, 2026-11-06). It only ever ANNOTATES: no place is dropped, merged away or demoted
out of the output. A place that is not simulated stays, marked unverified.

The order of the output is the system's own and is made WITHOUT the answer key. In an evaluation the
auditors' findings must never decide which flags come first, or "recall in the first K flags" would
reward peeking: so `annotate` takes `matched` only for production use (a known audit that matches),
and the evaluation passes none. A flag spewed out without evidence goes to the back:

    tier 0  confirmed by a sandbox test, or matched to a known audit finding (`matched`)
    tier 1  confirmed by a deterministic check
    tier 2  the reasoning verifier says valid (a scenario and a harm, nothing refuting it)
    tier 3  testable logic bug that was simulated and not confirmed
    tier 4  the reasoning verifier says uncertain
    tier 5  testable but over the per-report cap
    tier 6  not triaged, and not checked by the reasoning verifier
    tier 7  triage says not a testable logic bug (by-design admin power, best-practice note)
    tier 8  the reasoning verifier says invalid (it found a reason the claim is wrong)

and inside a tier: both layers agreeing first, then triage confidence, then severity, then id.
The reasoning verifier's answer, when there is one, decides the tier over the triage's: a place
triage called by-design that the verifier calls valid is tier 2. `review_list` is what a person is
shown: tiers 0 to 2, with the rest counted and kept, never deleted.
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from review.client import ReviewerClient
from review.layered import LAYERS, Place
from verify import funnel, reason, sandbox, triage
from verify.reason import Reasoned
from verify.runner import ForgeRunner
from verify.source import function_source
from verify.triage import Triage
from verify.types import Candidate, Credit, Verdict

_LEVEL = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Annotated:
    planned: funnel.Planned
    triage: Triage | None = None
    verdict: Verdict | None = None
    test_file: str = ""  # untrusted model output: kept by the caller outside the repo
    cost_usd: float = 0.0
    reasoned: Reasoned | None = None  # the reasoning verifier's answer; its sentences are untrusted

    @property
    def candidate(self) -> Candidate:
        return self.planned.candidate

    @property
    def credit(self) -> Credit | None:
        return self.planned.credit or (self.verdict.credit() if self.verdict else None)

    @property
    def tier(self) -> int:
        credit = self.credit
        if credit is not None:
            return 0 if credit.kind in ("sandbox", "report") else 1
        if self.reasoned is not None:
            return {"valid": 2, "uncertain": 4}.get(self.reasoned.verdict, 8)
        disposition = self.planned.disposition
        if disposition == "queued":
            return 3
        if disposition == "skipped":
            return 7
        return 5 if self.triage is not None else 6  # unverified: over the cap, or never triaged

    @property
    def status(self) -> str:
        """What a person is told: credited (a report, a check or a test), valid, uncertain,
        rejected (the verifier refuted it) or unchecked (the verifier did not answer)."""
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
    confidence = _LEVEL.get(a.triage.confidence, 3) if a.triage else 3
    return (a.tier, -len(c.layers & set(LAYERS)), confidence, _LEVEL.get(c.severity, 3), c.id)


def output_order(annotated: Sequence[Annotated]) -> list[Annotated]:
    """The output list: the system's order, with no access to the answer key."""
    return sorted(annotated, key=order_key)


def annotate(
    candidates: Sequence[Candidate],
    files: Mapping[str, str],
    *,
    triage_client: ReviewerClient,
    writer: ReviewerClient | None = None,
    reviewer: ReviewerClient | None = None,
    runner: ForgeRunner | None = None,
    solc: str = "",
    remappings: tuple[str, ...] = (),
    deps: Path | None = None,
    evm_version: str = "",
    matched: Mapping[tuple, str] | None = None,
    per_report: int = funnel.DEFAULT_PER_REPORT,
    max_attempts: int = sandbox.DEFAULT_ATTEMPTS,
    workers: int = 1,
    reasoner: ReviewerClient | None = None,
    reason_only_logic: bool = False,
) -> list[Annotated]:
    """Triage every place, plan the funnel, have the reasoning verifier read every place that no
    report or check already vouches for (only the ones triage calls logic when `reason_only_logic`),
    and simulate the queued ones when a writer, a reviewer and a runner are given (otherwise the
    plan alone). Returns one Annotated per place."""
    merged = funnel.merge_by_place(candidates)
    tri = triage.triage_all(triage_client, merged, files, workers)
    codes = {c.id: function_source(files, c) for c in merged}
    planned = funnel.plan(merged, matched=matched or {}, codes=codes, triage=tri,
                          per_report=per_report)  # fmt: skip
    annotated = [Annotated(p, tri.get(p.candidate.id)) for p in planned]
    if reasoner is not None:
        todo = [
            a for a in annotated
            if a.credit is None and (not reason_only_logic or (a.triage and a.triage.testable))
        ]  # fmt: skip
        got = reason.reason_all(reasoner, [a.candidate for a in todo], files, workers)
        for a in todo:
            a.reasoned = got.get(a.candidate.id)
    if writer is None or reviewer is None or runner is None:
        return annotated
    queued = [a for a in annotated if a.planned.disposition == "queued"]

    def simulate(a: Annotated) -> None:
        out = sandbox.verify_candidate(
            a.candidate, files, writer=writer, reviewer=reviewer, runner=runner, solc=solc,
            remappings=remappings, deps=deps, evm_version=evm_version, max_attempts=max_attempts,
        )  # fmt: skip
        a.verdict, a.test_file, a.cost_usd = out.verdict, out.test_file, out.cost_usd

    with ThreadPoolExecutor(max(workers, 1)) as pool:
        list(pool.map(simulate, queued))
    return annotated


def record(a: Annotated) -> dict:
    """What is kept: the funnel record, the triage words, the verdict and the test's hash; never the
    reader's sentence or the test text."""
    out = funnel.record(a.planned)
    out["tier"] = a.tier
    out["triage"] = (
        None if a.triage is None else {"kind": a.triage.kind, "confidence": a.triage.confidence}
    )
    out["verdict"] = None if a.verdict is None else {
        "status": a.verdict.status, "reason": a.verdict.reason,
        "attempts": a.verdict.attempts, "test_sha": a.verdict.test_sha,
    }  # fmt: skip
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
