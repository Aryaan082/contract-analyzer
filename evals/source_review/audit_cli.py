"""`python -m evals.source_review audit <command>`: judged audit items, plain against pipeline.

fetch      fetch the pinned files and the judging reports into --cache (outside the repo), run the
           pre-registered checks, write labels.toml. Read-only GETs; no model.
estimate   the cost on the real prompts of both arms. FREE.
run        the paid run on one side. REFUSES without --confirm-spend and --max-spend; held-out also
           needs --declare and a clean tree. Stops at the cap.
"""

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from evals.source_review import audit_items as ai
from evals.source_review import compare as cmp
from evals.source_review.audit_report import (
    build_audit_report,
    render_audit,
    today,
    write_audit_report,
)
from evals.source_review.cards import REPO_ROOT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.source_review audit", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "estimate", "run"):
        p = sub.add_parser(name)
        p.add_argument("--cache", type=Path, required=True, help="a folder outside the repo")
    for name in ("estimate", "run"):
        sub.choices[name].add_argument("--split", choices=("dev", "held_out"), required=True)
    run = sub.choices["run"]
    run.add_argument("--confirm-spend", action="store_true")
    run.add_argument("--max-spend", type=float)
    run.add_argument("--resume", type=Path, help="a checkpoint (.jsonl) of a stopped run")
    run.add_argument(
        "--declare", help="held-out only: why the code is frozen and this run is final"
    )
    return parser


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    return done.stdout.strip()


def cmd_fetch(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    items = ai.load_items()
    if REPO_ROOT in args.cache.resolve().parents:
        out("--cache must be outside the repo: third-party source is never stored in it")
        return 2
    results = ai.gather(items, args.cache, out=out)
    ai.write_labels(results)
    for side in ai.SIDES:
        got = [r for r in results if r.issue.split == side and r.accepted]
        files = {(r.issue.contest, r.issue.path) for r in got}
        unverified = sum(r.status == "unverified position" for r in got)
        out(f"{side}: {len(got)} issues accepted in {len(files)} files ({unverified} unverified)")
    out(f"wrote {ai.LABELS_PATH}")
    return 0


def prepare(args: argparse.Namespace):
    items = ai.load_items()
    files = ai.file_items(items, ai.load_labels(), args.split)
    return items, files, ai.load_sources(files, items, args.cache)


def cmd_estimate(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    _, files, sources = prepare(args)
    found = [e for f in files if (e := cmp.estimate_item(f, sources[f.id]))]
    out(f"{args.split}: {len(files)} files, {sum(len(f.issues) for f in files)} judged issues")
    for line in cmp.estimate_lines(found, {}):
        if line.startswith(("model", "costliest", "TOTAL", "lean")):
            out(line)
    return 0


def cmd_run(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    items, files, sources = prepare(args)
    central = sum(e.usd(ceiling=False) for f in files if (e := cmp.estimate_item(f, sources[f.id])))
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    problems = []
    if not args.confirm_spend:
        problems.append("--confirm-spend was not given")
    if args.max_spend is None:
        problems.append("--max-spend was not given")
    if not cmp.have_key():
        problems.append("OPENROUTER_API_KEY is not set")
    if args.split == "held_out" and not args.declare:
        problems.append("held-out needs --declare: why the code is frozen and this run is final")
    if args.split == "held_out" and dirty:
        problems.append("held-out needs a clean tree: commit the code first")
    out(f"{args.split}: {len(files)} files, central estimate ${central:.2f}")
    if problems:
        out("The run did not start, and no model was called:")
        out("\n".join(f"  - {p}" for p in problems))
        return 2
    if args.max_spend < central:
        out(f"NOTE: the cap ${args.max_spend:.2f} is below the estimate; the run will stop early.")
    stamp = f"{args.split}-{today()}"
    checkpoint = args.resume or cmp.RESULTS_DIR / f"audit-{stamp}.jsonl"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    done = cmp.read_checkpoint(checkpoint)
    budget = cmp.real_budget(args.max_spend)
    budget.spent = sum(r[a]["cost_usd"] for r in done.values() if "P" in r for a in ("P", "A"))
    records = cmp.run_compare(
        files,
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
        "date": today(),
        "split": args.split,
        "spent": round(budget.spent, 4),
        "max_spend": args.max_spend,
        "code_commit": git("rev-parse", "--short", "HEAD"),
        "dirty": dirty,
        "declared": args.declare or "",
        "checkpoint": checkpoint.name,
    }
    report = build_audit_report({f.id: f for f in files}, records, meta)
    js, md = write_audit_report(report, args.split)
    out("\n".join(render_audit(report)))
    out(f"\nwrote {js} and {md}")
    return 0


def main(argv: Sequence[str] | None = None, out: Callable[[str], None] = print) -> int:
    args = build_parser().parse_args(argv)
    return {"fetch": cmd_fetch, "estimate": cmd_estimate, "run": cmd_run}[args.command](args, out)


if __name__ == "__main__":
    sys.exit(main())
