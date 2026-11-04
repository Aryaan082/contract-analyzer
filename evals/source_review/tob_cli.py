"""`python -m evals.source_review tob <command>`: Trail of Bits items, plain against pipeline.

pdfs       fetch the pinned PDFs into --cache (outside the repo), hash them, check them against the
           publications repository, write reports.toml. Read-only GETs; no model.
plan       rules 1 to 3 and 5 from the PDFs and GitHub's metadata (repository, commit, file
           listing). No source is downloaded. Writes reports.toml, files.toml, labels.toml.
fetch      REFUSES without --confirm-download. Downloads the in-scope source of the candidate
           reports at the pinned commits, checks every file against its git blob hash, applies the
           size ceiling and assigns the sides. No model.
estimate   the cost of the stages on the real prompts of both arms and the real bundle sizes. FREE:
           arithmetic only, no model. Reads the cached source (run `fetch` first).
show       what the rules decided, report by report, and the findings that would be looked for.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from evals.source_review import compare as cmp
from evals.source_review import costs
from evals.source_review import tob_items as ti
from evals.source_review.cards import REPO_ROOT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.source_review tob", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("pdfs", "plan", "fetch"):
        p = sub.add_parser(name)
        p.add_argument("--cache", type=Path, required=True, help="a folder outside the repo")
    sub.choices["fetch"].add_argument(
        "--confirm-download",
        action="store_true",
        help="you approved downloading the source of the repositories `show` lists",
    )
    est = sub.add_parser("estimate")
    est.add_argument("--cache", type=Path, required=True, help="the cache `fetch` filled")
    est.add_argument("--side", choices=(*ti.SIDES, "both"), default="both")
    sub.add_parser("show")
    return parser


def check_cache(cache: Path, out: Callable[[str], None]) -> bool:
    if REPO_ROOT in cache.resolve().parents or cache.resolve() == REPO_ROOT:
        out("--cache must be outside the repo: third-party text and code are never stored in it")
        return False
    return True


def cmd_pdfs(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    if not check_cache(args.cache, out):
        return 2
    gh = ti.GitHub(args.cache)
    reports = ti.pin_pdfs(gh)
    ti.write_reports(reports)
    out(f"pinned {len(reports)} PDFs ({sum(r.size for r in reports):,} bytes) -> {ti.REPORTS_PATH}")
    return 0


def cmd_plan(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    if not check_cache(args.cache, out):
        return 2
    gh = ti.GitHub(args.cache)
    plans = []
    for base in ti.load_reports():
        pdf = args.cache / "pdfs" / base.file
        if ti.git_blob_sha(pdf.read_bytes()) != base.blob:
            out(f"{base.slug}: the cached PDF is not the pinned one")
            return 2
        base = ti.Report(base.slug, base.file, base.size, base.sha256, base.blob)
        plans.append(ti.plan_report(base, pdf, gh))
    write(plans)
    out(f"{gh.calls} GitHub metadata requests (cached ones not counted); no source downloaded")
    return summary(plans, out)


def cmd_fetch(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    if not args.confirm_download:
        out("nothing was downloaded: add --confirm-download once Aryaan approved the repositories")
        return 2
    if not check_cache(args.cache, out):
        return 2
    gh = ti.GitHub(args.cache)
    reports = ti.load_reports()
    files = ti.load_files()
    labels = ti.load_labels()
    plans = [
        ti.Plan(r, files.get(r.slug, []), [lab for lab in labels if lab.report == r.slug])
        for r in reports
    ]
    plans = ti.finalize(plans, gh)
    write(plans)
    out(f"{gh.calls} downloads (cached ones not counted)")
    return summary(plans, out)


def write(plans: Sequence[ti.Plan]) -> None:
    ti.write_reports([p.report for p in plans])
    ti.write_files(
        {p.report.slug: p.files for p in plans if p.report.status in ("candidate", "kept")}
    )
    ti.write_labels(
        [lab for p in plans if p.report.status in ("candidate", "kept") for lab in p.labels]
    )


def summary(plans: Sequence[ti.Plan], out: Callable[[str], None]) -> int:
    for p in plans:
        r = p.report
        repos = ", ".join(
            f"{x.full_name}@{x.commit[:10]} [{x.licence}] {x.files_bytes:,} B" for x in r.repos
        )
        extra = f" side={r.side} tokens={r.tokens:,}" if r.kept else ""
        out(f"{r.status:<9} {r.slug}: {r.reason or f'{r.kept_findings} findings'}{extra}")
        if repos:
            out(f"{'':<9}   {repos}")
    kept = [p for p in plans if p.report.status in ("candidate", "kept")]
    out(
        f"{len(kept)} reports left, {sum(len(p.labels) for p in kept)} findings, "
        f"{sum(len(p.files) for p in kept)} in-scope files"
    )
    return 0


# Judge calls: the pair prompt is the flag text, the finding's title and description and the
# rubric (about 1,800 input tokens) and the reply is a verdict and a line (about 150 tokens, plus
# hidden thinking for a model that thinks first). Prices are those in evals/judge/judges.toml.
JUDGE_PAIR_IN = 1_800
JUDGE_PAIR_OUT = 150
JUDGE_THINKING = {"qwen/qwen3.7-flash": 1_500}  # median hidden tokens measured 2026-10-28
FLAGS_PER_RUN = 12  # an assumption until a run exists: plain gave 2 to 4 flags per single file


def judge_usd(pairs: int) -> float:
    from evals.judge.client import load_judges
    from evals.judge.policy import load_policy

    total = 0.0
    specs = load_judges()
    for slug in load_policy().modes["milestone"]:
        spec = specs[slug]
        out = JUDGE_PAIR_OUT + JUDGE_THINKING.get(slug, 0)
        total += pairs * (JUDGE_PAIR_IN * spec.input_per_mtok + out * spec.output_per_mtok) / 1e6
    return total


def cmd_estimate(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    reports = [r for r in ti.load_reports() if r.kept and args.side in ("both", r.side)]
    sources = ti.load_sources(reports, ti.load_files(), ti.GitHub(args.cache))
    labels = ti.load_labels()
    out("Cost estimate (arithmetic on the real prompts; NO MODEL HAS BEEN CALLED)")
    out(f"model {cmp.OPUS.name}: $4/Mtok in, $20/Mtok out; tokens = characters / 3.5")
    out(f"{'report':<52}{'side':<9}{'files':>6}{'strip tok':>10}{'P $':>8}{'A $':>8}{'labels':>7}")
    totals = {"P": 0.0, "A": 0.0, "P_lean": 0.0, "A_lean": 0.0}
    by_side: dict[str, dict[str, float]] = {}
    ceiling = 0.0
    pairs = 0
    for r in reports:
        item = type("Item", (), {"id": r.slug, "group": f"tob_{r.side}"})()
        est = cmp.estimate_item(item, sources[r.slug])  # type: ignore[arg-type]
        assert est is not None
        p = cmp.call_usd(est.p_tokens, est.p_calls * cmp.OUT_CENTRAL)
        a = cmp.call_usd(est.a_tokens, est.a_calls * cmp.OUT_CENTRAL)
        lean = cmp.OUT_LEAN
        totals["P"] += p
        totals["A"] += a
        totals["P_lean"] += cmp.call_usd(est.p_tokens, est.p_calls * lean)
        totals["A_lean"] += cmp.call_usd(est.a_tokens, est.a_calls * lean)
        n_labels = sum(lab.report == r.slug for lab in labels)
        pairs += n_labels * FLAGS_PER_RUN
        ceiling += 2 * cmp.call_usd(
            est.p_tokens * 3.5 / cmp.PESSIMISTIC_CHARS_PER_TOKEN, est.p_calls * 8192
        ) + cmp.call_usd(est.a_tokens * 3.5 / cmp.PESSIMISTIC_CHARS_PER_TOKEN, est.a_calls * 8192)
        side = by_side.setdefault(r.side, {"P": 0.0, "A": 0.0, "pairs": 0.0, "reports": 0.0})
        side["P"] += p
        side["A"] += a
        side["pairs"] += n_labels * FLAGS_PER_RUN * 3
        side["reports"] += 1
        out(
            f"{r.slug:<52}{r.side:<9}{len(sources[r.slug].files):>6}{r.tokens:>10,}"
            f"{p:>8.2f}{a:>8.2f}{n_labels:>7}  (P {est.p_calls} req, A {est.a_calls} req)"
        )
    for label, key in (("central (3,000 output tokens a request)", ""), ("lean (1,500)", "_lean")):
        p, a = totals["P" + key], totals["A" + key]
        out(
            f"{label}: screening (P) ${p:.2f}; scoring (fresh P + A) ${p + a:.2f}; "
            f"both ${2 * p + a:.2f}"
        )
    for name, v in sorted(by_side.items()):
        out(
            f"  {name}: {int(v['reports'])} reports; screening ${v['P']:.2f}, scoring "
            f"${v['P'] + v['A']:.2f}, judges ${judge_usd(int(v['pairs'])):.2f}"
        )
    judged = judge_usd(pairs * 3)
    out(
        f"judges (glm + qwen, {pairs * 3:,} pair calls assuming {FLAGS_PER_RUN} flags a run and "
        f"three judged runs per report): ${judged:.2f}"
    )
    out(f"all stages, central: ${2 * totals['P'] + totals['A'] + judged:.2f}")
    out(
        f"worst case for the agents (every request writes 8,192 tokens, 3.0 characters a token): "
        f"${ceiling:.2f}, plus judges ${judged:.2f}"
    )
    out(
        f"(measured on the Sherlock runs: P ${0.07:.2f} and A ${0.10:.2f} a single file; "
        f"{costs.CLAUDE_OUTPUT:,} output tokens a request is the pessimistic default)"
    )
    return 0


def cmd_show(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    reports, files, labels = ti.load_reports(), ti.load_files(), ti.load_labels()
    plans = [
        ti.Plan(r, files.get(r.slug, []), [lab for lab in labels if lab.report == r.slug])
        for r in reports
    ]
    summary(plans, out)
    for lab in labels:
        out(f"  {lab.id} {lab.severity}/{lab.difficulty} {lab.files} {lab.title[:70]}")
    return 0


def main(argv: Sequence[str] | None = None, out: Callable[[str], None] = print) -> int:
    args = build_parser().parse_args(argv)
    return {
        "pdfs": cmd_pdfs,
        "plan": cmd_plan,
        "fetch": cmd_fetch,
        "estimate": cmd_estimate,
        "show": cmd_show,
    }[args.command](args, out)


if __name__ == "__main__":
    sys.exit(main())
