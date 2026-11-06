"""Score the layered system with the reasoning verifier on its OUTPUT (Aryaan, 2026-11-06).

The tables in the README ("The layered system with verifier v3") come from here. A run pair is a
plain run and a pipeline run of the same reports; `claims` gives every distinct flag of the pairs
its own verifier verdict, and `score` counts, for each system, the places it would show, the
places that match an auditor's finding and the findings found, from judgements already in the
cache (no model call):

    A  plain alone                      the plain run's places
    U  every flag shown                 the union of the plain and pipeline flags, by place
    V  union, shown by the verifier     a place is shown if one of its flags passes a rule; a
                                        finding counts as found only through a flag that passed

"Right" in the README is `credited / shown`: a place where a flag was judged the same bug as an
auditor's finding. Writing more flags cannot raise it.

    python -m evals.source_review.verifier_eval claims --side dev --cache C \
        --confirm-spend --max-spend 3
    python -m evals.source_review.verifier_eval score --side dev --cache C
    python -m evals.source_review.verifier_eval pipeline-all --side held_out --runs held-p1 ...
    python -m evals.source_review.verifier_eval score --side held_out --exploratory-held-out ...

`claims` and `pipeline-all` call a model: they print an estimate and refuse without
`--confirm-spend` and `--max-spend`; held-out also needs `--declare` and a clean tree, as every
other paid command does. Answers are cached by prompt hash, so asking again costs nothing. The
verifier is DeepSeek V4.1 Flash with thinking off. Results are dev-tuned and not quotable (draft
rubric; DeepSeek also judges): see `evals/datasets/tob_items/README.md`.
"""

import argparse
import hashlib
import json
import random
import statistics as st
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.judge.cache import JudgeCache
from evals.source_review import compare as cmp
from evals.source_review import tob_cli
from evals.source_review import tob_eval_cli as cli
from evals.source_review import tob_judge as tj
from evals.source_review import tob_run as tr
from review import layered as ly
from review.client import ModelUnavailable, OpenRouterReviewer, ReviewerCall
from review.types import Flag
from verify import reason
from verify.types import Candidate

NAME = "v3flash"  # the label of the verifier run: names its cache and its verdict file
EST_USD_PER_FLAG = 0.0016  # measured $0.0014 a flag on DeepSeek V4.1 Flash, thinking off
DEFAULT_PAIRS = {
    "dev": (
        "ds:screen+ds3:pipeline",
        "ds:fresh+rep-t2:pipeline",
        "rep-p3:screen+rep-t3:pipeline",
        "rep-p4:screen+rep-t4:pipeline",
        "rep-p5:screen+rep-t5:pipeline",
    ),
    "held_out": ("held-p1:screen+held-p1:pipeline",),
}
BASE_SYSTEMS = ("A plain alone", "U every flag shown")
# Verdict rules over a flag's record. "low+" is the severity gate of `verify.stage.review_list`.
SEVERITY_UP = {"medium+": ("medium", "high"), "low+": ("low", "medium", "high"), "high": ("high",)}
Rule = Callable[[Mapping | None], bool]
RULES: dict[str, Rule] = {
    "valid": lambda r: bool(r and r["verdict"] == "valid"),
    "valid + uncertain": lambda r: bool(r and r["verdict"] in ("valid", "uncertain")),
    "valid, severity medium+": lambda r: bool(
        r and r["verdict"] == "valid" and r["severity"] in SEVERITY_UP["medium+"]
    ),
    "valid, severity low+": lambda r: bool(
        r and r["verdict"] == "valid" and r["severity"] in SEVERITY_UP["low+"]
    ),
    "valid, severity high": lambda r: bool(
        r and r["verdict"] == "valid" and r["severity"] == "high"
    ),
    "valid, not informational": lambda r: bool(
        r and r["verdict"] == "valid" and r["severity"] != "informational"
    ),
    "valid, difficulty not high": lambda r: bool(
        r and r["verdict"] == "valid" and r["difficulty"] != "high"
    ),
}
# Dev only: the verifier's rules were written from the first two and the last two are totals only.
TUNE = ("2025-05-caplabs-coveredagentprotocol", "2023-05-fraxgov")
CHECK = ("2023-07-arcade", "2025-10-radiustechnology-evmauth")


# ---- the pure core: counting what a system shows -------------------------------------------------


@dataclass(frozen=True)
class FlagRow:
    """One flag of one run, with what the judges decided about it."""

    place: tuple  # (file, contract, function): `review.layered.place_key`
    claim_id: str  # `verify.types.Candidate.id`: the key of its verifier verdict
    labels: frozenset[str]  # the findings (of the key in use) it was judged the same bug as
    plain: bool  # raised by the plain reader (the other flags are the pipeline's)


def counts(rows: Iterable[FlagRow]) -> tuple[int, int, int]:
    """(places shown, places that match a finding, findings found) over the rows given."""
    by_place: dict[tuple, set[str]] = {}
    for row in rows:
        by_place.setdefault(row.place, set()).update(row.labels)
    found: set[str] = set().union(*by_place.values()) if by_place else set()
    return len(by_place), sum(bool(v) for v in by_place.values()), len(found)


def evaluate(
    runs: Sequence[Mapping[str, Sequence[FlagRow]]],
    slugs: Sequence[str],
    verdicts: Mapping[str, Mapping],
    rules: Mapping[str, Rule] = RULES,
) -> dict[str, dict[str, tuple[float, float, float]]]:
    """system -> report -> (places shown, places that match, findings found), the mean of the
    runs. A flag with no verdict passes no rule."""
    per_run: dict[str, dict[str, list[tuple[int, int, int]]]] = {
        name: {slug: [] for slug in slugs} for name in (*BASE_SYSTEMS, *rules)
    }
    for run in runs:
        for slug in slugs:
            rows = run.get(slug, ())
            per_run[BASE_SYSTEMS[0]][slug].append(counts(r for r in rows if r.plain))
            per_run[BASE_SYSTEMS[1]][slug].append(counts(rows))
            for name, rule in rules.items():
                per_run[name][slug].append(
                    counts(r for r in rows if rule(verdicts.get(r.claim_id)))
                )
    return {
        name: {slug: tuple(st.mean(c[i] for c in v) for i in range(3)) for slug, v in by.items()}
        for name, by in per_run.items()
    }  # type: ignore[misc]


def interval(
    fn: Callable[[Sequence[str]], float], slugs: Sequence[str], draws: int = 10_000
) -> tuple[float, float]:
    """The 95% bootstrap interval of `fn` over the reports (not over the runs)."""
    rng = random.Random(0)
    values = sorted(fn([rng.choice(slugs) for _ in slugs]) for _ in range(draws))
    return values[int(0.025 * draws)], values[int(0.975 * draws)]


def total(
    means: Mapping[str, Mapping[str, Sequence[float]]], name: str, slugs: Sequence[str], i: int
) -> float:
    return sum(means[name][s][i] for s in slugs)


@dataclass(frozen=True)
class Decision:
    """A system against plain alone, on the pre-registered rule: more findings than plain (not
    fewer), a credited share above plain's with the interval excluding 0, and fewer places shown."""

    share: float
    share_interval: tuple[float, float]
    found: float
    found_interval: tuple[float, float]
    shown: float

    @property
    def met(self) -> bool:
        return self.share_interval[0] > 0 and self.found >= 0 and self.shown < 0


def decide(
    means: Mapping[str, Mapping[str, Sequence[float]]], name: str, slugs: Sequence[str]
) -> Decision:
    a, v = BASE_SYSTEMS[0], name

    def share(reps: Sequence[str]) -> float:
        vn, an = total(means, v, reps, 0), total(means, a, reps, 0)
        vc, ac = total(means, v, reps, 1), total(means, a, reps, 1)
        return (vc / vn if vn else 0.0) - (ac / an if an else 0.0)

    def found(reps: Sequence[str]) -> float:
        return total(means, v, reps, 2) - total(means, a, reps, 2)

    return Decision(
        share(slugs), interval(share, slugs), found(slugs), interval(found, slugs),
        total(means, v, slugs, 0) - total(means, a, slugs, 0),
    )  # fmt: skip


# ---- a reviewer that is safe for threads, cached and capped --------------------------------------


class CachedClient:
    """Wraps a reviewer: answers from a prompt-hash cache on disk, counts each request in flight at
    its worst case against a spend cap, logs tokens and keeps the error of each failed request (the
    verifier turns every error into an unchecked flag; an OpenRouter 402 at a low balance with many
    workers looks exactly like that, so the messages are kept and printed)."""

    def __init__(self, inner, path: Path, max_usd: float, spec) -> None:
        self.inner, self.path, self.max_usd, self.spec, self.name = (
            inner, path, max_usd, spec, inner.name,
        )  # fmt: skip
        self.cache: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}
        self.spent = self.reserved = 0.0
        self.refused = self.hits = self.calls = 0
        self.log: list[dict] = []
        self.errors: list[str] = []
        self.lock = threading.Lock()

    def review(self, prompt, schema, *, max_tokens):
        key = hashlib.sha256((prompt.system + "\0" + prompt.user).encode()).hexdigest()
        with self.lock:
            if key in self.cache:
                self.hits += 1
                return ReviewerCall(text=self.cache[key], model=self.name)
        shown = cmp.with_schema(prompt, schema)
        worst = cmp.call_usd(shown.chars / cmp.PESSIMISTIC_CHARS_PER_TOKEN, max_tokens, self.spec)
        with self.lock:
            if self.spent + self.reserved + worst > self.max_usd:
                self.refused += 1
                raise ModelUnavailable("the spend cap would be passed")
            self.reserved += worst
        try:
            reply = self.inner.review(shown, schema, max_tokens=max_tokens)
        except ModelUnavailable as err:
            with self.lock:
                self.errors.append(str(err)[:160])
            raise
        finally:
            with self.lock:
                self.reserved -= worst
        with self.lock:
            self.spent += reply.cost_usd
            self.calls += 1
            self.log.append({"usd": reply.cost_usd, "finish": reply.finish_reason})
            if reply.text is not None and reply.finish_reason != "length":
                self.cache[key] = reply.text
            if self.calls % 40 == 0:
                self.flush()
        return reply

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache))


# ---- reading the runs and the judgements ---------------------------------------------------------


def parse_pair(text: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """`plainrun:stage+pipelinerun:stage`, for example `ds:screen+ds3:pipeline`."""
    try:
        left, right = text.split("+")
        (pr, ps), (qr, qs) = (part.split(":") for part in (left, right))
    except ValueError:
        raise SystemExit(
            f"a pair looks like plainrun:stage+pipelinerun:stage, not {text!r}"
        ) from None
    return (pr, ps), (qr, qs)


def default_out(side: str) -> Path:
    base = Path.home() / "Library/Caches/contract-analyzer/verify-runs"
    return base / ("reasoning-eval" if side == "dev" else "reasoning-eval-heldout")


def flags_of(cache: Path, side: str, stage: str, runs: str) -> dict[str, list[Flag]]:
    return {
        slug: [Flag(**d) for d in rec.get("flags", [])]
        for slug, rec in tr.read_stage(cache, side, stage, runs).items()
    }


def candidate_of(slug: str, paths: Mapping[str, str], flag: Flag) -> Candidate:
    return Candidate(
        slug, paths.get(flag.file_id, flag.file_id), flag.contract or "", flag.function or "",
        flag.start_line, flag.end_line, flag.note, flag.severity, frozenset(),
    )  # fmt: skip


class Judged:
    """What the judge panel decided about flags, read from the cache: the findings a flag was
    judged the same bug as. A pair a judge never answered counts as no match, as in the reports."""

    def __init__(self, ctx, judges, rubric) -> None:
        self.ctx, self.judges, self.rubric = ctx, judges, rubric
        self.cache = JudgeCache(ctx.cache / "judge")
        self.paths = {
            slug: {f"F{i}": p for i, (p, _) in enumerate(src.files, 1)}
            for slug, src in ctx.sources.items()
        }

    def labels(self, slug: str, flag: Flag) -> set[str]:
        claim = tj.Claim(
            self.paths[slug].get(flag.file_id, flag.file_id), flag.contract or "",
            flag.function or "", flag.start_line, flag.end_line, flag.note,
        )  # fmt: skip
        same: set[str] = set()
        for lab in self.ctx.labels_of[slug]:
            if lab.file_scoped and claim.file not in lab.files:
                continue
            got = [
                self.cache.get(tj.pair_key(self.ctx.refs[lab.id], claim, j, self.rubric))
                for j in self.judges
            ]
            if (
                all(g and g["parsed"] for g in got)
                and tj.combine([g["verdict"] for g in got]) == "same"
            ):
                same.add(lab.id)
        return same


def load_rows(
    ctx, pairs: Sequence[str], judged: Judged, keyset: set[str] | None = None
) -> list[dict[str, list[FlagRow]]]:
    """One dict per run pair: report -> the rows of its plain and pipeline flags."""
    runs = []
    for text in pairs:
        (pr, ps), (qr, qs) = parse_pair(text)
        plain, pipe = flags_of(ctx.cache, ctx.side, ps, pr), flags_of(ctx.cache, ctx.side, qs, qr)
        run: dict[str, list[FlagRow]] = {}
        for slug in ctx.sources:
            paths = judged.paths[slug]
            rows = []
            for is_plain, flags in ((True, plain.get(slug, [])), (False, pipe.get(slug, []))):
                for flag in flags:
                    labels = judged.labels(slug, flag)
                    if keyset is not None:
                        labels &= keyset
                    rows.append(FlagRow(
                        ly.place_key(flag, paths), candidate_of(slug, paths, flag).id,
                        frozenset(labels), is_plain,
                    ))  # fmt: skip
            run[slug] = rows
        runs.append(run)
    return runs


def all_claims(ctx, pairs: Sequence[str]) -> dict[str, tuple[str, Candidate]]:
    """candidate id -> (report, candidate) for every distinct flag of the pairs."""
    paths = {
        s: {f"F{i}": p for i, (p, _) in enumerate(src.files, 1)} for s, src in ctx.sources.items()
    }
    out: dict[str, tuple[str, Candidate]] = {}
    for text in pairs:
        (pr, ps), (qr, qs) = parse_pair(text)
        for runs, stage in ((pr, ps), (qr, qs)):
            for slug, flags in flags_of(ctx.cache, ctx.side, stage, runs).items():
                for flag in flags:
                    c = candidate_of(slug, paths[slug], flag)
                    out.setdefault(c.id, (slug, c))
    return out


# ---- commands ------------------------------------------------------------------------------


def judge_args(ns: argparse.Namespace) -> argparse.Namespace:
    """The options `evals.source_review.tob_eval_cli.judge_setup` reads."""
    return argparse.Namespace(
        mode=None, judge=None, model="deepseek", allow_same_company=ns.allow_same_company
    )


def context_of(ns: argparse.Namespace, key: str):
    args = argparse.Namespace(
        cache=ns.cache, side=ns.side, runs="ds", key=key,
        exploratory_held_out=getattr(ns, "exploratory_held_out", False),
    )  # fmt: skip
    return cli.context_for(args)


def cmd_claims(ns: argparse.Namespace) -> int:
    ctx = context_of(ns, "orig")
    pairs = ns.pair or DEFAULT_PAIRS[ns.side]
    items = all_claims(ctx, pairs)
    ordered = sorted(items.items(), key=lambda kv: hashlib.sha256(kv[0].encode()).hexdigest())
    if ns.limit:
        ordered = ordered[: ns.limit]
    spec = cmp.DEEPSEEK
    print(
        f"{ns.side}: {len(ordered)} distinct flags in {len(pairs)} run pairs; about "
        f"${len(ordered) * EST_USD_PER_FLAG:.2f} on {spec.name} (thinking off), cap ${ns.max_spend}"
    )
    problems = cli.money_problems(ns, ns.side)
    if not ns.confirm_spend or problems:
        if ns.confirm_spend or ns.max_spend is not None:
            print("\n".join(f"  - {p}" for p in problems))
        print("not run: no model was called")
        return 0 if not ns.confirm_spend else 2
    out = ns.out or default_out(ns.side)
    suffix = f"-limit{ns.limit}" if ns.limit else ""
    client = CachedClient(
        OpenRouterReviewer(
            spec, structured="none", reasoning="off", retries=cmp.RETRIES, timeout=300.0
        ),
        out / f"cache-{ns.name}.json", ns.max_spend, spec,
    )  # fmt: skip
    records: dict[str, dict] = {}
    started = time.time()
    try:
        for slug in sorted(ctx.sources):
            mine = [c for _, (sl, c) in ordered if sl == slug]
            if not mine:
                continue
            got = reason.reason_all(client, mine, dict(ctx.sources[slug].files), ns.workers)
            for c in mine:
                r = got.get(c.id)
                records[c.id] = {
                    "slug": slug, "verdict": r.verdict if r else None,
                    "confidence": r.confidence if r else None,
                    "claim_true": r.claim_true if r else "", "severity": r.severity if r else "",
                    "difficulty": r.difficulty if r else "", "category": r.category if r else "",
                    "downgraded": r.downgraded if r else None,
                    "overridden": r.overridden if r else None,
                    "claim": c.note, "key": [c.file, c.contract, c.function],
                }  # fmt: skip
            print(f"  {slug}: {len(mine)} flags, {time.time() - started:.0f}s, ${client.spent:.3f}")
    finally:
        client.flush()
    out.mkdir(parents=True, exist_ok=True)
    (out / f"claims-{ns.name}{suffix}.json").write_text(json.dumps(records))
    if client.errors:
        print("request errors:", dict(Counter(client.errors).most_common(4)))
    cut = sum(x["finish"] == "length" for x in client.log)
    print(
        f"{len(records)} flags; spent ${client.spent:.3f} ({client.calls} calls, {client.hits} "
        f"cached, {client.refused} refused, {cut} cut off)"
    )
    verdicts = Counter(r["verdict"] for r in records.values())
    print(
        "verdicts:",
        dict(verdicts),
        "| overridden:",
        sum(bool(r["overridden"]) for r in records.values()),
        "| downgraded:",
        sum(bool(r["downgraded"]) for r in records.values()),
    )
    unchecked = verdicts.get(None, 0)
    if unchecked:
        print(f"{unchecked} flags have no verdict: run the same command again (answers are cached)")
    return 0


def report(
    title: str, means, slugs: Sequence[str], side: str, name: str, runs: int, out=print
) -> None:
    a, u = BASE_SYSTEMS
    out(
        f"\n{title}: {name}, mean of {runs} run pairs; "
        "shown / credited / found, summed over reports"
    )
    for system in means:
        shown, credited, found = (total(means, system, slugs, i) for i in range(3))
        share = credited / shown if shown else 0.0
        out(f"  {system:<26}{shown:>7.0f}{credited:>8.1f}{found:>7.1f}  share {share:>6.1%}")
    if side != "dev":
        out("  per report (places shown / credited / findings found):")
        gate = "valid, severity low+"
        for s in slugs:
            cells = "  ".join(
                f"{label} " + "/".join(f"{means[system][s][i]:>4.0f}" for i in range(3))
                for label, system in (("plain", a), ("every flag", u), ("v3 low+", gate))
            )
            out(f"    {s[:46]:<46} {cells}")
    for rule in RULES:
        d = decide(means, rule, slugs)
        out(
            f"  {rule:<26} share {d.share:+.1%} "
            f"[{d.share_interval[0]:+.1%}, {d.share_interval[1]:+.1%}]"
            f" | found {d.found:+.1f} [{d.found_interval[0]:+.1f}, {d.found_interval[1]:+.1f}]"
            f" | shown {d.shown:+.0f} -> {'RULE MET' if d.met else 'rule not met'}"
        )
        if side == "dev":
            for tag, reps in (("tune (Cap, Frax)", TUNE), ("check (Arcade, Radius)", CHECK)):
                reps = [s for s in reps if s in slugs]
                x = decide(means, rule, reps) if reps else None
                if x:
                    base_found = total(means, a, reps, 2)
                    base_shown = total(means, a, reps, 0)
                    out(
                        f"      {tag:<24} share {x.share:+.1%} | found {x.found:+.1f} "
                        f"(plain {base_found:.1f}) | shown {x.shown:+.0f} (plain {base_shown:.0f})"
                    )


def cmd_score(ns: argparse.Namespace) -> int:
    pairs = ns.pair or DEFAULT_PAIRS[ns.side]
    out = ns.out or default_out(ns.side)
    path = out / f"claims-{ns.name}.json"
    if not path.exists():
        print(f"no verdicts at {path}: run `claims` first")
        return 2
    verdicts = json.loads(path.read_text())
    wide_ok = ns.side == "dev" or ns.exploratory_held_out
    ctx = context_of(ns, "wide" if wide_ok else "orig")
    judges, _, rubric, problems = cli.judge_setup(judge_args(ns), print)
    if problems or rubric is None:
        print("\n".join(problems))
        return 2
    plain_ctx = cli.load_context(ns.cache, ns.side)
    orig = {lab.id for labs in plain_ctx.labels_of.values() for lab in labs}
    wide = {lab.id for labs in ctx.labels_of.values() for lab in labs}
    judged = Judged(ctx, judges, rubric)
    slugs = sorted(ctx.sources)
    print(
        "verdicts:",
        dict(Counter(r["verdict"] for r in verdicts.values())),
        "| severity of the valid:",
        dict(Counter(r["severity"] for r in verdicts.values() if r["verdict"] == "valid")),
    )
    if ns.side == "dev":
        keysets = ((f"WIDE KEY ({len(wide)})", wide), (f"ORIGINAL KEY ({len(orig)})", orig))
    elif wide_ok:
        keysets = (
            (f"WIDE KEY ({len(wide)}), held-out, EXPLORATORY and post-hoc", wide),
            (f"ORIGINAL KEY ({len(orig)}), held-out", orig),
        )
    else:
        keysets = ((f"ORIGINAL KEY ({len(orig)}), held-out", orig),)
    for title, keyset in keysets:
        rows = load_rows(ctx, pairs, judged, keyset)
        means = evaluate(rows, slugs, verdicts)
        report(title, means, slugs, ns.side, ns.name, len(pairs))
    return 0


def cmd_pipeline_all(ns: argparse.Namespace) -> int:
    """The two-pass pipeline on EVERY report of a side, through the harness's own stage runner. The
    harness's `score` only runs the reports with a hard finding and refuses to start while one
    screening pair has no verdict; a layered run wants a pipeline run on every report."""
    argv = [
        "score", "--side", ns.side, "--stages", "pipeline", "--model", "deepseek",
        "--runs", ns.runs, "--pipeline", "two_pass", "--allow-same-company", ns.allow_same_company,
        "--max-spend", str(ns.max_spend), "--cache", str(ns.cache),
    ]  # fmt: skip
    if ns.confirm_spend:
        argv.append("--confirm-spend")
    if ns.declare:
        argv += ["--declare", ns.declare]
    args = tob_cli.build_parser().parse_args(argv)
    ctx = cli.context_for(args)
    return cli.run_stages(args, ctx, ["pipeline"], ctx.reports, print)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.source_review.verifier_eval", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--side", choices=("dev", "held_out"), required=True)
        p.add_argument("--cache", type=Path, required=True, help="the tob cache `fetch` filled")
        p.add_argument(
            "--pair", action="append", help="plainrun:stage+pipelinerun:stage (repeatable)"
        )
        p.add_argument("--name", default=NAME, help="label of the verifier run")
        p.add_argument("--out", type=Path, help="where verdicts and the answer cache live")
        p.add_argument(
            "--allow-same-company", default="", metavar="WHY",
            help="DeepSeek is the model under test and also a judge: say why that is allowed "
            "(dev only; the result is not quotable)",
        )  # fmt: skip

    def spend(p: argparse.ArgumentParser) -> None:
        p.add_argument("--confirm-spend", action="store_true")
        p.add_argument("--max-spend", type=float)
        p.add_argument(
            "--declare", help="held-out only: why the code is frozen and this run is final"
        )

    p = sub.add_parser("claims", help="verify every distinct flag (a model call; cached)")
    common(p)
    spend(p)
    p.add_argument("--workers", type=int, default=20)
    p.add_argument("--limit", type=int, default=0, help="only the first N flags (a smoke test)")
    p.set_defaults(run=cmd_claims)

    p = sub.add_parser("score", help="free: the tables, from the cache")
    common(p)
    p.add_argument(
        "--exploratory-held-out", action="store_true",
        help="held-out only: also score the wide key (post-hoc and exploratory; its pairs must "
        "have been judged with `judge --exploratory-held-out`)",
    )  # fmt: skip
    p.set_defaults(run=cmd_score)

    p = sub.add_parser(
        "pipeline-all", help="run the two-pass pipeline on every report (a model call)"
    )
    p.add_argument("--side", choices=("dev", "held_out"), required=True)
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument(
        "--runs", required=True, help="the cache folder that holds the screening records"
    )
    p.add_argument("--allow-same-company", default="", metavar="WHY")
    spend(p)
    p.set_defaults(run=cmd_pipeline_all)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    return int(ns.run(ns))


if __name__ == "__main__":
    sys.exit(main())
