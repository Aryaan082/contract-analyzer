"""The third stage of the layered run: credit the places, simulate the testable ones, order the
output (Aryaan, 2026-11-06). It only ever ANNOTATES: no place is dropped, merged away or demoted
out of the output. A place that is not simulated stays, marked unverified.

The order of the output is the system's own and is made WITHOUT the answer key. In an evaluation the
auditors' findings must never decide which flags come first, or "recall in the first K flags" would
reward peeking: so `annotate` takes `matched` only for production use (a known audit that matches),
and the evaluation passes none. A flag spewed out without evidence goes to the back:

    tier 0  confirmed by a sandbox test, or matched to a known audit finding (`matched`)
    tier 1  confirmed by a deterministic check
    tier 2  testable logic bug that was simulated and not confirmed
    tier 3  testable but over the per-report cap
    tier 4  not triaged
    tier 5  triage says not a testable logic bug (by-design admin power, best-practice note)

and inside a tier: both layers agreeing first, then triage confidence, then severity, then id.
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from review.client import ReviewerClient
from review.layered import LAYERS, Place
from verify import funnel, sandbox, triage
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
        disposition = self.planned.disposition
        if disposition == "queued":
            return 2
        if disposition == "skipped":
            return 5
        return 3 if self.triage is not None else 4  # unverified: over the cap, or never triaged


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
) -> list[Annotated]:
    """Triage every place, plan the funnel, and simulate the queued ones when a writer, a reviewer
    and a runner are given (otherwise the plan alone). Returns one Annotated per place."""
    merged = funnel.merge_by_place(candidates)
    tri = triage.triage_all(triage_client, merged, files, workers)
    codes = {c.id: function_source(files, c) for c in merged}
    planned = funnel.plan(merged, matched=matched or {}, codes=codes, triage=tri,
                          per_report=per_report)  # fmt: skip
    annotated = [Annotated(p, tri.get(p.candidate.id)) for p in planned]
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
    cr = a.credit
    out["credit"] = {"kind": cr.kind, "ref": cr.ref} if cr else None
    return out
