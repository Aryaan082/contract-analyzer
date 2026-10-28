"""Citation validity: how often the model's own report cites evidence that exists.

The validator proves one thing: every cited ID is in the run's evidence and is of the right kind
(plus a few consistency rules). It does not prove the evidence says what the claim says; that is
faithfulness, judged elsewhere. And because the loop accepts only reports that pass, the FINAL
report is valid by construction, so validity is counted on the FIRST submission:
- report level: the share of runs whose first `submit_report` passed with no problem;
- claim level: the share of the claims in first submissions that passed on their own;
- by problem code, so a schema slip (a missing field) is not mistaken for a bad citation.

A trace written since the instrumentation records every attempt. For an older trace the attempts are
rebuilt by running the validator again on the submitted arguments against the run's evidence; that
uses the final evidence, so it can be a little kinder than the original check.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from agent.evidence import Evidence, EvidenceRegistry
from agent.validator import MAX_FINDINGS, parse_submission, valid_findings, validate
from evals.metrics import Interval
from evals.repeats import NAN, cluster_bootstrap
from evals.runs import Attempt, RunRecord


def _registry(run: RunRecord) -> EvidenceRegistry:
    """The run's evidence, put back in the order it was recorded (so T1, T2, ... line up)."""
    registry = EvidenceRegistry()
    for raw in run.evidence:
        item = Evidence.model_validate(raw)
        if item.source == "tool":
            registry.add_tool(
                item.tool or "", item.arguments or {}, item.provenance, item.data, ok=item.ok
            )
        else:
            registry.add_knowledge(item.id, item.citation, str(item.data))
    return registry


def rebuild_attempts(run: RunRecord) -> tuple[Attempt, ...]:
    registry = _registry(run)
    out = []
    for number, arguments in enumerate(run.submissions, start=1):
        submission, problems = parse_submission(arguments)
        claims = failing = None
        if submission is not None:
            problems = validate(submission, registry)
            claims = len(submission.findings[:MAX_FINDINGS])
            failing = claims - len(valid_findings(submission, registry))
        out.append(
            Attempt(
                attempt=number,
                parsed=submission is not None,
                accepted=not problems,
                claims=claims,
                claims_failing=failing,
                problems=tuple((p.where, p.code) for p in problems),
                rebuilt=True,
            )
        )
    return tuple(out)


def attempts_of(run: RunRecord) -> tuple[Attempt, ...]:
    return run.attempts or rebuild_attempts(run)


@dataclass(frozen=True)
class CitationSummary:
    runs: int
    no_submission: int  # the run ended (a cap, the model went away) before any report was tried
    first_attempt_accepted: Interval | None  # of the runs that tried
    accepted_after_retry: int
    ended_rejected: int  # tried, and never got a report through
    claims_first: int
    claims_failing_first: int
    claim_validity: Interval | None
    problem_codes: dict[str, int]  # of first attempts, one per problem
    rebuilt_runs: int


def _validity(pairs: list[tuple[int, int]]) -> float:
    claims = sum(c for c, _ in pairs)
    return 1 - sum(f for _, f in pairs) / claims if claims else NAN


def citation_summary(runs: Sequence[RunRecord], *, resamples: int = 10_000) -> CitationSummary:
    tried = [(r, attempts_of(r)) for r in runs]
    tried = [(r, a) for r, a in tried if a]
    clusters: dict[str, list[bool]] = {}
    claim_clusters: dict[str, list[tuple[int, int]]] = {}
    codes: Counter[str] = Counter()
    for run, attempts in tried:
        first = attempts[0]
        clusters.setdefault(run.key, []).append(first.accepted)
        codes.update(code for _, code in first.problems)
        if first.parsed and first.claims is not None:
            claim_clusters.setdefault(run.key, []).append((first.claims, first.claims_failing or 0))
    claim_pairs = [p for c in claim_clusters.values() for p in c]
    return CitationSummary(
        runs=len(runs),
        no_submission=len(runs) - len(tried),
        first_attempt_accepted=(
            cluster_bootstrap(
                list(clusters.values()),
                lambda xs: sum(xs) / len(xs) if xs else NAN,
                resamples=resamples,
            )
            if clusters
            else None
        ),
        accepted_after_retry=sum(1 for r, a in tried if r.accepted and not a[0].accepted),
        ended_rejected=sum(1 for r, _ in tried if not r.accepted),
        claims_first=sum(c for c, _ in claim_pairs),
        claims_failing_first=sum(f for _, f in claim_pairs),
        claim_validity=(
            cluster_bootstrap(list(claim_clusters.values()), _validity, resamples=resamples)
            if claim_pairs
            else None
        ),
        problem_codes=dict(sorted(codes.items())),
        rebuilt_runs=sum(1 for r, a in tried if a[0].rebuilt),
    )
