"""The paid and judging commands of `python -m evals.source_review tob`.

screen      plain Opus once per report of a side (stage 1). REFUSES without --confirm-spend and
            --max-spend; held-out also needs --declare and a clean tree.
judge       the pairwise judge over the flags of a stage. Without --confirm-spend it only prints the
            estimate. REFUSES a judge from the model's own company, and an unapproved rubric unless
            --draft (a draft result says so and is not for quoting). Exits 3, not 0, while any pair
            still has no verdict (stopped at the cap, left over or unreadable): ask again.
score       the fresh plain run and the pipeline run on the reports that have a hard finding, as
            judged in screening (stage 2). Same refusals as `screen`.
agreement   free: do the judges differ; which one is the odd one out, and which way it leans.
handcheck   free: writes a sample of judged pairs for a person to label, judges and arm hidden.
report      free: the results file, the run records without any reviewer text, a results row.
            REFUSES while any pair lacks a verdict from a judge (the counts would be too low),
            unless --allow-partial, which prints a PARTIAL JUDGING banner on top.
rubric      the rubric's hash and whether it is the one Aryaan approved.

Every command takes --cache (the folder `fetch` filled; it also holds the runs and the judgements:
reviewers' sentences and the auditors' text never enter the repo).
"""

import argparse
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent.config import ModelSpec
from evals.judge.cache import JudgeCache
from evals.judge.client import OpenRouterJudge, load_judges
from evals.judge.policy import label as policy_label
from evals.judge.policy import load_policy, resolve_judges, self_grading
from evals.judge.runner import SpendCap
from evals.source_review import compare as cmp
from evals.source_review import tob_items as ti
from evals.source_review import tob_judge as tj
from evals.source_review import tob_report as tp
from evals.source_review import tob_run as tr
from evals.source_review import tob_wide as tw
from evals.source_review.cards import REPO_ROOT
from evals.source_review.compare import Source

EXIT_INCOMPLETE = 3  # `judge` ended with pairs that have no usable verdict

CREDIT_MARGIN = 0.05
SCORE_STAGES = ["fresh", "pipeline"]  # what `score` runs, in this order, unless --stages says less


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    return done.stdout.strip()


# ---- shared setup ------------------------------------------------------------------------------


@dataclass
class Context:
    cache: Path
    side: str
    reports: list[ti.Report]
    labels_of: dict[str, list[ti.Label]]
    sources: dict[str, Source]
    refs: dict[str, tj.Reference]
    runs: str = tr.RUNS  # which folder of the cache holds the stage records (`--runs`)
    key: str = "orig"  # "wide" adds the Low and Informational findings (`tob_wide`, dev only)
    wide_skipped: tuple[str, ...] = ()  # extra findings with no file in the code


def model_of(args: argparse.Namespace) -> ModelSpec:
    """The model under test: `--model` (opus unless said otherwise)."""
    return cmp.MODELS[getattr(args, "model", "opus")]


def reasoning_of(args: argparse.Namespace) -> str:
    """'off' (the model's hidden thinking is turned off) or '' (its own default). `auto` turns it
    off for DeepSeek: at 8,192 output tokens its thinking alone used the whole budget and the reply
    was empty (smoke test, 2026-11-04); Opus keeps its default, as in every pre-registered run."""
    choice = getattr(args, "reasoning", "auto")
    if choice == "auto":
        return "off" if getattr(args, "model", "opus") == "deepseek" else ""
    return "off" if choice == "off" else ""


def waiver_of(args: argparse.Namespace) -> str:
    """Why a judge from the model's own company is allowed (`--allow-same-company`), or ''."""
    return (getattr(args, "allow_same_company", None) or "").strip()


def key_of(args: argparse.Namespace) -> str:
    """Which key a command uses: `wide` (post-hoc secondary view) or `orig` (pre-registered)."""
    return getattr(args, "key", "orig")


def wide_problem(args: argparse.Namespace) -> str:
    """Why `--key wide` cannot be used here, or ''."""
    if key_of(args) == tw.KEY_NAME and args.side != "dev":
        return "the wide key is for the dev side only: the held-out rules are frozen"
    return ""


def context_for(args: argparse.Namespace) -> Context:
    """The context of the command's side, reading and writing the records of `--runs`. With
    `--key wide` (dev only) the Low and Informational findings are added to the labels."""
    ctx = load_context(args.cache, args.side)
    ctx.runs = getattr(args, "runs", tr.RUNS)
    return tw.widen(ctx) if key_of(args) == tw.KEY_NAME else ctx


def load_context(cache: Path, side: str, gh: ti.GitHub | None = None) -> Context:
    gh = gh or ti.GitHub(cache)
    reports = [r for r in ti.load_reports() if r.kept and side in ("both", r.side)]
    labels = [lab for lab in ti.load_labels() if lab.report in {r.slug for r in reports}]
    labels_of: dict[str, list[ti.Label]] = {}
    for lab in labels:
        labels_of.setdefault(lab.report, []).append(lab)
    sources = ti.load_sources(reports, ti.load_files(), gh)
    refs = tr.references(labels, tr.descriptions(cache, reports))
    return Context(cache, side, reports, labels_of, sources, refs)


def judge_setup(args: argparse.Namespace, out: Callable[[str], None]):
    """(judges, specs, rubric, problems) for the judge options on the command line. With neither
    --mode nor --judge the judges are the three of `tj.PANEL` (Aryaan, 2026-11-04): a majority of
    them decides a pair."""
    policy = load_policy()
    named = list(args.judge or []) if (args.mode or args.judge) else list(tj.PANEL)
    try:
        judges, _ = resolve_judges(args.mode, named, policy)
    except ValueError as err:
        return (), {}, None, [str(err)]
    problems = []
    if not judges:
        problems.append("no judge was named")
    refusals = self_grading(judges, model_of(args).name, policy)
    if refusals and waiver_of(args):
        out(f"WAIVED (dev only, the result is not quotable): {waiver_of(args)}")
        refusals = []
    problems += refusals
    specs = load_judges()
    problems += [
        f"judge {j} has no entry in evals/judge/judges.toml" for j in judges if j not in specs
    ]
    return judges, specs, tj.load_rubric(), problems


def money_problems(args: argparse.Namespace, side: str) -> list[str]:
    problems = []
    if not args.confirm_spend:
        problems.append("--confirm-spend was not given")
    if args.max_spend is None:
        problems.append("--max-spend was not given")
    if not cmp.have_key():
        problems.append("OPENROUTER_API_KEY is not set")
    if side == "held_out" and not getattr(args, "declare", None):
        problems.append("held-out needs --declare: why the code is frozen and this run is final")
    if side == "held_out" and git("status", "--porcelain", "--untracked-files=no"):
        problems.append("held-out needs a clean tree: commit the code first")
    return problems


def with_credit_limit(budget: cmp.Budget, out: Callable[[str], None]) -> None:
    left = cmp.credits_remaining()
    if left is None:
        return
    out(f"OpenRouter credits left: ${left:.2f}")
    budget.max_usd = min(budget.max_usd, budget.spent + max(left - CREDIT_MARGIN, 0.0))


# ---- screen and score --------------------------------------------------------------------------


def estimate_stage(
    ctx: Context,
    stage: str,
    reports: list[ti.Report],
    spec: ModelSpec = cmp.OPUS,
    pipeline: str = "single",
    plain_variant: str = "plain",
) -> float:
    return sum(
        tr.stage_estimate_usd(ctx.sources[r.slug], stage, r.slug, spec, pipeline, plain_variant)
        for r in reports
    )


def run_stages(
    args: argparse.Namespace,
    ctx: Context,
    stages: list[str],
    reports: list[ti.Report],
    out: Callable[[str], None],
) -> int:
    spec = model_of(args)
    pipeline = getattr(args, "pipeline", "single")
    variant = getattr(args, "plain_variant", "plain")
    central = sum(estimate_stage(ctx, s, reports, spec, pipeline, variant) for s in stages)
    out(
        f"{args.side}: {len(reports)} reports, stages {', '.join(stages)}, model {spec.name}, "
        f"pipeline {pipeline}, plain variant {variant}, central estimate ${central:.2f}"
        + (
            f" (two-pass: arm A scaled by {tr.PASS_FACTOR['two_pass']}, from one measurement)"
            if pipeline == "two_pass"
            else ""
        )
    )
    problems = money_problems(args, args.side)
    for stage in stages:
        if tr.ARM_OF[stage] == "A":
            made = {
                r.get("pipeline_mode", "single")
                for r in tr.read_stage(ctx.cache, args.side, stage, ctx.runs).values()
            }
            if made - {pipeline}:
                problems.append(
                    f"the {stage} records in --runs {ctx.runs} were made with pipeline "
                    f"{', '.join(sorted(made))}; use another --runs folder for {pipeline}"
                )
    if variant != "plain":
        if any(tr.ARM_OF[stage] == "A" for stage in stages):
            problems.append(
                f"--plain-variant {variant} changes only the plain stages (screen, fresh): "
                "run the pipeline stage in another command"
            )
        for stage in stages:
            if tr.ARM_OF[stage] == "P":
                for r in tr.read_stage(ctx.cache, args.side, stage, ctx.runs).values():
                    if r.get("plain_variant", "plain") != variant:
                        problems.append(
                            f"the {stage} records in --runs {ctx.runs} were made with plain "
                            f"variant {r.get('plain_variant', 'plain')}; use another --runs folder"
                        )
                        break
    else:
        for stage in stages:
            if tr.ARM_OF[stage] == "P":
                made = {
                    r.get("plain_variant", "plain")
                    for r in tr.read_stage(ctx.cache, args.side, stage, ctx.runs).values()
                }
                if made - {"plain"}:
                    problems.append(
                        f"the {stage} records in --runs {ctx.runs} were made with plain variant "
                        f"{', '.join(sorted(made - {'plain'}))}; use another --runs folder"
                    )
    if problems:
        out("The run did not start, and no model was called:")
        out("\n".join(f"  - {p}" for p in problems))
        return 2
    if args.max_spend < central:
        out(f"NOTE: the cap ${args.max_spend:.2f} is below the estimate; the run will stop early.")
    done = {s: tr.read_stage(ctx.cache, args.side, s, ctx.runs) for s in stages}
    reasoning = reasoning_of(args)
    out(f"reasoning: {reasoning or 'the model default'}")
    budget = cmp.real_budget(args.max_spend, spec, reasoning)  # --max-spend caps THIS command
    with_credit_limit(budget, out)
    # replies that are not bare JSON are kept, raw, outside the repo (see tr.Recording)
    raw = tr.Recording(budget, ctx.cache / ctx.runs / f"{args.side}-raw-replies.jsonl")
    for stage in stages:
        out(f"stage {stage}:")
        raw.stage = stage
        tr.run_stage(
            reports, ctx.sources, stage, raw, spent=lambda: budget.spent,
            max_spend=budget.max_usd, done=done[stage],
            checkpoint=tr.checkpoint_path(ctx.cache, args.side, stage, ctx.runs),
            max_tokens=args.plain_max_tokens if tr.ARM_OF[stage] == "P" else None, out=out,
            begin=raw.begin, spec=spec, pipeline=pipeline, reasoning=reasoning,
            plain_variant=variant,
        )  # fmt: skip
    for message in sorted(set(budget.errors)):
        out(f"request failed: {message}")
    out(f"spent ${budget.spent:.3f} of ${args.max_spend:.2f}")
    return 0


def cmd_screen(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    ctx = context_for(args)
    return run_stages(args, ctx, ["screen"], ctx.reports, out)


def hard_reports(
    ctx: Context, args: argparse.Namespace, out: Callable[[str], None]
) -> list[ti.Report] | None:
    judges, _, rubric, problems = judge_setup(args, out)
    if problems or rubric is None:
        out("\n".join(problems))
        return None
    records = tr.read_stage(ctx.cache, args.side, "screen", ctx.runs)
    cache = JudgeCache(ctx.cache / "judge")
    v = tr.verdicts(rubric, judges, ctx.refs, ctx.labels_of, records, ctx.sources, cache)
    hard, unjudged = tr.hard_labels(v)
    if not v:
        out("the screening run has not been judged: run `screen` and `judge --stage screen` first")
        return None
    if unjudged:
        out(f"{len(unjudged)} screening pairs have no usable verdict; judge them again first")
        return None
    slugs = {slug for (slug, lid) in v if lid in hard}
    out(f"hard set: {len(hard)} findings in {len(slugs)} reports")
    return [r for r in ctx.reports if r.slug in slugs]


def cmd_score(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    ctx = context_for(args)
    reports = hard_reports(ctx, args, out)
    if reports is None:
        return 2
    wanted = getattr(args, "stages", None) or SCORE_STAGES
    return run_stages(args, ctx, [s for s in SCORE_STAGES if s in wanted], reports, out)


# ---- judge -------------------------------------------------------------------------------------


def stage_jobs(
    ctx: Context, args: argparse.Namespace, stage: str, judges, rubric
) -> list[tj.PairJob]:
    records = tr.read_stage(ctx.cache, args.side, stage, ctx.runs)
    return [
        job
        for judge in judges
        for job in tr.judge_jobs(
            rubric, judge, ctx.refs, ctx.labels_of, records, ctx.sources, stage
        )
    ]


def cmd_judge(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    if problem := wide_problem(args):
        out(problem)
        return 2
    judges, specs, rubric, problems = judge_setup(args, out)
    if rubric is None or not judges:
        out("\n".join(problems))
        return 2
    ctx = context_for(args)
    cache = JudgeCache(ctx.cache / "judge")
    stages = list(tr.STAGES) if args.stage == "all" else [args.stage]
    jobs = [j for s in stages for j in stage_jobs(ctx, args, s, judges, rubric)]
    approved = tj.is_approved(rubric)
    label, quotable = policy_label(judges, load_policy())
    out(
        f"rubric {rubric.sha256[:12]}: {'approved' if approved else 'NOT approved by Aryaan'}; "
        f"judges {', '.join(judges)} ({label})"
    )
    total = 0.0
    for judge in judges:
        est = tj.estimate_pairs([j for j in jobs if j.model == judge], cache, specs[judge])
        total += est["typical_usd"]
        out(
            f"  {judge}: {est['pairs']} pairs, {est['to_call']} to call, about "
            f"${est['typical_usd']:.3f} (worst case ${est['worst_usd']:.3f})"
        )
    if not approved and not args.draft:
        problems.append("the rubric is not approved: add --draft for a result that says so")
    if not args.confirm_spend or args.max_spend is None:
        problems.append("--confirm-spend and --max-spend are both needed to call a judge")
    if problems:
        out("The run did not start, and no judge was called:")
        out("\n".join(f"  - {p}" for p in problems))
        return 2
    cap = SpendCap(args.max_spend)
    incomplete = False
    for judge in judges:
        client = OpenRouterJudge(specs[judge])
        run = tj.run_pairs(
            [j for j in jobs if j.model == judge], client, cache, cap, spec=specs[judge],
            workers=args.workers,
        )  # fmt: skip
        out(
            f"  {judge}: called {run.called}, cached {run.cached}, unreadable {run.unparsed}, "
            f"left {run.left}{', stopped: ' + run.stopped if run.stopped else ''}"
        )
        incomplete = incomplete or bool(run.stopped or run.left or run.unparsed)
    out(f"judge spend ${cap.spent:.3f} of ${args.max_spend:.2f}")
    if incomplete:
        out(
            "NOT COMPLETE: some pairs still have no verdict (stopped at the cap, left over, or "
            "unreadable). Run `judge` again; `score` and `report` refuse until every judge has "
            "answered every pair."
        )
        return EXIT_INCOMPLETE
    return 0


# ---- hand-check --------------------------------------------------------------------------------


def judge_cost(rows: list[dict], slugs: set[str]) -> float:
    """Dollars the judge cache holds for the pairs of these reports (one row per pair; the failed
    tries of a pair that was asked again are not in the cache and are not counted)."""
    return sum(r.get("cost_usd", 0.0) for r in rows if r.get("report") in slugs)


def all_pairs(ctx: Context, rubric, judges, cache, records) -> list[tp.PairView]:
    """Every pair that every judge has answered, over the records of all stages."""
    out: list[tp.PairView] = []
    for stage in tr.STAGES:
        out += tp.collect_pairs(
            rubric, judges, ctx.refs, ctx.labels_of, records.get(stage, {}), ctx.sources, cache
        )
    return out


def cmd_agreement(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    """Free: do the judges differ, and is one the odd one out? Reads the cached judgements."""
    judges, _, rubric, problems = judge_setup(args, out)
    if rubric is None or not judges:
        out("\n".join(problems))
        return 2
    ctx = context_for(args)
    cache = JudgeCache(ctx.cache / "judge")
    records = {s: tr.read_stage(ctx.cache, args.side, s, ctx.runs) for s in tr.STAGES}
    pairs = all_pairs(ctx, rubric, judges, cache, records)
    ja = tp.judge_agreement(pairs, judges)
    if not ja.get("pairs"):
        out("no pair has been answered by every judge yet")
        return 2
    out("\n".join(tp.render_agreement(ja)))
    return 0


def cmd_handcheck(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    judges, _, rubric, problems = judge_setup(args, out)
    if rubric is None or not judges:
        out("\n".join(problems))
        return 2
    ctx = context_for(args)
    cache = JudgeCache(ctx.cache / "judge")
    records = tr.read_stage(ctx.cache, args.side, args.stage, ctx.runs)
    pairs = tp.collect_pairs(rubric, judges, ctx.refs, ctx.labels_of, records, ctx.sources, cache)
    if not pairs:
        out("no judged pairs yet: run the stage and `judge` first")
        return 2
    sample = tp.sample_pairs(pairs, n=args.n, seed=args.seed)
    folder = ctx.cache / "handcheck"
    folder.mkdir(parents=True, exist_ok=True)
    plain = folder / f"{args.side}-{args.stage}.md"
    key = folder / f"{args.side}-{args.stage}-key.md"
    plain.write_text("\n".join(tp.render_handcheck(sample)) + "\n")
    key.write_text("\n".join(tp.render_handcheck(sample, reveal=True)) + "\n")
    kinds = {
        k: sum(p.combined == k for p in sample)
        for k in ("same", "same_place", "unrelated", "disputed")
    }
    out(f"{len(sample)} of {len(pairs)} judged pairs sampled {kinds}")
    out(f"for you to label (judges hidden): {plain}")
    out(f"with the judges' answers, for me: {key}")
    return 0


# ---- report ------------------------------------------------------------------------------------


def cmd_report(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    if problem := wide_problem(args):
        out(problem)
        return 2
    judges, specs, rubric, problems = judge_setup(args, out)
    if rubric is None or not judges:
        out("\n".join(problems))
        return 2
    note = (getattr(args, "note", None) or "").strip()
    runs = getattr(args, "runs", tr.RUNS)
    if runs != tr.RUNS and not note:
        out(f"--runs {runs} is not the pre-registered run: say why with --note (printed on top)")
        return 2
    ctx = context_for(args)
    cache = JudgeCache(ctx.cache / "judge")
    records = {s: tr.read_stage(ctx.cache, args.side, s, ctx.runs) for s in tr.STAGES}
    replaced = {s: tr.replaced_usd(ctx.cache, args.side, s, ctx.runs) for s in tr.STAGES}
    verd = {
        s: tr.verdicts(rubric, judges, ctx.refs, ctx.labels_of, records[s], ctx.sources, cache)
        for s in tr.STAGES
    }
    partial = {s: sum(v.unjudged for v in verd[s].values()) for s in tr.STAGES}
    partial = {s: n for s, n in partial.items() if n}
    if partial and not getattr(args, "allow_partial", False):
        out(
            "refused: pairs have no verdict from every judge ("
            + ", ".join(f"{s} {n}" for s, n in partial.items())
            + "), so the counts would be too low. Run `judge` until it ends with exit 0, or pass "
            "--allow-partial for a report that says so on top."
        )
        return 2
    flags = {
        s: {
            slug: tr.unmatched_flags(
                rubric, judges, ctx.refs, ctx.labels_of, rec, ctx.sources[slug], cache
            )
            for slug, rec in records[s].items()
            if tr.finished(rec) and slug in ctx.sources
        }
        for s in tr.STAGES
    }
    label, quotable = policy_label(judges, load_policy())
    waived = [j for j in judges if self_grading([j], model_of(args).name, load_policy())]
    waiver = waiver_of(args) if waived else ""
    if waiver:
        label, quotable = f"{label}; {', '.join(waived)} is the model's own company", False
    judge_usd = judge_cost(cache.rows(), {r.slug for r in ctx.reports})
    agreement = tp.judge_agreement(all_pairs(ctx, rubric, judges, cache, records), judges)
    meta = {
        "model": model_of(args).name, "date": tp.today(), "side": args.side,
        "judges": list(judges), "judge_waiver": waiver,
        "stage_models": tp.stage_models(records),
        "judge_label": label, "quotable": quotable and tj.is_approved(rubric),
        "rubric": rubric.sha256, "rubric_approved": tj.is_approved(rubric),
        "code_commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "judge_usd": round(judge_usd, 4), "runs": runs, "note": note,
        "partial_judging": partial,
        "key": key_of(args), "wide_skipped": list(ctx.wide_skipped),
    }  # fmt: skip
    report = tp.build_report(
        tp.Inputs(
            ctx.reports,
            [lab for ls in ctx.labels_of.values() for lab in ls],
            verd,
            records,
            flags,
            meta,
            replaced,
            agreement,
        )
    )
    js, md = tp.write_report(report, args.side)
    suffix = tp.runs_suffix(runs)
    for stage in tr.STAGES:
        if key_of(args) == tw.KEY_NAME:
            break  # the same run records as the report of the pre-registered key: already filed
        if runs != tr.RUNS and records[stage] == tr.read_stage(ctx.cache, args.side, stage):
            continue  # an unchanged copy of the pre-registered records: they are already filed
        rows = [tr.strip_notes(r) for r in records[stage].values() if "flags" in r]
        if rows:
            path = cmp.RESULTS_DIR / f"tob-{args.side}-{stage}-{meta['date']}{suffix}.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out("\n".join(tp.render(report)))
    out(f"\nwrote {js} and {md}")
    out("row for evals/results.md:\n" + tp.results_row(report))
    return 0


def cmd_rubric(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    rubric = tj.load_rubric()
    out(f"rubric {tj.RUBRIC_PATH}: hash {rubric.sha256}")
    out(
        f"approved hash: {tj.approved_hash() or '(none)'} -> "
        f"{'APPROVED' if tj.is_approved(rubric) else 'not approved'}"
    )
    return 0


# ---- the parser --------------------------------------------------------------------------------


def runs_name(text: str) -> str:
    """A folder name inside the cache: lower-case letters, digits, - and _ (no path)."""
    if not tr.RUNS_NAME.match(text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a plain folder name")
    return text


def add_parsers(sub: argparse._SubParsersAction) -> None:
    def common(p: argparse.ArgumentParser, *, side_both: bool = False) -> None:
        p.add_argument("--cache", type=Path, required=True)
        p.add_argument(
            "--side", choices=(*ti.SIDES, "both") if side_both else ti.SIDES, required=True
        )
        p.add_argument(
            "--model", choices=sorted(cmp.MODELS), default="opus",
            help="the model under test (both arms); opus is what the pre-registered runs used",
        )  # fmt: skip
        p.add_argument(
            "--reasoning", choices=("auto", "default", "off"), default="auto",
            help="the model's hidden thinking: auto = off for deepseek, default for opus",
        )  # fmt: skip
        p.add_argument(
            "--runs", type=runs_name, default=tr.RUNS,
            help="the folder of the cache that holds the stage records; a re-run that must not "
            "touch the pre-registered records goes to its own (copy screen and fresh into it)",
        )  # fmt: skip

    def judges(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--mode", choices=("quick", "milestone"), help="default: the three-judge panel"
        )
        p.add_argument("--judge", action="append", help="an extra judge the policy lists")
        p.add_argument(
            "--allow-same-company", metavar="WHY",
            help="development only: allow a judge from the model's own company; the result says "
            "so and is not quotable",
        )  # fmt: skip

    def key(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--key", choices=("orig", tw.KEY_NAME), default="orig",
            help="wide: dev only, adds the Low and Informational findings, each judged against "
            "the flags in its own files; a post-hoc secondary view, never the headline",
        )  # fmt: skip

    def spend(p: argparse.ArgumentParser) -> None:
        p.add_argument("--confirm-spend", action="store_true")
        p.add_argument("--max-spend", type=float)

    for name in ("screen", "score"):
        p = sub.add_parser(name)
        common(p)
        spend(p)
        p.add_argument(
            "--declare", help="held-out only: why the code is frozen and this run is final"
        )
        p.add_argument(
            "--plain-max-tokens", type=int, default=tr.OUTPUT_LIMIT["P"],
            help="output limit of a plain request; a record cut off at a lower limit is run again",
        )  # fmt: skip
        p.add_argument(
            "--plain-variant", choices=tr.PLAIN_VARIANTS, default="plain",
            help="plain (pre-registered) or checklist: the same request with the pipeline's "
            "checklist added as a reminder, an ablation that needs its own --runs folder",
        )  # fmt: skip
        if name == "score":
            judges(p)
            p.add_argument(
                "--stages", nargs="+", choices=SCORE_STAGES, default=SCORE_STAGES,
                help="which stages to run (default both)",
            )  # fmt: skip
            p.add_argument(
                "--pipeline", choices=tr.PIPELINES, default="single",
                help="how arm A reads: single (pre-registered) or two_pass (leads, then checks)",
            )  # fmt: skip
    p = sub.add_parser("judge")
    common(p)
    judges(p)
    spend(p)
    p.add_argument("--stage", choices=(*tr.STAGES, "all"), default="all")
    p.add_argument("--draft", action="store_true", help="allow a rubric Aryaan has not approved")
    p.add_argument("--workers", type=int, default=4)
    key(p)
    p = sub.add_parser("handcheck")
    common(p)
    judges(p)
    p.add_argument("--stage", choices=tr.STAGES, default="screen")
    p.add_argument("--n", type=int, default=24)
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("report")
    common(p)
    judges(p)
    p.add_argument("--note", help="needed with --runs: why this is not the pre-registered run")
    key(p)
    p.add_argument(
        "--allow-partial", action="store_true",
        help="write the report although some pairs have no verdict from every judge "
        "(it says PARTIAL JUDGING on top and its counts are too low)",
    )  # fmt: skip
    p = sub.add_parser("agreement")
    common(p)
    judges(p)
    sub.add_parser("rubric")


COMMANDS = {
    "screen": cmd_screen, "score": cmd_score, "judge": cmd_judge, "handcheck": cmd_handcheck,
    "report": cmd_report, "rubric": cmd_rubric, "agreement": cmd_agreement,
}  # fmt: skip
