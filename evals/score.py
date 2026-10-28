"""Score a set of agent traces offline: no model, no network, no money.

    python -m evals.score agent/runs                          card-free metrics for every trace
    python -m evals.score runset/ --cards evals/datasets/contracts.toml --split dev
    python -m evals.score runset/ --cards ... --write         also save evals/results/<...>.json
    python -m evals.score runset/ --cards ... --held-out --declare "milestone 1: harness shakedown"

Without cards only the metrics that need no label are computed (hygiene rules, citation validity,
time, cost). With cards, runs are matched to their card by chain, address and block and the
tool-use,
detection and reason checks are added. Faithfulness (the LLM judge) is not here: it is judged apart
and its cached verdicts are merged in by a later part.

The held-out split is only scored with `--held-out --declare "<why>"`, prints totals and never
writes
per-contract detail, the same rules as the retrieval layer.
"""

import argparse
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any

from evals.cards import CardSet, load_cards
from evals.citations import citation_summary
from evals.detection import (
    Pair,
    by_reason,
    confusion,
    f1_of,
    facts_stated,
    grey_check,
    precision_of,
    recall_of,
    verdict_right,
    verdicts_by_class,
)
from evals.repeats import cluster_bootstrap, contract_rates, flips
from evals.results_file import RESULTS_DIR, build_results, write_results
from evals.runs import RunRecord, load_runs
from evals.timing import cost_summary, time_summary, tool_call_counts
from evals.trajectory import check, rule_failures


def parts(obj: Any) -> dict[str, Any]:
    """A dataclass as a dict, keeping nested intervals as intervals (they are written later)."""
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def pair_up(runs: Sequence[RunRecord], cards: CardSet | None) -> tuple[list[Pair], list[RunRecord]]:
    pairs: list[Pair] = []
    loose: list[RunRecord] = []
    for run in runs:
        card = cards.find(run) if cards is not None else None
        if card is None:
            loose.append(run)
        else:
            pairs.append((run, card))
    return pairs, loose


def _share(items: list[bool]) -> float:
    return sum(items) / len(items) if items else float("nan")


def _tool_use(pairs: list[Pair], everyone: Sequence[RunRecord], resamples: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    hygiene: dict[str, list[bool]] = defaultdict(list)
    for run in everyone:
        hygiene[run.key].append(check(run).passed)
    out["hygiene_only"] = {
        "runs": len(everyone),
        "pass": cluster_bootstrap(list(hygiene.values()), _share, resamples=resamples),
    }
    out["hit_tool_cap_by_model"] = tool_call_counts(everyone, lambda r: r.model)
    if pairs:
        trajectories = [(run, card, check(run, card)) for run, card in pairs]
        outcomes: dict[str, list[bool]] = defaultdict(list)
        for _, card, trajectory in trajectories:
            outcomes[card.id].append(trajectory.passed)
        out["carded_runs"] = len(trajectories)
        out["pass"] = parts(contract_rates(outcomes, resamples=resamples))
        out["rule_failures"] = rule_failures(t for _, _, t in trajectories)
    return out


def _detection(pairs: list[Pair], resamples: int) -> dict[str, Any]:
    scored = [p for p in pairs if p[1].scored]
    out: dict[str, Any] = {"verdicts_by_class": verdicts_by_class(pairs)}
    if scored:
        clusters: dict[str, list[Pair]] = defaultdict(list)
        for pair in scored:
            clusters[pair[1].id].append(pair)
        conf = confusion(scored)
        groups = list(clusters.values())
        out.update(
            scored_runs=len(scored),
            scored_contracts=len(clusters),
            confusion=parts(conf),
            precision=cluster_bootstrap(groups, precision_of, resamples=resamples),
            recall=cluster_bootstrap(groups, recall_of, resamples=resamples),
            f1=cluster_bootstrap(groups, f1_of, resamples=resamples),
            coverage=conf.coverage,
            by_reason=by_reason(scored),
        )
        right: dict[str, list[bool]] = defaultdict(list)
        for run, card in scored:
            right[card.id].append(bool(verdict_right(run, card)))
        out["verdict_right"] = parts(contract_rates(right, resamples=resamples))
    verdicts: dict[str, list[str]] = defaultdict(list)
    for run, card in pairs:
        verdicts[card.id].append(run.verdict)
    out["flips"] = parts(flips(verdicts))
    stated = [s for run, card in pairs if (s := facts_stated(run, card)) is not None]
    out["facts_stated"] = {"runs": len(stated), "share": _share(stated)}
    grey: dict[str, dict[str, Any]] = {}
    for run, card in pairs:
        if card.klass != "grey":
            continue
        row = grey.setdefault(card.id, {"runs": 0, "not_checked_ok": 0, "forbidden": 0})
        checked = grey_check(run, card)
        row["runs"] += 1
        row["not_checked_ok"] += bool(checked["not_checked_ok"])
        row["forbidden"] += bool(checked["forbidden"])
    out["grey"] = grey
    return out


# Below this many flagged contracts a recall figure is a count, and the report says so.
FEW_FLAGGED = 10


def score(
    runs: Sequence[RunRecord],
    cards: CardSet | None = None,
    *,
    split: str | None = None,
    model: str | None = None,
    label: str = "",
    declared: str | None = None,
    resamples: int = 10_000,
) -> dict[str, Any]:
    """Every layer that can be scored offline, as a results document (see `results_file`)."""
    if split == "heldout" and not declared:
        raise ValueError("the held-out split is only scored with a declared reason")
    if model:
        runs = [r for r in runs if r.model == model]
    pairs, loose = pair_up(runs, cards)
    if split:
        pairs = [(r, c) for r, c in pairs if c.split == split]
        loose = []
    in_scope = [r for r, _ in pairs] + loose
    unreviewed, total = cards.unreviewed() if cards is not None else (0, 0)
    commits = sorted(
        {r.environment["git_commit"] for r in in_scope if r.environment.get("git_commit")}
    )
    models = Counter(r.model for r in in_scope)
    notes = []
    if len(models) > 1:
        notes.append(
            "Several models are pooled in these numbers, which is only a survey: score one model "
            "at a time (--model) for a result that can be quoted."
        )
    if cards is not None:
        flagged = sum(c.klass == "flagged" for c in (cards.in_split(split) if split else cards))
        if flagged < FEW_FLAGGED:
            notes.append(
                "Recall rests on "
                + (
                    f"{flagged} flagged contract{'s' * (flagged != 1)}"
                    if flagged
                    else "no flagged contract"
                )
                + f" in this split (fewer than {FEW_FLAGGED}): read it as a count of contracts, "
                "not a rate, and expect a very wide interval."
            )
    manifest = {
        "label": label,
        "split": split or "all",
        "held_out_declared": declared,
        "traces": len(in_scope),
        "carded_runs": len(pairs),
        "uncarded_runs": len(loose),
        "models": dict(models),
        "git_commits": commits,
        "git_dirty": any(r.environment.get("git_dirty") for r in in_scope),
        "cards": total,
        "unreviewed_labels": f"{unreviewed} of {total}",
        "notes": [
            *notes,
            *(
                [
                    "Labels are computed by script and no human has reviewed them: the numbers "
                    "say 'agrees with the script and the written rules', not 'agrees with Aryaan'."
                ]
                if unreviewed
                else []
            ),
        ],
    }
    layers: dict[str, Any] = {
        "tool_use": _tool_use(pairs, in_scope, resamples),
        "detection": _detection(pairs, resamples) if pairs else None,
        "answer_quality": {
            "citations": parts(citation_summary(in_scope, resamples=resamples)),
            "faithfulness": None,  # the judge: a later part
        },
        "time": time_summary(in_scope),
        "cost": cost_summary(in_scope),
    }
    if split != "heldout":
        by_card: dict[str, list[Pair]] = defaultdict(list)
        for pair in pairs:
            by_card[pair[1].id].append(pair)
        layers["per_contract"] = [
            {
                "id": card_id,
                "class": group[0][1].klass,
                "runs": len(group),
                "verdicts": dict(Counter(r.verdict for r, _ in group)),
                "trajectory_failed": sorted({n for r, c in group for n in check(r, c).failed}),
            }
            for card_id, group in sorted(by_card.items())
        ]
    return build_results(manifest, layers)


# ---- the readable report -------------------------------------------------------------------


def _iv(value: dict[str, Any] | None) -> str:
    if not value:
        return "n/a"
    return f"{value['value']:.2f} [{value['low']:.2f}-{value['high']:.2f}]"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_report(results: dict[str, Any]) -> str:
    m = results["manifest"]
    lines = [
        f"scored {m['traces']} traces ({m['carded_runs']} matched to a card), split {m['split']}; "
        f"models: {', '.join(f'{k} x{v}' for k, v in m['models'].items()) or 'none'}",
        f"labels: {m['unreviewed_labels']} unreviewed",
        *m["notes"],
    ]
    tool = results["tool_use"]
    hygiene = tool["hygiene_only"]
    lines.append(
        f"\ntool use   hygiene rules pass {_iv(hygiene['pass'])} over {hygiene['runs']} runs; "
        f"hit the tool cap: {tool['hit_tool_cap_by_model']}"
    )
    if "pass" in tool:
        p = tool["pass"]
        lines.append(
            f"           all rules: single run {_iv(p['single_run'])}, "
            f"majority {_iv(p['majority'])}, "
            f"all-correct {_iv(p['all_correct'])} ({p['contracts']} contracts, {p['runs']} runs; "
            f"{p['stable_pass']} stable, {p['unstable']} unstable, {p['stable_fail']} failing)"
        )
        failing = {k: v for k, v in tool["rule_failures"].items() if v["failed"]}
        lines.append(
            "           rules failing: "
            + (", ".join(f"{k} {v['failed']}/{v['applied']}" for k, v in failing.items()) or "none")
        )
    det = results.get("detection")
    if det and "confusion" in det:
        c = det["confusion"]
        lines.append(
            f"\ndetection  precision {_iv(det['precision'])}, recall {_iv(det['recall'])}, "
            f"F1 {_iv(det['f1'])}, coverage {_num(det['coverage'])}"
        )
        lines.append(
            f"           flagged: {c['tp']} warned, {c['fn']} cleared, "
            f"{c['abstain_flagged']} abstained; "
            f"benign: {c['fp']} warned, {c['tn']} cleared, {c['abstain_benign']} abstained"
        )
        v = det["verdict_right"]
        lines.append(
            f"           verdict as labelled: single run {_iv(v['single_run'])}, "
            f"majority {_iv(v['majority'])}, all-correct {_iv(v['all_correct'])}; "
            f"verdict flips in {_num(det['flips']['rate'])} of repeated contracts"
        )
    elif det:
        lines.append(f"\ndetection  nothing scored (verdicts: {det['verdicts_by_class']})")
    cites = results["answer_quality"]["citations"]
    lines.append(
        f"\nanswer quality  first submission accepted {_iv(cites['first_attempt_accepted'])}, "
        f"claim validity {_iv(cites['claim_validity'])} "
        f"({cites['claims_failing_first']} of {cites['claims_first']} claims failed); "
        f"codes {cites['problem_codes']}; faithfulness: not judged yet"
    )
    for model, t in results["time"].items():
        e = t["end_to_end_s"]
        shown = (
            f"p50 {e['p50']}s, p95 {e['p95'] if e['p95'] is not None else 'n/a'}s, max {e['max']}s"
            if e.get("n")
            else "no usable timing"
        )
        lines.append(
            f"\ntime [{model}]  {t['used']} of {t['runs']} runs usable "
            f"(left out: {t['left_out'] or 'none'}); end to end n={e.get('n', 0)} {shown}"
        )
    for model, c in results["cost"].items():
        lines.append(
            f"cost [{model}]  ${c['total_usd']:.4f} over {c['runs']} runs; per report "
            f"p50 ${c['per_report_usd']['p50']}, max ${c['per_report_usd']['max']}; "
            f"cached share {c['cached_share']}; tool calls per run {c['tool_calls_mean']}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.score", description=__doc__)
    parser.add_argument("places", nargs="+", type=Path, help="trace files or folders")
    parser.add_argument("--cards", type=Path, help="a cards TOML file (the labels)")
    parser.add_argument("--split", choices=("dev", "heldout"))
    parser.add_argument("--held-out", action="store_true", help="score the held-out split")
    parser.add_argument("--declare", help="why the held-out split is being looked at")
    parser.add_argument("--model", help="score only the runs of this model slug")
    parser.add_argument("--label", default="", help="name for the results file")
    parser.add_argument("--write", action="store_true", help=f"save into {RESULTS_DIR.name}/")
    parser.add_argument("--out", type=Path, help="save into this folder instead")
    parser.add_argument("--resamples", type=int, default=10_000)
    args = parser.parse_args(argv)

    split = "heldout" if args.held_out else args.split
    if split == "heldout" and not (args.held_out and args.declare):
        print("error: the held-out split needs --held-out and --declare '<why>'", file=sys.stderr)
        return 1
    if split and not args.cards:
        print("error: --split needs --cards", file=sys.stderr)
        return 1
    runs, skipped = load_runs(args.places)
    for note in skipped:
        print(f"skipped: {note}", file=sys.stderr)
    if not runs:
        print("error: no traces found", file=sys.stderr)
        return 1
    cards = load_cards(args.cards) if args.cards else None
    results = score(
        runs,
        cards,
        split=split,
        model=args.model,
        label=args.label,
        declared=args.declare,
        resamples=args.resamples,
    )
    print(format_report(results))
    if args.write or args.out:
        path = write_results(results, args.label or "runset", args.out or RESULTS_DIR)
        print(f"\nsaved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
