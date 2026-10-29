"""`python -m evals.source_review compare <command>`: plain Opus 5.5 against our source review.

dry-run    both arms with scripted clients on the planted items: FREE, no model, no network
estimate   the cost on the exact item list, from the real prompts. Fetches third-party source
           through the read-only explorer (no model is called, nothing is written in the repo)
run        the paid run. REFUSES without --confirm-spend and --max-spend; stops at the cap
log        appends one row to evals/results.md from a finished report
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from evals.source_review import compare as cmp
from evals.source_review.cards import REPO_ROOT, load_cards

RESULTS_MD = REPO_ROOT / "evals" / "results.md"
LOG_HEADER = (
    "\n## Source review: plain model against our pipeline\n\n"
    "| date | change | model | arms | n scored | recall P / A (planted) | false alarm P / A "
    "(safe twins) | cost P / A | notes |\n|---|---|---|---|---|---|---|---|---|\n"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.source_review compare", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("dry-run", "estimate", "run"):
        p = sub.add_parser(name)
        p.add_argument("--exclude", default="", help="comma-separated item ids to leave out")
        p.add_argument("--planted-only", action="store_true", help="no contracts from the chain")
        if name != "dry-run":
            p.add_argument(
                "--cache-dir", type=Path, help="keep fetched bundles here (outside the repo)"
            )
    run = sub.choices["run"]
    run.add_argument(
        "--confirm-spend", action="store_true", help="you accept that this spends money"
    )
    run.add_argument("--max-spend", type=float, help="the most this run may spend, in USD")
    run.add_argument(
        "--resume", type=Path, help="a checkpoint (.jsonl) of a stopped run to continue"
    )
    log = sub.add_parser("log")
    log.add_argument("--report", type=Path, required=True, help="the compare-<date>.json to log")
    return parser


def selected(args: argparse.Namespace) -> list[cmp.Item]:
    skip = {x.strip() for x in args.exclude.split(",") if x.strip()}
    items = [i for i in cmp.dev_items(load_cards()) if i.id not in skip]
    if args.planted_only:
        items = [i for i in items if i.card.origin == "planted"]
    assert all(i.card.split == "dev" for i in items), "held-out items are never run here"
    return items


def sources_for(args: argparse.Namespace, items: Sequence[cmp.Item]) -> dict[str, cmp.Source]:
    needs_chain = any(i.card.contract for i in items)
    explorer = cmp.make_explorer() if needs_chain else None
    return cmp.load_sources(items, explorer=explorer, cache_dir=getattr(args, "cache_dir", None))


def print_estimate(
    items: Sequence[cmp.Item], sources: dict[str, cmp.Source], out: Callable[[str], None]
) -> float:
    found = [e for i in items if (e := cmp.estimate_item(i, sources[i.id]))]
    unreadable = {i.id: sources[i.id].reason for i in items if sources[i.id].bundle is None}
    for line in cmp.estimate_lines(found, unreadable):
        out(line)
    return sum(e.usd(ceiling=False) for e in found)


def cmd_dry_run(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    args.planted_only = True
    items = selected(args)
    sources = sources_for(args, items)
    records, spent = cmp.scripted_run(items, sources, out=out)
    report = cmp.build_report(
        records,
        load_cards(),
        {
            "model": "scripted",
            "date": cmp.today(),
            "items_total": len(items),
            "spent": spent,
            "max_spend": 1.0,
        },
    )
    for line in cmp.render(report):
        out(line)
    out("\nSCRIPTED: both arms were handed the labels. This tests the harness, not any model.")
    return 0


def cmd_run(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    items = selected(args)
    sources = sources_for(args, items)
    central = print_estimate(items, sources, out)
    problems = []
    if not args.confirm_spend:
        problems.append("--confirm-spend was not given")
    if args.max_spend is None:
        problems.append("--max-spend was not given")
    if not cmp.have_key():
        problems.append("OPENROUTER_API_KEY is not set")
    if problems:
        out("The run did not start, and no model was called:")
        out("\n".join(f"  - {p}" for p in problems))
        return 2
    if args.max_spend < central:
        out(
            f"NOTE: the cap ${args.max_spend:.2f} is below the central estimate "
            f"${central:.2f}; the "
            "run will stop early and the report lists what was not run."
        )
    checkpoint = args.resume or cmp.RESULTS_DIR / f"compare-{cmp.today()}.jsonl"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    done = cmp.read_checkpoint(checkpoint)
    budget = cmp.real_budget(args.max_spend)
    budget.spent = sum(r[a]["cost_usd"] for r in done.values() if "P" in r for a in ("P", "A"))
    out(f"running {len(items)} items ({len(done)} already done), cap ${args.max_spend:.2f}")
    records = cmp.run_compare(
        items,
        sources,
        lambda i, s: budget,
        lambda i, s: budget,
        spent=lambda: budget.spent,
        max_spend=args.max_spend,
        done=done,
        checkpoint=checkpoint,
        out=out,
    )
    meta = {
        "model": cmp.OPUS.name,
        "date": cmp.today(),
        "items_total": len(items),
        "spent": round(budget.spent, 4),
        "max_spend": args.max_spend,
        "refused_requests": budget.refused,
        "checkpoint": checkpoint.name,
    }
    report = cmp.build_report(records, load_cards(), meta)
    js, md = cmp.write_report(report)
    out("\n".join(cmp.render(report)))
    out(f"\nwrote {js} and {md}")
    return 0


def cmd_log(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    report = json.loads(args.report.read_text())
    row = cmp.results_row(report)
    text = RESULTS_MD.read_text()
    if row in text:
        out("that row is already in evals/results.md")
        return 1
    if "## Source review: plain model against our pipeline" not in text:
        text = text.rstrip("\n") + "\n" + LOG_HEADER
    RESULTS_MD.write_text(text.rstrip("\n") + "\n" + row + "\n")
    out("appended one row to evals/results.md")
    return 0


def main(argv: Sequence[str] | None = None, out: Callable[[str], None] = print) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "dry-run":
        return cmd_dry_run(args, out)
    if args.command == "estimate":
        items = selected(args)
        print_estimate(items, sources_for(args, items), out)
        return 0
    if args.command == "run":
        return cmd_run(args, out)
    return cmd_log(args, out)


if __name__ == "__main__":
    sys.exit(main())
