"""The commands behind `make eval`, `make eval-run` and `make eval-heldout`.

    all                 retrieval on dev (local small models) + scoring of the latest stored dev
                        run-set; free.  `make eval`
    generate            ESTIMATE ONLY; `--go --max-spend X` makes a run-set.  `make eval-run`
    heldout --declare   a milestone look at held-out, totals only.  `make eval-heldout`
    ledger              what the paid commands spent
    check-summaries     score the committed summaries again and compare (CI)

(each is `python -m evals.run <command>`)

What each does and does not do is in evals/README.md. In short: only `generate` (and `heldout
--generate`) can spend money, only with `--go`, only up to `--max-spend`, and a judge only when
`--judge-mode` or `--judge` asks for one.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.config import load_config
from agent.runmeta import environment
from evals import spend
from evals.cards import CardSet, load_cards
from evals.disagreements import list_disagreements, render
from evals.disagreements import write as write_text
from evals.generate import AgentRunner, generate, judge_runset, live_agent_runner, plan_runs
from evals.judge.cache import CACHE_DIR, JudgeCache
from evals.judge.client import JudgeClient, JudgeSpec, OpenRouterJudge, load_judges
from evals.judge.policy import load_policy, policy_block, resolve_judges, self_grading
from evals.judge.rubric import load_rubric
from evals.judge.runner import SpendCap
from evals.results_file import RESULTS_DIR, write_results
from evals.runset import (
    RUNSETS_DIR,
    SUMMARIES_DIR,
    RunSet,
    build_summary,
    check_summary,
    create_runset,
    dataset_matches,
    describe_dataset,
    latest_runset,
    load_runset,
    load_summary,
    new_manifest,
    read_runs,
    results_manifest,
    unique_name,
    write_summary,
)
from evals.score import format_report, score
from evals.table import layer_table

DATASETS = Path(__file__).resolve().parent / "datasets"
DEV_CARDS = DATASETS / "contracts.toml"
RetrievalFn = Callable[[argparse.Namespace, list], int]

JUDGE_MODE_HELP = (
    "judge the reports afterwards: quick = one judge (z-ai/glm-5.3-flash, results labelled 'single "
    "judge, not for quoting'); milestone = two judges from two companies (z-ai/glm-5.3-flash and "
    "qwen/qwen3.7-flash) for milestone runs, the held-out look, the transfer check, the final run "
    "and any number that will be quoted. There is no default: no mode, no judge. Aryaan approved "
    "the rubric on 2026-10-28 (evals/judge/policy.toml); a changed rubric needs a new approval"
)
JUDGE_HELP = (
    "an extra judge on top of --judge-mode (or alone); only deepseek/deepseek-v4.1-flash-1017 is "
    "allowed, and only for agents from other companies. A judge never grades an agent from its own "
    "company: deepseek cannot judge the eval-tier agent, which is DeepSeek"
)


def _out(message: str = "") -> None:
    print(message, flush=True)


def retrieval_namespace(held_out: bool) -> argparse.Namespace:
    return argparse.Namespace(
        variants="core",
        also="",
        held_out=held_out,
        by=None,
        log=None,
        save_baseline=False,
        check_baseline=False,
    )


def _default_retrieval(args: argparse.Namespace, collect: list) -> int:
    from evals.run import run_retrieval

    return run_retrieval(args, collect)


# ---- scoring a run-set ---------------------------------------------------------------------------


def usable_judges(manifest: dict[str, Any]) -> tuple[tuple[str, ...], list[str]]:
    """The run-set's judges that may count: a judge from the agent's own company is left out (with a
    plain line saying so), however its judgements got into the cache."""
    policy = load_policy()
    named = tuple(manifest["judges"]["slugs"])
    refused = self_grading(named, manifest["model"]["slug"], policy)
    skipped = {j for j in named if any(j in line for line in refused)}
    return tuple(j for j in named if j not in skipped), refused


def score_runset(
    runset: RunSet,
    cards: CardSet,
    *,
    cards_path: Path,
    judge_cache: JudgeCache | None,
    resamples: int,
    declared: str | None = None,
    ignore_dataset_change: bool = False,
) -> tuple[dict[str, Any] | None, list[Any], str | None]:
    """(results, runs, problem). `problem` is why nothing was scored, if nothing was."""
    manifest = runset.manifest
    if not ignore_dataset_change:
        try:
            dataset_matches(manifest, cards_path, cards)
        except ValueError as err:
            return None, [], f"{err}; use --ignore-dataset-change to score anyway"
    runs, skipped = read_runs(runset)
    for note in skipped:
        _out(f"skipped: {note}")
    if not runs:
        return None, [], f"run-set {runset.name} has no readable traces"
    slugs, refused = usable_judges(manifest)
    for line in refused:
        _out(f"not counted: {line}")
    results = score(
        runs,
        cards,
        split=manifest["kind"],
        label=runset.name,
        declared=declared,
        resamples=resamples,
        judge_cache=judge_cache,
        judges=slugs,
        runset=results_manifest(manifest),
    )
    return results, runs, None


def _score_command(
    args: argparse.Namespace,
    kind: str,
    cards_path: Path,
    retrieval: RetrievalFn | None,
    *,
    declared: str | None,
) -> int:
    cards = load_cards(cards_path)
    wrong = [c.id for c in cards if c.split != kind]
    if wrong:
        _out(
            f"error: {cards_path.name} holds {len(wrong)} card(s) that are not in the {kind} split"
        )
        return 2
    rows: list | None = None
    status = 0
    if not args.no_retrieval:
        rows = []
        status = (retrieval or _default_retrieval)(retrieval_namespace(kind == "heldout"), rows)
    runset = (
        load_runset(Path(args.runset)) if args.runset else latest_runset(args.runsets_dir, kind)
    )
    results = None
    if runset is not None:
        results, runs, problem = score_runset(
            runset,
            cards,
            cards_path=cards_path,
            judge_cache=JudgeCache(args.judge_cache),
            resamples=args.resamples,
            declared=declared,
            ignore_dataset_change=args.ignore_dataset_change,
        )
        if problem:
            _out(f"error: {problem}")
            return 1
    _out()
    _out(layer_table(rows, results))
    if results is None:
        _out(
            f"\nno {kind} run-set yet: tool use, detection, answer quality, time, cost not scored."
        )
        _out("Make one with `make eval-run` (prints the estimate; spends nothing without --go).")
        return status
    _out()
    _out(format_report(results))
    if kind == "dev":
        _disagreement_hint(runset, runs, JudgeCache(args.judge_cache))
    path = write_results(results, runset.name, args.results_dir)
    _out(f"\nsaved {path}")
    if kind == "dev" and args.write_summary:
        labels = None
        counted, _ = usable_judges(runset.manifest)
        if counted:
            from evals.judge.faithfulness import collect_labels

            labels = collect_labels(
                runs, JudgeCache(args.judge_cache), counted, load_rubric().sha256
            )
            results = score(
                runs,
                cards,
                split="dev",
                label=runset.name,
                resamples=args.resamples,
                judge_labels=labels,
                runset=results_manifest(runset.manifest),
            )
            path = write_results(results, runset.name, args.results_dir)
        summary = build_summary(runset.manifest, runs, labels, path.name)
        _out(f"saved {write_summary(summary, args.summaries_dir)} (commit it with {path.name})")
    return status


def _disagreement_hint(runset: RunSet, runs: list[Any], cache: JudgeCache) -> None:
    """For a run-set judged by two judges: how many claims they split on, and how to read them."""
    judges, _ = usable_judges(runset.manifest)
    if len(judges) != 2 or runset.kind != "dev":
        return
    found = list_disagreements(runs, cache, judges, load_rubric().sha256)
    _out(
        f"the two judges disagree on {len(found)} claims or summary sentences; to review them: "
        f"python -m evals.run disagreements --runset {runset.path}"
    )


def cmd_disagreements(args: argparse.Namespace, **_: Any) -> int:
    runset = (
        load_runset(Path(args.runset)) if args.runset else latest_runset(args.runsets_dir, "dev")
    )
    if runset is None:
        _out("no dev run-set yet")
        return 0
    if runset.kind != "dev":
        _out("error: held-out has totals only; there is no per-claim list to show")
        return 2
    judges, refused = usable_judges(runset.manifest)
    for line in refused:
        _out(f"not counted: {line}")
    if len(judges) != 2:
        _out(f"this run-set has {len(judges)} usable judge(s); disagreements need two")
        return 0
    runs, _ = read_runs(runset)
    rubric = load_rubric().sha256
    text = render(
        list_disagreements(runs, JudgeCache(args.judge_cache), judges, rubric), judges, rubric
    )
    if args.out:
        write_text(Path(args.out), text)
        _out(f"written to {args.out}")
    else:
        _out(text)
    return 0


def cmd_all(args: argparse.Namespace, retrieval: RetrievalFn | None = None, **_: Any) -> int:
    return _score_command(args, "dev", args.cards or DEV_CARDS, retrieval, declared=None)


# ---- generating a run-set ----------------------------------------------------------------------


def cmd_generate(
    args: argparse.Namespace,
    *,
    make_runner: Callable[[Path], AgentRunner] | None = None,
    make_judge: Callable[[JudgeSpec], JudgeClient] | None = None,
    env: Mapping[str, str] | None = None,
    kind: str = "dev",
    cards_path: Path | None = None,
    **_: Any,
) -> int:
    cards_path = cards_path or args.cards or DEV_CARDS
    cards = load_cards(cards_path)
    wrong = [c.id for c in cards if c.split != kind]
    if wrong:
        _out(
            f"error: {cards_path.name} holds {len(wrong)} card(s) that are not in the {kind} split"
        )
        return 2
    chosen = list(cards)
    if args.contracts:
        wanted = {c.strip() for c in args.contracts.split(",") if c.strip()}
        chosen = [c for c in chosen if c.id in wanted]
        if len(chosen) != len(wanted):
            _out("error: --contracts names a card that is not in the file")
            return 2
    elif args.tool_use:
        chosen = [c for c in chosen if c.tool_use]
    if not chosen:
        _out("error: no contracts selected")
        return 2
    config = load_config()
    slug = config.resolve_model(args.model).name
    policy = load_policy()
    try:
        judges, judge_mode = resolve_judges(getattr(args, "judge_mode", None), args.judge, policy)
    except ValueError as err:
        _out(f"error: {err}")
        return 2
    refused = self_grading(judges, slug, policy)
    if refused:
        for line in refused:
            _out(line)
        return 2
    cap_default = spend.HELDOUT_CAP_USD if kind == "heldout" else spend.DEV_CAP_USD
    max_spend = args.max_spend if args.max_spend is not None else cap_default
    try:
        est = spend.estimate_generation(
            slug,
            len(chosen) * args.repeats,
            judges,
            per_run_usd=args.per_run_usd,
            judge_call_usd=args.judge_call_usd,
        )
    except spend.UnknownCost as err:
        _out(f"error: {err}")
        return 2
    specs = load_judges()
    for judge in judges:
        if judge not in specs:
            _out(f"error: judge {judge!r} is not in judges.toml (known: {', '.join(specs)})")
            return 2
    _out(f"{kind} run-set: {len(chosen)} contracts x {args.repeats} repeats, model {slug}")
    for line in spend.describe(est, max_spend):
        _out(line)
    judge_policy = policy_block(judges, mode=judge_mode, policy=policy) if judges else None
    if judge_policy:
        _out(f"judging: {judge_mode or 'custom'} ({', '.join(judges)}): {judge_policy['label']}")
        if judge_policy["rubric_notice"]:
            _out(f"note: {judge_policy['rubric_notice']}")
    decision = spend.decide(
        est,
        go=args.go,
        max_spend=max_spend,
        allow_expensive=args.allow_expensive,
        declared=bool(getattr(args, "declare", None)),
        kind=kind,
    )
    for line in decision.lines:
        _out(line)
    if not decision.proceed:
        if decision.lines[0].startswith("nothing was spent"):
            target = "eval-heldout" if kind == "heldout" else "eval-run"
            _out(
                f'with make: make {target} ARGS="--go" (add --judge-mode quick|milestone to judge)'
            )
            return 0
        return 2

    environment_facts = environment()
    name = unique_name(
        args.runsets_dir, args.name or f"{kind}-{args.model.split('/')[-1]}-r{args.repeats}"
    )
    dataset = describe_dataset(cards_path, cards, kind)
    manifest = new_manifest(
        name=name,
        kind=kind,
        model_tier=args.model,
        model_slug=slug,
        repeats=args.repeats,
        dataset=dataset,
        judges=judges,
        rubric_sha256=load_rubric().sha256 if judges else None,
        environment=environment_facts,
        models_toml_sha=environment_facts.get("models_toml_sha"),
        estimated_usd=est.total,
        max_spend_usd=max_spend,
        declared=getattr(args, "declare", None),
        workers=args.workers,
        judge_policy=judge_policy,
    )
    runset = create_runset(args.runsets_dir, manifest)
    if make_runner is not None:
        runner = make_runner(runset.traces)
    else:
        from chain.config import load_env

        load_env()
        runner = live_agent_runner(
            args.model, runset.traces, env if env is not None else os.environ
        )
    cap = SpendCap(max_spend)
    manifest["status"] = "running"
    runset.save()
    plan = plan_runs(chosen, args.repeats)
    _out(f"running {len(plan)} runs into {runset.path}")

    def progress(g: Any) -> None:
        if (g.done + g.failed) % 10 == 0:
            _out(f"  {g.done}/{g.planned} done, {g.failed} failed, ${g.spent_usd:.4f}")

    done = generate(
        runset, plan, runner, cap, ceiling=est.ceiling, workers=args.workers, progress=progress
    )
    agent_spent = cap.spent
    judged = []
    if judges and done.done:
        judged = judge_runset(
            runset,
            judges,
            JudgeCache(args.judge_cache),
            cap,
            make_judge or OpenRouterJudge,
            workers=args.workers if args.workers > 1 else 4,
        )
    if judges and done.done and kind == "dev":
        _disagreement_hint(runset, read_runs(runset)[0], JudgeCache(args.judge_cache))
    traces = runset.trace_files()
    first = _first_metrics(traces)
    manifest["completed_runs"] = len(traces)
    manifest["status"] = "complete" if done.stopped is None and not done.skipped else "stopped"
    manifest["stopped_because"] = done.stopped or next(
        (j.stopped for j in judged if j.stopped), None
    )
    manifest["spend"]["spent_usd"] = round(cap.spent, 6)
    manifest["hashes"].update(first)
    runset.save()
    spend.record(
        args.runsets_dir,
        {
            "command": "heldout --generate" if kind == "heldout" else "generate",
            "runset": name,
            "kind": kind,
            "model": slug,
            "runs_planned": len(plan),
            "runs_done": done.done,
            "estimated_usd": round(est.total, 6),
            "spent_usd": round(cap.spent, 6),
            "agent_usd": round(agent_spent, 6),
            "judges": list(judges),
            "judge_mode": judge_mode,
            "stopped": manifest["stopped_because"],
        },
    )
    _out(
        f"done: {done.done} of {len(plan)} runs, {done.failed} failed, "
        f"${agent_spent:.4f} on the agent, ${cap.spent - agent_spent:.4f} on judges, "
        f"${cap.spent:.4f} in all (cap ${max_spend:.2f})"
    )
    if manifest["stopped_because"]:
        _out(f"STOPPED: {manifest['stopped_because']}")
    for line in done.errors[:5]:
        _out(f"  failed: {line}")
    follow_up = "heldout --declare '<why>'" if kind == "heldout" else "all"
    _out(f"next: python -m evals.run {follow_up} --runset {runset.path}")
    return 3 if manifest["stopped_because"] else 0


def _first_metrics(traces: list[Path]) -> dict[str, str | None]:
    for path in traces:
        try:
            metrics = json.loads(path.read_text()).get("metrics") or {}
        except (OSError, ValueError):
            continue
        if metrics.get("system_prompt_sha"):
            return {
                "system_prompt_sha": metrics["system_prompt_sha"],
                "tools_sha": metrics.get("tools_sha"),
            }
    return {}


# ---- held-out ----------------------------------------------------------------------------------


def cmd_heldout(args: argparse.Namespace, retrieval: RetrievalFn | None = None, **kw: Any) -> int:
    if not args.declare or not args.declare.strip():
        _out("error: the held-out set is only looked at with --declare '<why this look is taken>'")
        return 2
    if args.cards is None:
        _out("error: give --cards <the held-out cards file> (the Makefile target does)")
        return 2
    if args.generate:
        status = cmd_generate(args, kind="heldout", cards_path=args.cards, **kw)
        if status != 0 or not args.go:
            return status
    return _score_command(args, "heldout", args.cards, retrieval, declared=args.declare)


# ---- the ledger, the committed summaries ---------------------------------------------------------


def cmd_ledger(args: argparse.Namespace, **_: Any) -> int:
    rows = spend.read_ledger(args.runsets_dir)
    if not rows:
        _out("the ledger is empty: no paid command has been run through evals.run yet")
    for row in rows:
        _out(
            f"{row['at']}  {row['command']:<18} {row['runset']:<34} "
            f"runs {row['runs_done']}/{row['runs_planned']}  "
            f"estimated ${row['estimated_usd']:.4f}  spent ${row['spent_usd']:.4f}"
            + (f"  STOPPED {row['stopped']}" if row.get("stopped") else "")
        )
    total = spend.total_spent(rows)
    _out(f"total through evals.run: ${total:.4f} of the roughly ${spend.ENVELOPE_USD:.0f} envelope")
    _out(
        "not in it: the eval-model smoke test ($1.64) and the judge trial ($0.2163), made before it"
    )
    return 0


def cmd_check_summaries(args: argparse.Namespace, **_: Any) -> int:
    paths = sorted(args.summaries_dir.glob("*.json")) if args.summaries_dir.exists() else []
    if not paths:
        _out(
            "no committed summaries yet: nothing to score again (the first milestone run makes one)"
        )
        return 0
    cards_path = args.cards or DEV_CARDS

    def cards_for(manifest: dict[str, Any]) -> CardSet:
        cards = load_cards(cards_path)
        dataset_matches(manifest, cards_path, cards)
        return cards

    failed = 0
    for path in paths:
        problems = check_summary(path, cards_for, args.results_dir, resamples=args.resamples)
        if problems:
            failed += 1
            _out(f"FAIL {path.name}: scoring it again does not give the committed results")
            for line in problems[:10]:
                _out(f"  {line}")
        else:
            _out(f"ok   {path.name}: scoring it again gives exactly the committed results file")
            committed = json.loads(
                (args.results_dir / load_summary(path)["results_file"]).read_text()
            )
            _out(layer_table(None, committed))
            _out()
    return 1 if failed else 0


# ---- the parser ------------------------------------------------------------


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cards", type=Path, help="the cards file (default: the dev cards)")
    p.add_argument("--runsets-dir", type=Path, default=RUNSETS_DIR, help=argparse.SUPPRESS)
    p.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help=argparse.SUPPRESS)
    p.add_argument("--summaries-dir", type=Path, default=SUMMARIES_DIR, help=argparse.SUPPRESS)
    p.add_argument(
        "--judge-cache", type=Path, default=CACHE_DIR, help="folder of cached judgements"
    )
    p.add_argument("--resamples", type=int, default=10_000)


def _score_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--runset", help="a run-set folder (default: the newest of this split)")
    p.add_argument("--no-retrieval", action="store_true", help="skip the retrieval layer")
    p.add_argument(
        "--ignore-dataset-change", action="store_true", help="score although the cards changed"
    )
    p.add_argument(
        "--write-summary", action="store_true", help="also write the committed summary (dev)"
    )


def _generate_args(p: argparse.ArgumentParser, *, heldout: bool) -> None:
    p.add_argument(
        "--model", default="eval", help="a tier from models.toml (eval, sonnet, haiku) or a slug"
    )
    p.add_argument("--repeats", type=int, default=3, help="runs per contract (default 3)")
    p.add_argument("--contracts", help="only these card ids, comma separated")
    p.add_argument("--tool-use", action="store_true", help="only the tool-use set (a quick run)")
    p.add_argument("--name", help="name of the run-set (default: from the commit and model)")
    p.add_argument(
        "--judge-mode", choices=sorted(load_policy().modes), default=None, help=JUDGE_MODE_HELP
    )
    p.add_argument("--judge", action="append", default=[], help=JUDGE_HELP)
    cap = spend.HELDOUT_CAP_USD if heldout else spend.DEV_CAP_USD
    p.add_argument(
        "--max-spend", type=float, help=f"hard cap in USD for the whole command (default {cap:g})"
    )
    p.add_argument(
        "--go",
        action="store_true",
        help="actually spend money; without it only the estimate is printed",
    )
    p.add_argument(
        "--allow-expensive",
        action="store_true",
        help="needed for a model dearer than $0.02 a run (Sonnet)",
    )
    p.add_argument(
        "--workers", type=int, default=1, help="runs at the same time (timings then do not count)"
    )
    p.add_argument(
        "--per-run-usd", type=float, help="cost per run for a model with no measured cost"
    )
    p.add_argument(
        "--judge-call-usd", type=float, help="cost per call for a judge with no measured cost"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    one = sub.add_parser("all", help="retrieval + scoring of the latest stored dev run-set (free)")
    _common(one)
    _score_args(one)
    one.set_defaults(run=cmd_all)

    two = sub.add_parser("generate", help="make a run-set: prints the estimate; --go spends")
    _common(two)
    _generate_args(two, heldout=False)
    two.set_defaults(run=cmd_generate)

    three = sub.add_parser("heldout", help="a milestone look at held-out, totals only")
    _common(three)
    _score_args(three)
    _generate_args(three, heldout=True)
    three.add_argument("--declare", help="why this look is being taken (required)")
    three.add_argument(
        "--generate", action="store_true", help="make a held-out run-set first (paid)"
    )
    three.set_defaults(run=cmd_heldout)

    four = sub.add_parser("ledger", help="what the paid commands spent")
    _common(four)
    four.set_defaults(run=cmd_ledger)

    five = sub.add_parser("check-summaries", help="score the committed summaries again and compare")
    _common(five)
    five.set_defaults(run=cmd_check_summaries)

    six = sub.add_parser("disagreements", help="where a run-set's two judges differ, with reasons")
    _common(six)
    six.add_argument("--runset", help="a dev run-set folder (default: the newest)")
    six.add_argument("--out", help="write the list (Markdown) to this file")
    six.set_defaults(run=cmd_disagreements)
    return parser


def main(argv: Sequence[str] | None = None, **services: Any) -> int:
    """`services` replace the real thing in tests: retrieval, make_runner, make_judge, env."""
    args = build_parser().parse_args(argv)
    return args.run(args, **services)


if __name__ == "__main__":
    sys.exit(main())
