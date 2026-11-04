"""The paid and judging commands of `python -m evals.source_review tob`.

screen      plain Opus once per report of a side (stage 1). REFUSES without --confirm-spend and
            --max-spend; held-out also needs --declare and a clean tree.
judge       the pairwise judge over the flags of a stage. Without --confirm-spend it only prints the
            estimate. REFUSES a judge from the model's own company, and an unapproved rubric unless
            --draft (a draft result says so and is not for quoting).
score       the fresh plain run and the pipeline run on the reports that have a hard finding, as
            judged in screening (stage 2). Same refusals as `screen`.
agreement   free: do the judges differ; which one is the odd one out, and which way it leans.
handcheck   free: writes a sample of judged pairs for a person to label, judges and arm hidden.
report      free: the results file, the run records without any reviewer text, a results row.
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
from evals.source_review.cards import REPO_ROOT
from evals.source_review.compare import Source

CREDIT_MARGIN = 0.05


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
    problems += self_grading(judges, cmp.OPUS.name, policy)
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


def estimate_stage(ctx: Context, stage: str, reports: list[ti.Report]) -> float:
    return sum(tr.stage_estimate_usd(ctx.sources[r.slug], stage, r.slug) for r in reports)


def run_stages(
    args: argparse.Namespace,
    ctx: Context,
    stages: list[str],
    reports: list[ti.Report],
    out: Callable[[str], None],
) -> int:
    central = sum(estimate_stage(ctx, s, reports) for s in stages)
    out(
        f"{args.side}: {len(reports)} reports, stages {', '.join(stages)}, "
        f"central estimate ${central:.2f}"
    )
    problems = money_problems(args, args.side)
    if problems:
        out("The run did not start, and no model was called:")
        out("\n".join(f"  - {p}" for p in problems))
        return 2
    if args.max_spend < central:
        out(f"NOTE: the cap ${args.max_spend:.2f} is below the estimate; the run will stop early.")
    done = {s: tr.read_stage(ctx.cache, args.side, s) for s in stages}
    budget = cmp.real_budget(args.max_spend)  # --max-spend caps what THIS command spends
    with_credit_limit(budget, out)
    for stage in stages:
        out(f"stage {stage}:")
        tr.run_stage(
            reports, ctx.sources, stage, budget, spent=lambda: budget.spent,
            max_spend=budget.max_usd, done=done[stage],
            checkpoint=tr.checkpoint_path(ctx.cache, args.side, stage),
            max_tokens=args.plain_max_tokens if tr.ARM_OF[stage] == "P" else None, out=out,
        )  # fmt: skip
    for message in sorted(set(budget.errors)):
        out(f"request failed: {message}")
    out(f"spent ${budget.spent:.3f} of ${args.max_spend:.2f}")
    return 0


def cmd_screen(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    ctx = load_context(args.cache, args.side)
    return run_stages(args, ctx, ["screen"], ctx.reports, out)


def hard_reports(
    ctx: Context, args: argparse.Namespace, out: Callable[[str], None]
) -> list[ti.Report] | None:
    judges, _, rubric, problems = judge_setup(args, out)
    if problems or rubric is None:
        out("\n".join(problems))
        return None
    records = tr.read_stage(ctx.cache, args.side, "screen")
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
    ctx = load_context(args.cache, args.side)
    reports = hard_reports(ctx, args, out)
    if reports is None:
        return 2
    return run_stages(args, ctx, ["fresh", "pipeline"], reports, out)


# ---- judge -------------------------------------------------------------------------------------


def stage_jobs(
    ctx: Context, args: argparse.Namespace, stage: str, judges, rubric
) -> list[tj.PairJob]:
    records = tr.read_stage(ctx.cache, args.side, stage)
    return [
        job
        for judge in judges
        for job in tr.judge_jobs(
            rubric, judge, ctx.refs, ctx.labels_of, records, ctx.sources, stage
        )
    ]


def cmd_judge(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    judges, specs, rubric, problems = judge_setup(args, out)
    if rubric is None or not judges:
        out("\n".join(problems))
        return 2
    ctx = load_context(args.cache, args.side)
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
    out(f"judge spend ${cap.spent:.3f} of ${args.max_spend:.2f}")
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
    ctx = load_context(args.cache, args.side)
    cache = JudgeCache(ctx.cache / "judge")
    records = {s: tr.read_stage(ctx.cache, args.side, s) for s in tr.STAGES}
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
    ctx = load_context(args.cache, args.side)
    cache = JudgeCache(ctx.cache / "judge")
    records = tr.read_stage(ctx.cache, args.side, args.stage)
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
    judges, specs, rubric, problems = judge_setup(args, out)
    if rubric is None or not judges:
        out("\n".join(problems))
        return 2
    ctx = load_context(args.cache, args.side)
    cache = JudgeCache(ctx.cache / "judge")
    records = {s: tr.read_stage(ctx.cache, args.side, s) for s in tr.STAGES}
    replaced = {s: tr.replaced_usd(ctx.cache, args.side, s) for s in tr.STAGES}
    verd = {
        s: tr.verdicts(rubric, judges, ctx.refs, ctx.labels_of, records[s], ctx.sources, cache)
        for s in tr.STAGES
    }
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
    judge_usd = judge_cost(cache.rows(), {r.slug for r in ctx.reports})
    agreement = tp.judge_agreement(all_pairs(ctx, rubric, judges, cache, records), judges)
    meta = {
        "model": cmp.OPUS.name, "date": tp.today(), "side": args.side, "judges": list(judges),
        "judge_label": label, "quotable": quotable and tj.is_approved(rubric),
        "rubric": rubric.sha256, "rubric_approved": tj.is_approved(rubric),
        "code_commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "judge_usd": round(judge_usd, 4),
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
    for stage in tr.STAGES:
        rows = [tr.strip_notes(r) for r in records[stage].values() if "flags" in r]
        if rows:
            path = cmp.RESULTS_DIR / f"tob-{args.side}-{stage}-{meta['date']}.jsonl"
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


def add_parsers(sub: argparse._SubParsersAction) -> None:
    def common(p: argparse.ArgumentParser, *, side_both: bool = False) -> None:
        p.add_argument("--cache", type=Path, required=True)
        p.add_argument(
            "--side", choices=(*ti.SIDES, "both") if side_both else ti.SIDES, required=True
        )

    def judges(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--mode", choices=("quick", "milestone"), help="default: the three-judge panel"
        )
        p.add_argument("--judge", action="append", help="an extra judge the policy lists")

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
        if name == "score":
            judges(p)
    p = sub.add_parser("judge")
    common(p)
    judges(p)
    spend(p)
    p.add_argument("--stage", choices=(*tr.STAGES, "all"), default="all")
    p.add_argument("--draft", action="store_true", help="allow a rubric Aryaan has not approved")
    p.add_argument("--workers", type=int, default=4)
    p = sub.add_parser("handcheck")
    common(p)
    judges(p)
    p.add_argument("--stage", choices=tr.STAGES, default="screen")
    p.add_argument("--n", type=int, default=24)
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("report")
    common(p)
    judges(p)
    p = sub.add_parser("agreement")
    common(p)
    judges(p)
    sub.add_parser("rubric")


COMMANDS = {
    "screen": cmd_screen, "score": cmd_score, "judge": cmd_judge, "handcheck": cmd_handcheck,
    "report": cmd_report, "rubric": cmd_rubric, "agreement": cmd_agreement,
}  # fmt: skip
