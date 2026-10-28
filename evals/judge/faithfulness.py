"""Faithfulness of a set of agent runs, counted from cached judgements. No model, no network.

Claim level: the share of labelled claims that are `supported`.
Report level: the share of reports with zero `not_supported` or `contradicted` claims (one wrong
claim in a safety report matters more than the average). A report with a claim that has no label
(not yet judged, or the reply was refused) is neither clean nor failed: it is counted as unknown
and kept in the denominator, so it can only pull the share down.
Summary: the same two counts for the summary's sentences.

Intervals resample contracts, not claims, because the claims of one report, and the reports of one
contract, are not independent.

Only accepted reports are counted. The numbers describe what the judge said about the claims; they
are a model's opinion, and `limits` in the result says how that opinion was checked.
"""

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evals.judge.cache import JudgeCache, claim_key, summary_key
from evals.judge.compare import agreement
from evals.judge.inputs import claim_inputs, judgeable, summary_input
from evals.judge.rubric import BAD_LABELS, LABELS
from evals.metrics import Interval
from evals.repeats import NAN, cluster_bootstrap
from evals.runs import RunRecord

LIMITS = (
    "The judge is a model's opinion of whether the cited evidence supports each claim. It was "
    "checked against faults we planted, faults found by reading reports, and a second judge from "
    "another company; it was not checked against the project owner's own labels, and it is not "
    "truth. It sees only what a claim cites."
)


def run_key(run: RunRecord, number: int) -> str:
    """How a run is named in a table of labels: its trace's file name (so the same name serves on
    any machine), else its position."""
    return Path(run.path).name if run.path else f"run-{number}"


def _label(row: dict | None, index: int = 0) -> str | None:
    if row is None or not row.get("parsed"):
        return None
    verdicts = row.get("verdicts") or []
    return verdicts[index]["label"] if index < len(verdicts) else None


def claim_labels(
    runs: Sequence[RunRecord], cache: JudgeCache, model: str, rubric: str, repeat: int = 0
) -> dict[str, list[str | None]]:
    """For each accepted run (keyed by its path or position), the label of each of its claims;
    None where the claim has no usable judgement."""
    out: dict[str, list[str | None]] = {}
    for number, run in enumerate(runs):
        if not judgeable(run):
            continue
        labels = []
        for inp in claim_inputs(run):
            key = claim_key(
                inp.claim_sha, inp.evidence_sha, inp.kind, inp.severity, model, rubric, repeat
            )
            labels.append(_label(cache.get(key)))
        out[run_key(run, number)] = labels
    return out


def summary_labels(
    runs: Sequence[RunRecord], cache: JudgeCache, model: str, rubric: str, repeat: int = 0
) -> dict[str, list[str | None]]:
    out: dict[str, list[str | None]] = {}
    for number, run in enumerate(runs):
        if not judgeable(run):
            continue
        inp = summary_input(run)
        if inp is None:
            continue
        row = cache.get(summary_key(inp.summary_sha, inp.context_sha, model, rubric, repeat))
        if row is not None and row.get("parsed"):
            out[run_key(run, number)] = [v["label"] for v in row["verdicts"]]
        else:
            out[run_key(run, number)] = [None] * len(inp.sentences)
    return out


def _share(items: list[bool]) -> float:
    return sum(items) / len(items) if items else NAN


def _levels(
    by_run: dict[str, list[str | None]], contract_of: dict[str, str], resamples: int
) -> dict[str, object]:
    labels = Counter(label for run in by_run.values() for label in run)
    clusters_claims: dict[str, list[bool]] = {}
    clusters_reports: dict[str, list[bool]] = {}
    clean = failed = unknown = empty = 0
    for run, run_labels in by_run.items():
        contract = contract_of.get(run, run)
        if not run_labels:
            empty += 1
            continue
        clusters_claims.setdefault(contract, []).extend(
            label == "supported" for label in run_labels if label is not None
        )
        if any(label in BAD_LABELS for label in run_labels):
            failed += 1
            ok = False
        elif any(label is None for label in run_labels):
            unknown += 1
            ok = False
        else:
            clean += 1
            ok = True
        clusters_reports.setdefault(contract, []).append(ok)
    claim_clusters = [c for c in clusters_claims.values() if c]
    return {
        "items": sum(len(v) for v in by_run.values()),
        "labelled": sum(labels[name] for name in LABELS),
        "unlabelled": labels.get(None, 0),
        "by_label": {name: labels.get(name, 0) for name in LABELS},
        "item_level": _interval(claim_clusters, resamples),
        "reports": len(by_run) - empty,
        "reports_without_items": empty,
        "reports_clean": clean,
        "reports_failed": failed,
        "reports_unknown": unknown,
        "report_level": _interval(list(clusters_reports.values()), resamples),
    }


def _interval(clusters: list[list[bool]], resamples: int) -> Interval | None:
    if not clusters:
        return None
    return cluster_bootstrap(clusters, _share, resamples=resamples)


def judge_cost(cache: JudgeCache, model: str, rubric: str) -> dict[str, object]:
    rows = [r for r in cache.rows() if r["model"] == model and r["rubric"] == rubric]
    return {
        "calls": len(rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "refused_replies": sum(1 for r in rows if not r["parsed"]),
    }


def collect_labels(
    runs: Sequence[RunRecord], cache: JudgeCache, models: Sequence[str], rubric: str
) -> dict[str, Any]:
    """Everything the faithfulness numbers are counted from, as plain data: for each judge, the
    label of each claim and of each summary sentence of each accepted run, and what the judge cost.
    This is what a committed run-set summary keeps, so the numbers can be counted again without the
    traces or the cache."""
    return {
        "rubric_sha256": rubric,
        "judges": {
            model: {
                "claims": claim_labels(runs, cache, model, rubric),
                "summary": summary_labels(runs, cache, model, rubric),
                "cost": judge_cost(cache, model, rubric),
            }
            for model in models
        },
    }


def faithfulness_from_labels(
    labels: dict[str, Any],
    runs: Sequence[RunRecord],
    *,
    detail: bool = True,
    resamples: int = 10_000,
) -> dict[str, object]:
    """The faithfulness block of the results file for each judge in `labels`, and, for two judges,
    how they agree. Only the runs in `runs` count. `detail=False` (the held-out split) leaves out
    the per-report disagreement list."""
    contract_of = {run_key(r, n): r.key for n, r in enumerate(runs)}
    models = list(labels["judges"])
    per_judge: dict[str, object] = {}
    claim_sets = {}
    for model in models:
        found = labels["judges"][model]
        claims = {k: v for k, v in found["claims"].items() if k in contract_of}
        summaries = {k: v for k, v in found["summary"].items() if k in contract_of}
        claim_sets[model] = claims
        per_judge[model] = {
            "claims": _levels(claims, contract_of, resamples),
            "summary": _levels(summaries, contract_of, resamples),
            "cost": found["cost"],
        }
    block: dict[str, object] = {
        "rubric_sha256": labels["rubric_sha256"],
        "judges": models,
        "per_judge": per_judge,
        "limits": LIMITS,
    }
    if len(models) == 2:
        first, second = (
            {
                f"{run}#{i}": label
                for run, run_labels in claim_sets[m].items()
                for i, label in enumerate(run_labels)
            }
            for m in models
        )
        both = agreement(first, second)
        if not detail:
            both = {k: v for k, v in both.items() if k != "disagreements"}
        block["agreement"] = both
    judged = sum(per_judge[m]["claims"]["labelled"] for m in models)  # type: ignore[index]
    block["claims_judged"] = judged
    return block


def faithfulness(
    runs: Sequence[RunRecord],
    cache: JudgeCache,
    models: Sequence[str],
    rubric: str,
    *,
    detail: bool = True,
    resamples: int = 10_000,
) -> dict[str, object]:
    """`collect_labels` then `faithfulness_from_labels`: the block counted from a judge cache."""
    return faithfulness_from_labels(
        collect_labels(runs, cache, models, rubric), runs, detail=detail, resamples=resamples
    )
