"""One command for the evals: `python -m evals.run <layer or command> [options]`.

The retrieval layer is run here (`retrieval`). The agent layers are scored from a stored run-set by
`all`, a run-set is made by `generate`, and `heldout`, `ledger` and `check-summaries` are in
`evals/pipeline.py`; `make eval`, `make eval-run` and `make eval-heldout` call them.

    python -m evals.run all               retrieval on dev + the latest dev run-set, scored
    python -m evals.run generate          the estimate of a run-set; --go --max-spend X makes one

    python -m evals.run retrieval                       dev questions, the core variants
    python -m evals.run retrieval --variants all        every variant (needs the base models too)
    python -m evals.run retrieval --by style kind       recall@5 split by query style and by kind
    python -m evals.run retrieval --held-out            held-out questions: totals only. Run at
                                                        milestones, never to choose a variant
    python -m evals.run retrieval --log "change"        also append one row per variant to
                                                        evals/results.md
    python -m evals.run retrieval --check-baseline      exit 1 if the saved baseline variant is
                                                        confidently worse than when saved (CI)
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from evals.metrics import Interval, paired_difference
from evals.quiz import Split, load_split
from evals.retrieval_eval import RetrievalRunner, Run, Summary, breakdown, summarize
from evals.variants import CHAIN, GROUPS, VARIANTS, resolve

EVALS_DIR = Path(__file__).resolve().parent
RESULTS = EVALS_DIR / "results.md"
BASELINE = EVALS_DIR / "baseline_retrieval.json"
LAST_RUN = EVALS_DIR / "last_run.json"  # git-ignored

LOG_HEADER = (
    "| date | change | variant | split | n | recall@5 [95% CI] | MRR [95% CI] "
    "| p50 / p95 ms | notes |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
)


def _signed(value: float) -> str:
    return f"{value:+.2f}"


def _span(interval: Interval) -> str:
    return f"{_signed(interval.mean)} [{_signed(interval.low)}..{_signed(interval.high)}]"


def _row(change: str, name: str, split: str, s: Summary, notes: str = "") -> str:
    return (
        f"| {date.today().isoformat()} | {change} | {name} | {split} | {s.n} | {s.recall} "
        f"| {s.mrr} | {s.p50_ms:.1f} / {s.p95_ms:.1f} | {notes} |\n"
    )


def _print_table(rows: list[tuple[str, str, Summary]]) -> None:
    print(f"{'variant':20} {'split':18} {'n':>3}  {'recall@5':22} {'MRR':22} {'p50/p95 ms':>12}")
    for name, split, s in rows:
        print(
            f"{name:20} {split:18} {s.n:>3}  {s.recall!s:22} {s.mrr!s:22} "
            f"{s.p50_ms:>6.1f}/{s.p95_ms:<6.1f}"
        )


def _compare(runs: dict[str, Run]) -> list[tuple[str, str, str]]:
    """(label, recall text, notes) for every chain pair where both variants were run."""
    out = []
    for a, b in CHAIN:
        if a in runs and b in runs:
            ra = [s.recall for s in runs[a].scored]
            rb = [s.recall for s in runs[b].scored]
            mrr = paired_difference([s.rr for s in runs[b].scored], [s.rr for s in runs[a].scored])
            rec = paired_difference(rb, ra)
            verdict = "shown" if rec.shown else "not shown"
            text = _span(rec.interval)
            notes = (
                f"recall@5 {verdict}; {rec.wins} better, {rec.losses} worse, {rec.ties} same; "
                f"MRR {_span(mrr.interval)}"
            )
            out.append((f"{a} → {b}", text, notes))
    return out


def run_retrieval(args: argparse.Namespace, collect: list | None = None) -> int:
    """`collect`, if given, receives (variant, split, summary) for each variant that was run, for
    the layer table of `python -m evals.run all`."""
    split: Split = "heldout" if args.held_out else "dev"
    if args.held_out and args.by:
        print("error: --by is not available on held-out (totals only)", file=sys.stderr)
        return 1
    items = load_split(split)
    label = "held-out" if args.held_out else "dev"
    runner = RetrievalRunner()
    runs: dict[str, Run] = {}
    logged = [v.name for v in resolve(args.variants)]
    for variant in resolve(args.variants) + resolve(args.also):
        if variant.name not in runs:
            runs[variant.name] = runner.run(variant, items, label)
            print(
                f"ran {variant.name} on {len(items)} {label} questions", file=sys.stderr, flush=True
            )

    for tier, spent in runner.spend().items():
        print(
            f"hosted embedder {tier}: {spent['requests']} requests, {spent['tokens']} tokens, "
            f"cost {spent['cost']} credits",
            file=sys.stderr,
        )

    rows = [(name, label, summarize(run.scored)) for name, run in runs.items()]
    if collect is not None:
        collect.extend(rows)
    _print_table(rows)
    comparisons = _compare(runs)
    if comparisons:
        print("\npaired differences in recall@5 (second minus first), same questions:")
        for name, text, notes in comparisons:
            print(f"  {name:36} {text:22} {notes}")

    # A breakdown run logs only its breakdown rows: the totals were logged by the run before it.
    log_rows = (
        [_row(args.log, name, label, s) for name, _, s in rows if name in logged]
        if args.log and not args.by
        else []
    )
    for by in args.by or []:
        print(f"\nrecall@5 by {by}:")
        for name, run in runs.items():
            if name not in logged:
                continue
            for group, s in breakdown(run, by).items():
                print(f"  {name:20} {group:12} n={s.n:<3} {s.recall}")
                if args.log:
                    log_rows.append(_row(args.log, name, f"{label} [{by}={group}]", s))
    for name, text, notes in comparisons:
        if args.log and not args.by:
            log_rows.append(
                f"| {date.today().isoformat()} | {args.log} | {name} | {label}, paired difference "
                f"| {len(items)} | {text} | | | {notes} |\n"
            )
    if args.log:
        if not RESULTS.exists():
            RESULTS.write_text("# Experiment log\n\n" + LOG_HEADER)
        with RESULTS.open("a") as handle:
            handle.writelines(log_rows)
        print(f"\nappended {len(log_rows)} rows to {RESULTS.relative_to(EVALS_DIR.parent)}")

    if not args.held_out:  # per-question detail is never written for held-out
        LAST_RUN.write_text(
            json.dumps(
                {
                    name: {s.question.id: round(s.recall, 4) for s in run.scored}
                    for name, run in runs.items()
                },
                indent=1,
            )
        )
    if args.save_baseline:
        if len(runs) != 1:
            print("error: --save-baseline needs exactly one variant", file=sys.stderr)
            return 1
        ((name, run),) = runs.items()
        BASELINE.write_text(
            json.dumps(
                {"variant": name, "recall": {s.question.id: s.recall for s in run.scored}}, indent=1
            )
            + "\n"
        )
        print(f"saved baseline for {name} to {BASELINE.name}")
    if args.check_baseline:
        return _check_baseline(runner, runs)
    return 0


def _check_baseline(runner: RetrievalRunner, runs: dict[str, Run]) -> int:
    """Fail only if the baseline variant is confidently worse on the same questions (the paired
    95% interval of the change lies entirely below zero). With ~70 questions that catches a real
    drop, not noise."""
    saved = json.loads(BASELINE.read_text())
    name = saved["variant"]
    run = runs.get(name) or runner.run(VARIANTS[name], load_split("dev"), "dev")
    now = {s.question.id: s.recall for s in run.scored}
    if set(now) != set(saved["recall"]):
        print("baseline check: the questions changed since the baseline was saved; re-save it")
        return 1
    ids = sorted(now)
    diff = paired_difference([now[i] for i in ids], [saved["recall"][i] for i in ids])
    print(
        f"baseline check ({name}): recall@5 change {_signed(diff.interval.mean)} "
        f"[{_signed(diff.interval.low)}..{_signed(diff.interval.high)}], "
        f"{diff.wins} better, {diff.losses} worse"
    )
    if diff.interval.high < 0:
        print("FAIL: confidently worse than the baseline")
        return 1
    return 0


LAYERS: dict[str, Callable[[argparse.Namespace], int]] = {"retrieval": run_retrieval}


COMMANDS = (
    "all",
    "generate",
    "heldout",
    "ledger",
    "check-summaries",
    "disagreements",
    "noise",
    "noise-report",
)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in COMMANDS:
        from evals.pipeline import main as pipeline_main

        return pipeline_main(arguments)
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__)
    parser.add_argument("layer", choices=sorted(LAYERS))
    parser.add_argument("--variants", default="core", help=f"{' | '.join(GROUPS)} or a,b,c")
    parser.add_argument(
        "--also", default="", help="more variants to run for the comparisons, not logged"
    )
    parser.add_argument("--held-out", action="store_true")
    parser.add_argument("--by", choices=("style", "kind"), nargs="+")
    parser.add_argument("--log", metavar="CHANGE", help="append rows to results.md")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--check-baseline", action="store_true")
    args = parser.parse_args(arguments)
    try:
        return LAYERS[args.layer](args)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
