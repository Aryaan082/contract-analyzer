"""`python -m evals.judge`: build and read the check set offline; judge only with `--go`.

    rubric                                      the rubric hash and whether it is the recorded one
    checkset build --traces DIR                 rebuild checkset/ from the dev traces (offline)
    checkset show                               what is in it
    checkset estimate --judge SLUG              calls, tokens and cost of judging it, nothing sent
    checkset run --judge SLUG --max-spend 0.2 --go     judge it (PAID: needs OPENROUTER_API_KEY)
    checkset report --judge A [--judge B] [--stability A]    catch, false alarm, agreement (offline)
    run TRACES --judge SLUG --max-spend 1 --go    judge the claims and summaries of traces (PAID)

Without `--go` nothing is sent. With it, every call is counted against `--max-spend`, a hard cap:
the run stops before a call that could pass it.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from evals.judge import checkset as cs
from evals.judge.cache import CACHE_DIR, JudgeCache
from evals.judge.client import JudgeSpec, ModelUnavailable, OpenRouterJudge, load_judges
from evals.judge.inputs import claim_inputs, judgeable, summary_input
from evals.judge.rubric import load_rubric, recorded_hash
from evals.judge.runner import (
    Job,
    Price,
    RunSummary,
    SpendCap,
    claim_job,
    estimate,
    run_jobs,
    summary_job,
)
from evals.runs import load_runs


def _out(message: str = "") -> None:
    print(message, flush=True)


def _spec(args: argparse.Namespace, slug: str) -> JudgeSpec:
    known = load_judges().get(slug)
    if args.price_in is not None and args.price_out is not None:
        return JudgeSpec(
            slug=slug,
            input_per_mtok=args.price_in,
            output_per_mtok=args.price_out,
            max_tokens=args.max_tokens or (known.max_tokens if known else 800),
            structured=known.structured if known else "json_schema",
        )
    if known is None:
        raise SystemExit(
            f"{slug} is not in judges.toml: give --price-in and --price-out "
            "(USD per million tokens)"
        )
    return (
        known
        if not args.max_tokens
        else JudgeSpec(**{**known.__dict__, "max_tokens": args.max_tokens})
    )


def _progress(summary: RunSummary) -> None:
    done = summary.called
    if done % 10 == 0:
        _out(f"  {done} calls, ${summary.spent_usd:.4f} spent, {summary.unparsed} refused replies")


def _spend(jobs_by_model: dict[str, list[Job]], args: argparse.Namespace, cache: JudgeCache) -> int:
    total_worst = total_typical = 0.0
    plans = {}
    for slug, jobs in jobs_by_model.items():
        spec = _spec(args, slug)
        price = Price(spec.input_per_mtok, spec.output_per_mtok)
        plan = estimate(
            jobs,
            cache,
            price,
            spec.max_tokens,
            typical_out=args.typical_out,
            retry_unparsed=args.retry_unparsed,
        )
        plans[slug] = (spec, price, plan)
        total_worst += plan["worst_cost_usd"]
        total_typical += plan["typical_cost_usd"]
        _out(
            f"{slug}: {plan['to_call']} calls to make ({plan['cached']} cached), about "
            f"{plan['input_tokens']:,} input tokens; typical ${plan['typical_cost_usd']:.4f}, "
            f"worst case ${plan['worst_cost_usd']:.4f}"
        )
    _out(f"total: typical ${total_typical:.4f}, worst case ${total_worst:.4f}")
    if not args.go:
        _out("nothing sent (add --go --max-spend X to judge)")
        return 0
    if args.max_spend is None:
        _out("error: --go needs --max-spend")
        return 2
    cap = SpendCap(args.max_spend)
    code = 0
    for slug, (spec, price, plan) in plans.items():
        if not plan["to_call"]:
            continue
        _out(f"judging with {slug} (cap ${args.max_spend:.2f} for the whole command)")
        try:
            client = OpenRouterJudge(spec)
            result = run_jobs(
                jobs_by_model[slug],
                client,
                cache,
                cap,
                price=price,
                max_tokens=spec.max_tokens,
                workers=args.workers,
                retry_unparsed=args.retry_unparsed,
                progress=_progress,
            )
        except ModelUnavailable as err:
            _out(f"error: {err}")
            return 1
        _out(
            f"  done: {result.called} called, {result.parsed} parsed, {result.unparsed} refused "
            f"{dict(result.refused) or ''}, {result.left} left, ${result.spent_usd:.4f} so far"
        )
        if result.stopped:
            _out(f"  STOPPED: {result.stopped}")
            code = 3
            break
    return code


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--judge", action="append", default=[], help="a judge model slug")
    parser.add_argument("--cache", type=Path, default=CACHE_DIR)
    parser.add_argument("--price-in", type=float, help="USD per million input tokens")
    parser.add_argument("--price-out", type=float, help="USD per million output tokens")
    parser.add_argument("--max-tokens", type=int, help="most tokens a reply may use")
    parser.add_argument(
        "--typical-out", type=int, default=150, help="answer tokens per call, for the estimate"
    )
    parser.add_argument("--max-spend", type=float, help="hard cap in USD for this command")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--repeat", type=int, default=0, help="0 normally; 1 for the stability pass"
    )
    parser.add_argument("--retry-unparsed", action="store_true")
    parser.add_argument("--go", action="store_true", help="actually call the judge (costs money)")


def cmd_rubric(args: argparse.Namespace) -> int:
    rubric = load_rubric()
    _out(f"rubric sha256 {rubric.sha256}")
    _out(f"recorded      {recorded_hash()}")
    return 0 if rubric.sha256 == recorded_hash() else 1


def cmd_build(args: argparse.Namespace) -> int:
    rows, evidence = cs.build(args.traces)
    cs.write(rows, evidence)
    _out(f"wrote {len(rows)} items and {len(evidence)} evidence texts to {cs.CHECKSET_DIR}")
    return cmd_show(args)


def cmd_show(args: argparse.Namespace) -> int:
    items = cs.load_items()
    claims = [i for i in items if i.task == "claim"]
    faults = [i for i in claims if i.row["fault"]]
    _out(
        f"{len(claims)} claims ({len(faults)} faults, {len(claims) - len(faults)} sound) and "
        f"{len(items) - len(claims)} summaries"
    )
    _out(f"fault origins: {dict(Counter(i.row['origin'] for i in faults))}")
    planted = Counter(i.row["operator"] for i in faults if i.row["origin"] == "planted")
    _out(f"planted by operator: {dict(planted)}")
    units = [(u, f) for i in items for u, f in i.scored()]
    _out(f"scored units: {len(units)} ({sum(f for _, f in units)} faults)")
    return 0


def _checkset_jobs(args: argparse.Namespace) -> dict[str, list[Job]]:
    rubric = load_rubric()
    items = cs.load_items()
    return {slug: cs.jobs_for(items, rubric, slug, args.repeat) for slug in args.judge}


def cmd_estimate_or_run(args: argparse.Namespace) -> int:
    if not args.judge:
        _out("error: give at least one --judge")
        return 2
    return _spend(_checkset_jobs(args), args, JudgeCache(args.cache))


def cmd_report(args: argparse.Namespace) -> int:
    rubric = load_rubric()
    report = cs.evaluate(
        cs.load_items(),
        JudgeCache(args.cache),
        args.judge,
        rubric.sha256,
        stability_models=args.stability,
    )
    _out(json.dumps(report, indent=1))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if not args.judge:
        _out("error: give at least one --judge")
        return 2
    rubric = load_rubric()
    runs, skipped = load_runs(args.traces)
    for note in skipped:
        _out(f"skipped: {note}")
    jobs: dict[str, list[Job]] = {slug: [] for slug in args.judge}
    for run in runs:
        if not judgeable(run):
            continue
        for slug in args.judge:
            jobs[slug] += [claim_job(c, rubric, slug, args.repeat) for c in claim_inputs(run)]
            summary = summary_input(run)
            if summary is not None:
                jobs[slug].append(summary_job(summary, rubric, slug, args.repeat))
    return _spend(jobs, args, JudgeCache(args.cache))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.judge", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("rubric").set_defaults(run=cmd_rubric)

    check = sub.add_parser("checkset").add_subparsers(dest="action", required=True)
    build = check.add_parser("build")
    build.add_argument("--traces", type=Path, required=True)
    build.set_defaults(run=cmd_build)
    check.add_parser("show").set_defaults(run=cmd_show)
    for name in ("estimate", "run"):
        one = check.add_parser(name)
        _common(one)
        one.set_defaults(run=cmd_estimate_or_run)
    rep = check.add_parser("report")
    rep.add_argument("--judge", action="append", default=[])
    rep.add_argument("--cache", type=Path, default=CACHE_DIR)
    rep.add_argument("--stability", action="append", default=[])
    rep.set_defaults(run=cmd_report)

    runs = sub.add_parser("run")
    runs.add_argument("traces", nargs="+", type=Path)
    _common(runs)
    runs.set_defaults(run=cmd_run)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
