"""The arithmetic for checking a judge: catch and false-alarm rates, agreement between two judges,
and stability of one judge. Plain counting over labels, no model.

Definitions (fixed here so a number always means the same thing):
- A *fault* item is a claim that is wrong or unsupported on purpose or by discovery. It is *caught*
  when the judge's label is one of the item's `catch_labels` (for a stretched claim `partly` counts;
  for a flipped fact only `not_supported` or `contradicted`). `caught_strict` counts only
  `not_supported` and `contradicted`, the two labels that fail a report.
- A *sound* item is a claim believed to follow from its evidence. A *false alarm* is a sound item
  labelled `not_supported` or `contradicted`; a sound item labelled `partly` is *downgraded*, which
  lowers claim-level faithfulness but does not fail the report.
- Items the judge did not label (an unparsed reply, or never judged) are counted apart and are in no
  rate's denominator; the rate is always printed with how many were left out.
- Intervals are Wilson 95% intervals for a proportion. With about 40 items they are wide (a rate of
  0.90 over 40 reads roughly 0.77 to 0.96), and that is the honest size of what this check shows.
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from evals.judge.rubric import BAD_LABELS, LABELS


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(successes: int, total: int) -> dict[str, float | int | None]:
    interval = wilson(successes, total)
    return {
        "k": successes,
        "n": total,
        "rate": round(successes / total, 4) if total else None,
        "low": round(interval[0], 4) if interval else None,
        "high": round(interval[1], 4) if interval else None,
    }


@dataclass(frozen=True)
class Scored:
    """One check item with the judge's label (None: not labelled)."""

    id: str
    fault: bool
    origin: str  # planted | real | sound
    operator: str
    catch_labels: tuple[str, ...]
    label: str | None


def catch_and_false_alarm(items: Sequence[Scored]) -> dict[str, object]:
    faults = [i for i in items if i.fault]
    sound = [i for i in items if not i.fault]
    labelled_faults = [i for i in faults if i.label is not None]
    labelled_sound = [i for i in sound if i.label is not None]
    caught = [i for i in labelled_faults if i.label in i.catch_labels]
    strict = [i for i in labelled_faults if i.label in BAD_LABELS]
    alarms = [i for i in labelled_sound if i.label in BAD_LABELS]
    downgraded = [i for i in labelled_sound if i.label == "partly"]

    def breakdown(key: str) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for name in sorted({getattr(i, key) for i in labelled_faults}):
            group = [i for i in labelled_faults if getattr(i, key) == name]
            out[name] = rate(sum(1 for i in group if i.label in i.catch_labels), len(group))
        return out

    return {
        "faults": len(faults),
        "faults_unlabelled": len(faults) - len(labelled_faults),
        "catch": rate(len(caught), len(labelled_faults)),
        "catch_strict": rate(len(strict), len(labelled_faults)),
        "catch_by_origin": breakdown("origin"),
        "catch_by_operator": breakdown("operator"),
        "sound": len(sound),
        "sound_unlabelled": len(sound) - len(labelled_sound),
        "false_alarm": rate(len(alarms), len(labelled_sound)),
        "downgraded": rate(len(downgraded), len(labelled_sound)),
        "missed": sorted(i.id for i in labelled_faults if i.label not in i.catch_labels),
        "false_alarms": sorted(i.id for i in alarms),
        "labels_on_faults": dict(Counter(i.label for i in labelled_faults)),
        "labels_on_sound": dict(Counter(i.label for i in labelled_sound)),
    }


def agreement(first: dict[str, str | None], second: dict[str, str | None]) -> dict[str, object]:
    """Two judges on the same items (id -> label). Only items both labelled are compared. Besides
    exact agreement on the four labels there is agreement on the coarse question "supported or
    not", and Cohen's kappa on that coarse question (agreement beyond what the two judges' own
    label frequencies would give by chance; unstable with few items, shown for reference)."""
    both = sorted(i for i in first if first[i] is not None and second.get(i) is not None)
    exact = sum(first[i] == second[i] for i in both)
    coarse = [(first[i] == "supported", second[i] == "supported") for i in both]
    agree = sum(a == b for a, b in coarse)
    n = len(both)
    kappa = None
    if n:
        p_a = sum(a for a, _ in coarse) / n
        p_b = sum(b for _, b in coarse) / n
        chance = p_a * p_b + (1 - p_a) * (1 - p_b)
        kappa = None if chance == 1 else round((agree / n - chance) / (1 - chance), 4)
    return {
        "items": n,
        "left_out": len(first) - n,
        "exact": rate(exact, n),
        "supported_or_not": rate(agree, n),
        "kappa_supported_or_not": kappa,
        "disagreements": [
            {"id": i, "first": first[i], "second": second[i]} for i in both if first[i] != second[i]
        ],
        "by_pair": dict(Counter(f"{first[i]} / {second[i]}" for i in both)),
    }


def stability(first: dict[str, str | None], second: dict[str, str | None]) -> dict[str, object]:
    """The same judge, the same inputs, asked twice: how many labels changed."""
    both = sorted(i for i in first if first[i] is not None and second.get(i) is not None)
    changed = [i for i in both if first[i] != second[i]]
    coarse = [i for i in both if (first[i] == "supported") != (second[i] == "supported")]
    return {
        "items": len(both),
        "left_out": len(first) - len(both),
        "changed": len(changed),
        "changed_supported_or_not": len(coarse),
        "changes": [{"id": i, "first": first[i], "second": second[i]} for i in changed],
    }


def label_counts(labels: Sequence[str | None]) -> dict[str, int]:
    counts = Counter(labels)
    out = {name: counts.get(name, 0) for name in LABELS}
    out["unlabelled"] = counts.get(None, 0)
    return out
