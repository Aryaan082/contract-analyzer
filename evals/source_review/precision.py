"""Scoring that spewing flags cannot game (Aryaan, 2026-11-06: "we shouldn't just be able to spew
information to perform well on evals ... the entire point of verification is to make sure the
number of false positives is reduced").

The pre-registered headline counts the auditors' findings a run finds. That is recall, and a reader
that writes 150 flags a report earns it with the last 100. On the 2026-11-05 dev runs plain emitted
146 flags a run: 9% were judged the same bug as an auditor's finding, 23% sat in the right place
with a different bug and 69% were neither; its first 20 flags per report found 7.4 findings and all
of them found 13.2. So a layered run is scored on its OUTPUT, in the order the system itself puts
it:

* recall at K: the findings found in the first K places of each report, K = 5, 10, 20, 40 (the
  primary K is 20, taken from plain's own curve before any layered run was made);
* credited share: the share of output places with any evidence, in all and in the first K. Evidence
  is an answer-key match, a sandbox confirmation or a deterministic check;
* unverified share: the places with none. It is an UPPER bound on false positives, not a count of
  them: the auditors' list is incomplete and the sandbox is not yet good at confirming real bugs, so
  "unverified" is never called "false";
* places with evidence but no answer-key match (`verified_beyond_key`): real-looking finds the key
  does not know, reported apart and never added to the headline;
* findings per 100 places: the cost to a reader of each find.

The order must not come from the answer key (`verify.stage.output_order` has no access to it), or
recall at K would reward peeking. These are pure functions of already-judged data.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

KS = (5, 10, 20, 40)
PRIMARY_K = 20


@dataclass(frozen=True)
class OutPlace:
    """One place in a system's output list, with what is known about it."""

    report: str
    labels: frozenset[str] = field(
        default_factory=frozenset
    )  # findings a flag here was judged the same bug as
    right_place: bool = False  # a flag here is in a finding's place with a different bug
    verified: bool = False  # sandbox-confirmed or confirmed by a deterministic check

    @property
    def evidence(self) -> bool:
        return bool(self.labels) or self.verified


def _found(places: Sequence[OutPlace]) -> int:
    return len(frozenset().union(*(p.labels for p in places))) if places else 0


def output_metrics(
    ordered: Sequence[OutPlace], total_findings: int, ks: Sequence[int] = KS
) -> dict:
    """`ordered` is one system's output, every report, each report's places in the system's order.
    K applies per report."""
    by_report: dict[str, list[OutPlace]] = {}
    for p in ordered:
        by_report.setdefault(p.report, []).append(p)
    n = len(ordered)
    credited = sum(p.evidence for p in ordered)
    beyond = sum(1 for p in ordered if p.verified and not p.labels)
    right_only = sum(1 for p in ordered if p.right_place and not p.evidence)
    found = _found(ordered)
    out: dict = {
        "places": n,
        "found": found,
        "recall": found / total_findings if total_findings else 0.0,
        "credited": credited,
        "credited_share": credited / n if n else 0.0,
        "verified_beyond_key": beyond,
        "right_place_only": right_only,
        "unverified": n - credited - right_only,
        "unverified_share": (n - credited) / n if n else 0.0,
        "findings_per_100_places": 100 * found / n if n else 0.0,
        "at_k": {},
    }
    for k in ks:
        top = [p for ps in by_report.values() for p in ps[:k]]
        out["at_k"][k] = {
            "places": len(top),
            "found": _found(top),
            "credited_share": sum(p.evidence for p in top) / len(top) if top else 0.0,
        }
    return out


def beats(layered: dict, baseline: dict, k: int = PRIMARY_K) -> bool:
    """The pre-registered guard of the layered evaluation, on point values: more findings in the
    first K places AND a credited share there that is not lower. (The report adds the interval.)"""
    a, b = layered["at_k"][k], baseline["at_k"][k]
    return a["found"] > b["found"] and a["credited_share"] >= b["credited_share"]
