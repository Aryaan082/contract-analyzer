"""Detection: is the verdict right? Precision, recall and F1, with the abstains in view.

The verdict is turned into a stance: `elevated_risk` and `high_risk` warn, `no_red_flags_found`
clears, `insufficient_evidence` abstains. A flagged contract that is cleared or abstained on is a
miss (strict recall); a benign contract that is warned on is a false alarm; a benign contract that
gets an abstain is not (coverage shows how often the agent answered at all). Only `flagged` and
`benign` cards are scored; `grey` is judged on its not-checked list and on what it must not say, and
`beyond_reach` is only reported.

Precision depends on how common flagged contracts are in the test set, so it must not be quoted as a
real-world false-alarm rate; the confusion counts are always reported with it.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from evals.cards import SCORED, WARNING_VERDICTS, Card
from evals.runs import RunRecord

Pair = tuple[RunRecord, Card]
NAN = float("nan")


def stance(verdict: str) -> str:
    if verdict in WARNING_VERDICTS:
        return "warn"
    return "clear" if verdict == "no_red_flags_found" else "abstain"


@dataclass(frozen=True)
class Confusion:
    tp: int = 0  # flagged, warned
    fn: int = 0  # flagged, cleared
    abstain_flagged: int = 0
    fp: int = 0  # benign, warned
    tn: int = 0  # benign, cleared
    abstain_benign: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else NAN

    @property
    def recall(self) -> float:
        """Strict: an abstain on a flagged contract counts as a miss."""
        total = self.tp + self.fn + self.abstain_flagged
        return self.tp / total if total else NAN

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if p != p or r != r or p + r == 0:
            return NAN
        return 2 * p * r / (p + r)

    @property
    def coverage(self) -> float:
        """The share of runs that got an answer rather than an abstain."""
        total = sum((self.tp, self.fn, self.abstain_flagged, self.fp, self.tn, self.abstain_benign))
        answered = total - self.abstain_flagged - self.abstain_benign
        return answered / total if total else NAN


def confusion(pairs: Iterable[Pair]) -> Confusion:
    counts: Counter[str] = Counter()
    for run, card in pairs:
        if card.klass not in SCORED:
            continue
        verdict = stance(run.verdict)
        if card.klass == "flagged":
            counts[{"warn": "tp", "clear": "fn", "abstain": "abstain_flagged"}[verdict]] += 1
        else:
            counts[{"warn": "fp", "clear": "tn", "abstain": "abstain_benign"}[verdict]] += 1
    return Confusion(**counts)


def precision_of(pairs: Sequence[Pair]) -> float:
    return confusion(pairs).precision


def recall_of(pairs: Sequence[Pair]) -> float:
    return confusion(pairs).recall


def f1_of(pairs: Sequence[Pair]) -> float:
    return confusion(pairs).f1


def verdict_right(run: RunRecord, card: Card) -> bool | None:
    """Whether the verdict is one the card accepts; None for cards that are not scored."""
    return run.verdict in card.acceptable if card.scored else None


def by_reason(pairs: Iterable[Pair]) -> dict[str, dict[str, int]]:
    """For each labelled reason of the flagged contracts: runs, and how many of them warned. A
    contract with two reasons counts under both. `honeypot-sell-blocked` is decided by the sell
    rehearsal and the validator's floor rather than by the model's judgement; read it as such."""
    out: dict[str, dict[str, int]] = {}
    for run, card in pairs:
        if card.klass != "flagged":
            continue
        for reason in card.reasons or ("unspecified",):
            row = out.setdefault(reason, {"runs": 0, "warned": 0})
            row["runs"] += 1
            row["warned"] += stance(run.verdict) == "warn"
    return dict(sorted(out.items()))


def verdicts_by_class(pairs: Iterable[Pair]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter[str]] = {}
    for run, card in pairs:
        out.setdefault(card.klass, Counter())[run.verdict] += 1
    return {klass: dict(counter) for klass, counter in sorted(out.items())}


def _matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE | re.DOTALL) is not None


def facts_stated(run: RunRecord, card: Card) -> bool | None:
    """Whether the report states every fact the card requires (reason recall); None if the card
    lists none. A crude regular-expression check: it finds gross omissions, not small ones."""
    if not card.facts:
        return None
    return all(_matches(p, run.report_text) for p in card.facts)


def forbidden_said(run: RunRecord, card: Card) -> list[str]:
    """The card's `must_not_say` patterns the report matches."""
    return [p for p in card.must_not_say if _matches(p, run.report_text)]


def grey_check(run: RunRecord, card: Card) -> dict[str, object]:
    """What is checked for a contract that is not scored on its verdict."""
    return {
        "not_checked_ok": len(run.not_checked) >= card.min_not_checked,
        "forbidden": forbidden_said(run, card),
        "facts": facts_stated(run, card),
    }
