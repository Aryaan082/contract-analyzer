"""`python -m evals.source_review <command>`: the evaluation kit for source review.

dry-run     everything end to end with the scripted reviewer: FREE, no model, no network
hostile     the hostile suite of note 3.5 (gate 1, scripted); --case H06 runs one case
cards       the cards, their counts and how many labels nobody has reviewed
split       checks split.toml against the cards and the rule (never rewrites it)
planted     --write regenerates the renamed copies; the default checks they are current
estimates   the T1, T2, T2b and T3 cost estimates (arithmetic; nothing is spent)
t1          gate 2: the real-model smoke run. REFUSES unless --confirm-spend and --max-spend
            are both given, the estimate is covered and Aryaan has approved the criteria.
            It has never been run.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from evals.source_review import costs, dryrun, planted, split
from evals.source_review.cards import ORIGINS, load_cards, problems
from evals.source_review.hostile import run_suite
from evals.source_review.hostile_cases import cases
from evals.source_review.t1 import run_t1, t1_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.source_review", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run", help="everything, scripted: free")
    dry.add_argument("--out", type=Path, help="also write a small JSON results file here")
    hostile = sub.add_parser("hostile", help="the hostile suite, gate 1: free")
    hostile.add_argument("--case", action="append", help="run only this case (H01, H06, ...)")
    sub.add_parser("cards", help="the cards and their counts")
    sub.add_parser("split", help="check split.toml against the cards")
    pl = sub.add_parser("planted", help="the planted variants' renamed copies")
    pl.add_argument("--write", action="store_true", help="regenerate renamed.sol files")
    est = sub.add_parser("estimates", help="the T1, T2, T2b and T3 estimates")
    est.add_argument("--repeats", type=int, default=3, help="T1 repeats (default 3)")
    t1 = sub.add_parser("t1", help="gate 2: real reviewer, hostile pairs and planted items")
    t1.add_argument(
        "--confirm-spend", action="store_true", help="you accept that this spends money"
    )
    t1.add_argument("--max-spend", type=float, help="the most this run may spend, in USD")
    t1.add_argument("--repeats", type=int, help="repeats per scenario (default: the criteria's)")
    t1.add_argument("--cases", help="comma-separated case ids (default: every pair case)")
    return parser


def cmd_cards(out: Callable[[str], None]) -> int:
    cards = load_cards()
    unreviewed, drafted = cards.unreviewed_labels()
    for origin in ORIGINS:
        members = cards.of(origin)
        held = sum(c.split == "held_out" for c in members)
        out(
            f"{origin:<9} {len(members):>3} cards, {sum(c.labelled for c in members):>3} labelled, "
            f"{held} held out"
        )
    out(f"{unreviewed} of {drafted} drafted labels unreviewed ({drafted} of {len(cards)} cards)")
    found = problems(cards)
    for p in found:
        out(f"PROBLEM: {p}")
    return 1 if found else 0


def main(argv: Sequence[str] | None = None, out: Callable[[str], None] = print) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "dry-run":
        return dryrun.main(out, args.out)
    if args.command == "hostile":
        wanted = set(args.case or [])
        chosen = [c for c in cases() if not wanted or c.id in wanted]
        if wanted - {c.id for c in cases()}:
            out(f"unknown case(s): {sorted(wanted - {c.id for c in cases()})}")
            return 2
        result = run_suite(chosen)
        for line in result.lines():
            out(line)
        out(f"{sum(c.passed for c in result.cases)} of {len(result.cases)} cases pass")
        return 0 if result.passed else 1
    if args.command == "cards":
        return cmd_cards(out)
    if args.command == "split":
        found = split.check(load_cards())
        for p in found:
            out(f"PROBLEM: {p}")
        out("split.toml agrees with the cards and the rule" if not found else "split.toml is wrong")
        return 1 if found else 0
    if args.command == "planted":
        if args.write:
            for path in planted.write_renamed():
                out(f"wrote {path}")
            return 0
        stale = planted.stale_renamed()
        for name in stale:
            out(f"STALE: {name}/renamed.sol (run with --write)")
        out("renamed copies are current" if not stale else "renamed copies are out of date")
        return 1 if stale else 0
    if args.command == "estimates":
        for line in costs.render(costs.all_stages(t1_plan(args.repeats))):
            out(line)
        return 0
    if args.command == "t1":
        case_ids = [c.strip() for c in args.cases.split(",")] if args.cases else None
        return run_t1(
            confirm_spend=args.confirm_spend,
            max_spend=args.max_spend,
            repeats=args.repeats,
            case_ids=case_ids,
            out=out,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
