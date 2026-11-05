"""The Trail of Bits report: the headline, the descriptive tables, and the pairs Aryaan checks.

Headline (fixed in evals/datasets/tob_items/README.md): held-out hard-finding recall, pipeline
against the fresh plain run, as points and as (A - P) / P, each with a 95% bootstrap interval that
resamples REPORTS (a report's findings stay together), plus paired counts. "x% better" may be said
only if the interval of the relative improvement excludes 0.

Nothing here holds a reviewer's sentence or an auditor's text: a report names findings by id, and
the pairs written for a person to read go to the cache folder, outside the repo.
"""

import json
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from evals.repeats import cluster_bootstrap
from evals.source_review import tob_items as ti
from evals.source_review import tob_judge as tj
from evals.source_review import tob_run as tr
from evals.source_review.compare import RESULTS_DIR, fmt, sign_test_p
from evals.source_review.tob_run import LabelVerdict

NAN = float("nan")
SHOWN = {
    True: "**Improvement shown** (the interval excludes 0).",
    False: "**No improvement shown** (the interval includes 0 or is undefined).",
}
BANNER = (
    "DRAFT LABELS and a judge that is a model's opinion: the labels were made by rule from ToB's "
    "own Target line, and a pair counts as found only if the judges agree. One run per stage at "
    "temperature 0. Read the intervals: they resample reports, and there are few."
)


def year_of(slug: str) -> int:
    """The year a report is dated, from its slug (`2025-01-…`); 0 if the slug has none."""
    return int(slug[:4]) if slug[:4].isdigit() else 0


def today() -> str:
    return date.today().isoformat()


# ---- the numbers -------------------------------------------------------------------------------


def _diff(items: list[tuple[int, int]]) -> float:
    return (sum(a for _, a in items) - sum(p for p, _ in items)) / len(items)


def _relative(items: list[tuple[int, int]]) -> float:
    p = sum(p for p, _ in items)
    return (sum(a for _, a in items) - p) / p if p else NAN


def _num(x: float) -> float | None:
    return None if x != x else round(x, 4)


def _iv(iv: Any) -> list[float | None]:
    return [_num(iv.mean), _num(iv.low), _num(iv.high)]


def _clusters(rows: list[dict], f: str, a: str) -> list[list[tuple[int, int]]]:
    by: dict[str, list[tuple[int, int]]] = {}
    for r in rows:
        by.setdefault(r["report"], []).append((int(r[f]), int(r[a])))
    return list(by.values())


def headline(rows: list[dict], f: str = "F", a: str = "A", seed: int = 0) -> dict:
    """Pipeline against fresh plain on the hard findings, resampling reports."""
    if not rows:
        return {"findings": 0, "reports": 0}
    clusters = _clusters(rows, f, a)
    k_f, k_a = sum(r[f] for r in rows), sum(r[a] for r in rows)
    out: dict[str, Any] = {
        "findings": len(rows),
        "reports": len(clusters),
        "plain_found": k_f,
        "pipeline_found": k_a,
        "both": sum(r[f] and r[a] for r in rows),
        "pipeline_only": sum(r[a] and not r[f] for r in rows),
        "plain_only": sum(r[f] and not r[a] for r in rows),
        "neither": sum(not r[f] and not r[a] for r in rows),
    }
    out["sign_test_p"] = round(sign_test_p(out["pipeline_only"], out["plain_only"]), 4)
    diff = cluster_bootstrap(clusters, _diff, seed=seed)
    out["difference"] = _iv(diff)
    if k_f:
        rel = cluster_bootstrap(clusters, _relative, seed=seed)
        out["relative"] = _iv(rel)
        out["improvement_shown"] = rel.low > 0
    else:
        out["relative"] = None
        out["improvement_shown"] = False
        out["note"] = (
            "plain found none of the hard findings, so a relative improvement is undefined"
        )
    return out


def _recall(rows: list[dict], key: str) -> dict:
    n = len(rows)
    k = sum(r[key] for r in rows)
    return {"found": k, "of": n, "share": _num(k / n) if n else None}


@dataclass
class Inputs:
    """Everything a report needs, already judged."""

    reports: list[ti.Report]  # the side's kept reports
    labels: list[ti.Label]
    verdicts: dict[str, dict[tuple[str, str], LabelVerdict]]  # stage -> (report, label) -> verdict
    records: dict[str, dict[str, dict]]  # stage -> report -> checkpoint record
    flags: dict[str, dict[str, tuple[int, int]]]  # stage -> report -> (flags, unmatched)
    meta: dict[str, Any]
    replaced: dict[str, float] = field(default_factory=dict)  # stage -> dollars of replaced lines
    agreement: dict[str, Any] = field(default_factory=dict)  # judge_agreement(...)


def build_report(inp: Inputs) -> dict:
    screen, fresh, pipe = (inp.verdicts.get(s, {}) for s in tr.STAGES)
    by_id = {lab.id: lab for lab in inp.labels}
    year = {r.slug: year_of(r.slug) for r in inp.reports}
    rows = []
    for (slug, lid), s in sorted(screen.items()):
        lab = by_id[lid]
        f, a = fresh.get((slug, lid)), pipe.get((slug, lid))
        rows.append(
            {
                "report": slug,
                "label": lid,
                "severity": lab.severity,
                "difficulty": lab.difficulty,
                "year": year[slug],
                "S": s.kind,
                "S_found": s.found,
                "F": bool(f and f.found),
                "A": bool(a and a.found),
                "F_loose": bool(f and f.found_lenient),
                "A_loose": bool(a and a.found_lenient),
                "F_all": bool(f and f.found_unanimous),
                "A_all": bool(a and a.found_unanimous),
                "F_kind": f.kind if f else "not run",
                "A_kind": a.kind if a else "not run",
                "scored": f is not None and a is not None,
            }
        )
    hard = [r for r in rows if r["S"] in ("missed", "mislabelled")]
    scored_hard = [r for r in hard if r["scored"]]
    scored_all = [r for r in rows if r["scored"]]
    report: dict[str, Any] = {
        "meta": inp.meta,
        "banner": BANNER,
        "labels": len(rows),
        "hard_findings": len(hard),
        "hard_reports": len({r["report"] for r in hard}),
        "unjudged_in_screening": sum(r["S"] == "unjudged" for r in rows),
        "headline": headline(scored_hard),
        "headline_lenient": headline(
            [{**r, "F": r["F_loose"], "A": r["A_loose"]} for r in scored_hard]
        ),
        "headline_unanimous": headline(
            [{**r, "F": r["F_all"], "A": r["A_all"]} for r in scored_hard]
        ),
        "judge_agreement": inp.agreement,
        "screening_recall": _recall(rows, "S_found"),
        "all_findings_in_scored_reports": {
            "screen": _recall(scored_all, "S_found"),
            "fresh": _recall(scored_all, "F"),
            "pipeline": _recall(scored_all, "A"),
        },
        "repeat_noise": {
            "screen_found_fresh_found": sum(r["S_found"] and r["F"] for r in scored_all),
            "screen_found_fresh_missed": sum(r["S_found"] and not r["F"] for r in scored_all),
            "screen_missed_fresh_found": sum(not r["S_found"] and r["F"] for r in scored_all),
            "screen_missed_fresh_missed": sum(not r["S_found"] and not r["F"] for r in scored_all),
        },
        "mislabelled": {
            "screen": sum(r["S"] == "mislabelled" for r in rows),
            "fresh_among_hard": sum(r["F_kind"] == "mislabelled" for r in scored_hard),
            "pipeline_among_hard": sum(r["A_kind"] == "mislabelled" for r in scored_hard),
        },
        "by_difficulty": _split(scored_hard, "difficulty"),
        "by_year": _split(scored_hard, "year"),
        "by_severity": _split(scored_hard, "severity"),
        "false_alarms": {
            stage: {
                "flags": sum(v[0] for v in inp.flags.get(stage, {}).values()),
                "unmatched": sum(v[1] for v in inp.flags.get(stage, {}).values()),
            }
            for stage in tr.STAGES
        },
        "cost": _cost(inp.records, inp.replaced),
        "failures": _failures(inp.records),
        "per_finding": rows,
        "not_scored": [
            {"report": r.slug, "why": _why_not(r.slug, inp)}
            for r in inp.reports
            if not _complete(inp, r.slug)
        ],
    }
    return report


def _split(rows: list[dict], key: str) -> dict:
    out: dict[str, dict] = {}
    for value in sorted({str(r[key]) for r in rows}):
        part = [r for r in rows if str(r[key]) == value]
        out[value] = {
            "hard": len(part),
            "fresh_found": sum(r["F"] for r in part),
            "pipeline_found": sum(r["A"] for r in part),
        }
    return out


def _complete(inp: Inputs, slug: str) -> bool:
    return all(tr.finished(inp.records.get(s, {}).get(slug, {})) for s in ("screen",))


def _why_not(slug: str, inp: Inputs) -> str:
    rec = inp.records.get("screen", {}).get(slug)
    if rec is None:
        return "not screened"
    return rec.get("skipped") or "a screening request was refused or failed"


def _cost(records: dict[str, dict[str, dict]], replaced: dict[str, float]) -> dict:
    out: dict[str, Any] = {}
    total = 0.0
    for stage in tr.STAGES:
        rows = [r for r in records.get(stage, {}).values() if "cost_usd" in r]
        usd = [r["cost_usd"] for r in rows]
        sec = [r["seconds"] for r in rows]
        total += sum(usd) + replaced.get(stage, 0.0)
        out[stage] = {
            "replaced_usd": replaced.get(stage, 0.0),
            "reports": len(rows),
            "total_usd": round(sum(usd), 4),
            "mean_usd": _num(statistics.fmean(usd)) if usd else None,
            "mean_seconds": _num(statistics.fmean(sec)) if sec else None,
            "calls": sum(r["calls"] for r in rows),
        }
    out["agents_total_usd"] = round(total, 4)
    return out


def _failures(records: dict[str, dict[str, dict]]) -> dict:
    out = {}
    for stage in tr.STAGES:
        rows = [r for r in records.get(stage, {}).values() if "failures" in r]
        reasons: dict[str, int] = {}
        for r in rows:
            for f in r["failures"]:
                reasons[f] = reasons.get(f, 0) + 1
        out[stage] = {
            "reports_with_unreadable_reply": sum(bool(r["failures"]) for r in rows),
            "unreadable_replies": reasons,
            "reports_incomplete": sum(not r["complete"] for r in rows),
            "requests_sent_again": sum(r.get("retries", 0) for r in rows),
        }
    return out


# ---- the written report ------------------------------------------------------------------------


def render_agreement(ja: dict) -> list[str]:
    out = [
        f"{ja['pairs']} judged pairs: all judges agree on {ja['unanimous']}; exactly one judge is "
        f"in the minority on {ja['one_judge_in_the_minority']}; other splits {ja['other_splits']}.",
        "",
        "| judge | minority | share of pairs | share of minority cases | more generous | stricter"
        " | said same / same_place / unrelated |",
        "|---|---|---|---|---|---|---|",
    ]
    for j, v in ja["minority"].items():
        said = ja["said"][j]
        out.append(
            f"| {j} | {v['count']} | {v['share_of_all_pairs']} | {v['share_of_minority_cases']} | "
            f"{v['more_generous']} | {v['stricter']} | {said['same']} / {said['same_place']} / "
            f"{said['unrelated']} |"
        )
    pairs = "; ".join(f"{k}: {v}" for k, v in ja["pairwise_exact_agreement"].items())
    return out + ["", f"Exact agreement between each two judges: {pairs}."]


def render(report: dict) -> list[str]:
    m, h = report["meta"], report["headline"]
    out = [
        f"# Trail of Bits items: plain {m.get('model', 'Opus')} against our source review "
        f"({m.get('side', '?')}, {m.get('date', '')})",
        "",
        f"**{report['banner']}**",
        "",
        *(
            [
                "**PARTIAL JUDGING: "
                + ", ".join(f"{s} {n}" for s, n in m["partial_judging"].items())
                + " pairs have no verdict from every judge, so the found counts below are too "
                "low and the comparison means nothing until judging is complete.**",
                "",
            ]
            if m.get("partial_judging")
            else []
        ),
        *([f"**POST-HOC: {m['note']}**", ""] if m.get("note") else []),
        *(
            [f"**JUDGE WAIVER (dev only, not quotable): {m['judge_waiver']}**", ""]
            if m.get("judge_waiver")
            else []
        ),
        *(
            ["Models by stage: " + "; ".join(f"{s} {n}" for s, n in m["stage_models"].items()), ""]
            if m.get("stage_models")
            else []
        ),
        f"{report['labels']} labelled findings; plain's screening run found "
        f"{report['screening_recall']['found']} and left {report['hard_findings']} hard (missed or "
        f"mislabelled) in {report['hard_reports']} reports. "
        f"Judges: {', '.join(m.get('judges', []))} "
        f"({m.get('judge_label', '')}). Rubric {m.get('rubric', '')[:12]}"
        f"{'' if m.get('rubric_approved') else ' (NOT approved by Aryaan)'}.",
        "",
        "## Headline: the hard findings, pipeline against a fresh plain run",
        "",
    ]
    if not h.get("findings"):
        out.append("No hard finding was scored.")
    else:
        out += [
            f"{h['findings']} hard findings in {h['reports']} reports (intervals resample "
            "reports).",
            "",
            "| | found | recall |",
            "|---|---|---|",
            f"| fresh plain (P2) | {h['plain_found']}/{h['findings']} | "
            f"{h['plain_found'] / h['findings']:.2f} |",
            f"| pipeline (A) | {h['pipeline_found']}/{h['findings']} | "
            f"{h['pipeline_found'] / h['findings']:.2f} |",
            "",
            f"Difference (A - P2), points: {fmt(h['difference'])}. Relative improvement "
            f"(A - P2) / P2: {fmt(h['relative']) if h['relative'] else 'undefined'}. "
            + SHOWN[h["improvement_shown"]],
            "",
            f"Paired: both {h['both']}, pipeline only {h['pipeline_only']}, plain only "
            f"{h['plain_only']}, neither {h['neither']} (exact sign test p = {h['sign_test_p']}; "
            "findings of one report are not independent).",
        ]
        if h.get("note"):
            out.append(f"Note: {h['note']}.")
        for key, name in (
            ("headline_unanimous", "Unanimous reading (every judge says `same`)"),
            ("headline_lenient", "Lenient reading (any judge says `same`)"),
        ):
            hv = report[key]
            if hv.get("findings"):
                out.append(
                    f"{name}: plain {hv['plain_found']}, pipeline {hv['pipeline_found']} of "
                    f"{hv['findings']}; difference {fmt(hv['difference'])}."
                )
    a = report["all_findings_in_scored_reports"]
    n = report["repeat_noise"]
    out += [
        "",
        "## Descriptive",
        "",
        f"- All labelled findings in the scored reports, found: screening plain "
        f"{a['screen']['found']}/{a['screen']['of']}, fresh plain {a['fresh']['found']}/"
        f"{a['fresh']['of']}, pipeline {a['pipeline']['found']}/{a['pipeline']['of']}.",
        f"- Repeat noise (screening against fresh plain, same findings): found then found "
        f"{n['screen_found_fresh_found']}, found then missed {n['screen_found_fresh_missed']}, "
        f"missed then found {n['screen_missed_fresh_found']}, missed then missed "
        f"{n['screen_missed_fresh_missed']}.",
        f"- Mislabelled (right place, wrong bug): screening {report['mislabelled']['screen']}; "
        f"among the hard findings, fresh plain {report['mislabelled']['fresh_among_hard']}, "
        f"pipeline {report['mislabelled']['pipeline_among_hard']}.",
    ]
    for name in ("by_difficulty", "by_year", "by_severity"):
        parts = ", ".join(
            f"{k}: {v['hard']} hard, plain {v['fresh_found']}, pipeline {v['pipeline_found']}"
            for k, v in report[name].items()
        )
        out.append(f"- Hard findings {name.replace('by_', 'by ')}: {parts or 'none'}.")
    fa = report["false_alarms"]
    out.append(
        "- Flags matching no labelled finding (a lower bound on precision: real bugs the auditors "
        "did not list count here): "
        + "; ".join(f"{s} {v['unmatched']} of {v['flags']}" for s, v in fa.items())
        + "."
    )
    c = report["cost"]
    out.append(
        "- Cost and time per report: "
        + "; ".join(
            f"{s} ${c[s]['total_usd']:.2f} ({c[s]['mean_seconds'] or 0:.0f} s, "
            f"{c[s]['calls']} calls)"
            for s in tr.STAGES
        )
        + f"; agents ${c['agents_total_usd']:.2f} (of which "
        f"${sum(c[s]['replaced_usd'] for s in tr.STAGES):.2f} for replies cut off at the output "
        f"limit and run again); judges ${m.get('judge_usd', 0.0):.2f}."
    )
    f = report["failures"]
    out.append(
        "- Unreadable replies: "
        + "; ".join(f"{s} {f[s]['unreadable_replies'] or 'none'}" for s in tr.STAGES)
        + f". Pairs the judge could not answer in screening: {report['unjudged_in_screening']}."
    )
    again = {s: f[s].get("requests_sent_again", 0) for s in tr.STAGES}
    if any(again.values()):
        out.append(
            "- Requests sent a second time because the first reply was not the JSON asked for: "
            + "; ".join(f"{s} {n}" for s, n in again.items() if n)
            + " (their cost is in the totals above)."
        )
    ja = report.get("judge_agreement") or {}
    if ja.get("pairs"):
        out += ["", "## Do the judges differ?", ""] + render_agreement(ja)
    if report["not_scored"]:
        out += ["", "## Not scored", ""]
        out += [f"- {n['report']}: {n['why']}" for n in report["not_scored"]]
    out += [
        "",
        "## Per finding",
        "",
        "| finding | screening | fresh plain | pipeline |",
        "|---|---|---|---|",
    ]
    out += [
        f"| {r['label']} | {r['S']} | {r['F_kind'] if r['S'] != 'found' else '-'} | "
        f"{r['A_kind'] if r['S'] != 'found' else '-'} |"
        for r in report["per_finding"]
    ]
    out += ["", "## What this can and cannot show", ""]
    out += [
        "- The labels are drafts made by rule from ToB's Target line; the judge is a model checked "
        "against Aryaan's reading of a sample, not against truth.",
        "- ToB reports and their code are public and the model may have seen them; older reports "
        "are "
        "likelier to have been seen (see the year table).",
        "- A flag that matches no listed finding may still be a real bug.",
        "- Findings in one report share code and are not independent; the intervals resample "
        "reports.",
    ]
    return out


def runs_suffix(runs: str | None) -> str:
    """The file-name ending of a report made from a re-run folder; none for the pre-registered
    runs, so a re-run never overwrites the original report."""
    return f"-{runs}" if runs and runs != tr.RUNS else ""


def stage_models(records: dict[str, dict[str, dict]]) -> dict[str, str]:
    """The model behind each stage's records, from the records themselves; records made before the
    model was kept were all Opus 5.5."""
    out = {}
    for stage in tr.STAGES:
        rows = list(records.get(stage, {}).values())
        names = {r.get("model", "anthropic/claude-opus-5.5") for r in rows}
        modes = {r["pipeline_mode"] for r in rows if r.get("pipeline_mode", "single") != "single"}
        modes |= {"reasoning off" for r in rows if r.get("reasoning") == "off"}
        if names:
            out[stage] = ", ".join(sorted(names)) + "".join(f" [{m}]" for m in sorted(modes))
    return out


def write_report(report: dict, side: str, directory: Path = RESULTS_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    meta = report["meta"]
    stem = f"tob-{side}-{meta.get('date', today())}{runs_suffix(meta.get('runs'))}"
    js, md = directory / f"{stem}.json", directory / f"{stem}.md"
    js.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    md.write_text("\n".join(render(report)) + "\n")
    return js, md


def model_label(name: str | None) -> str:
    """The model as a person says it: 'Opus 5.5' for the pre-registered runs, else its slug."""
    if not name or "opus-5.5" in name:
        return "Opus 5.5"
    return name


def results_row(report: dict) -> str:
    """The one row for evals/results.md (the convention: change, metrics, date)."""
    m, h = report["meta"], report["headline"]
    if not h.get("findings"):
        return f"| {m.get('date')} | ToB hard findings, {m.get('side')} | no hard finding scored |"
    rel = fmt(h["relative"]) if h.get("relative") else "undefined"
    verdict = "improvement shown" if h["improvement_shown"] else "no improvement shown"
    after = f" (post-hoc: {m['note']})" if m.get("note") else ""
    partial = " (**PARTIAL JUDGING, counts too low**)" if m.get("partial_judging") else ""
    return (
        f"| {m.get('date')} | plain {model_label(m.get('model'))} vs our source review on ToB "
        f"findings plain missed{after}{partial} | "
        f"{m.get('side')} | {h['findings']} hard findings in {h['reports']} reports | "
        f"{h['plain_found']}/{h['findings']} (fresh plain) | "
        f"{h['pipeline_found']}/{h['findings']} | "
        f"**{verdict}**: relative {rel}, difference {fmt(h['difference'])}; paired: both "
        f"{h['both']}, pipeline only {h['pipeline_only']}, plain only {h['plain_only']}, neither "
        f"{h['neither']}; agents ${report['cost']['agents_total_usd']:.2f} |"
    )


def judge_agreement(pairs: Sequence["PairView"], judges: Sequence[str]) -> dict:
    """Do the judges differ, and is one of them the odd one out? Over the judged pairs: how often
    all agree; with three or more judges, how often exactly one judge disagrees with the rest (it is
    the minority) and whether it was the more generous or the stricter; each pair of judges' exact
    agreement; and what each judge said overall."""
    n = len(judges)
    out: dict[str, Any] = {"judges": list(judges), "pairs": len(pairs)}
    if not pairs:
        return out
    odd = {j: {"count": 0, "more_generous": 0, "stricter": 0} for j in judges}
    unanimous = one_odd = other = 0
    for p in pairs:
        said = [p.judged[j] for j in judges]
        counts = {v: said.count(v) for v in set(said)}
        top = max(counts, key=lambda v: counts[v])
        if len(counts) == 1:
            unanimous += 1
        elif n >= 3 and counts[top] == n - 1:
            one_odd += 1
            who = judges[next(i for i, v in enumerate(said) if v != top)]
            odd[who]["count"] += 1
            odd[who]["more_generous" if tj.RANK[p.judged[who]] > tj.RANK[top] else "stricter"] += 1
        else:
            other += 1
    out.update(
        unanimous=unanimous,
        one_judge_in_the_minority=one_odd,
        other_splits=other,
        minority={
            j: {
                **v,
                "share_of_all_pairs": _num(v["count"] / len(pairs)),
                "share_of_minority_cases": _num(v["count"] / one_odd) if one_odd else None,
            }
            for j, v in odd.items()
        },
        pairwise_exact_agreement={
            f"{a} | {b}": _num(sum(p.judged[a] == p.judged[b] for p in pairs) / len(pairs))
            for i, a in enumerate(judges)
            for b in judges[i + 1 :]
        },
        said={j: {k: sum(p.judged[j] == k for p in pairs) for k in tj.VERDICTS} for j in judges},
    )
    return out


# ---- pairs for a person to read ----------------------------------------------------------------


@dataclass(frozen=True)
class PairView:
    ref: tj.Reference
    claim: tj.Claim
    judged: dict[str, str]  # judge -> verdict
    combined: str


def collect_pairs(
    rubric: tj.Rubric,
    judges: Sequence[str],
    refs: dict[str, tj.Reference],
    labels_of: dict[str, list[ti.Label]],
    records: dict[str, dict],
    sources: dict[str, Any],
    cache: Any,
) -> list[PairView]:
    out = []
    for slug, record in records.items():
        if not tr.finished(record) or slug not in sources:
            continue
        for claim in tr.claims_of(record, sources[slug]):
            for lab in labels_of.get(slug, []):
                got = {}
                for judge in judges:
                    row = cache.get(tj.pair_key(refs[lab.id], claim, judge, rubric))
                    if row is not None and row["parsed"]:
                        got[judge] = row["verdict"]
                if len(got) == len(judges):
                    out.append(PairView(refs[lab.id], claim, got, tj.combine(list(got.values()))))
    return out


def sample_pairs(
    pairs: Sequence[PairView], n: int = 24, per_kind: int = 6, seed: int = 0
) -> list[PairView]:
    """At least `per_kind` of each combined verdict that exists (`same`, `same_place`, `unrelated`,
    `disputed`), then the rest at random, `n` in all, shuffled. The same sample for the same
    input."""
    rng = random.Random(seed)
    chosen: list[PairView] = []
    pool = list(pairs)
    rng.shuffle(pool)
    for kind in ("same", "same_place", "unrelated", "disputed"):
        chosen += [p for p in pool if p.combined == kind][:per_kind]
    rest = [p for p in pool if p not in chosen]
    chosen += rest[: max(0, n - len(chosen))]
    rng.shuffle(chosen)
    return chosen


def render_handcheck(sample: Sequence[PairView], *, reveal: bool = False) -> list[str]:
    out = [
        "# Hand-check: is the claim the same bug as the reference?",
        "",
        "For each pair choose one: `same` (the claim names the reference's defect), `same_place` "
        "(it points at the same code but names a different defect), `unrelated`. The judges' "
        "answers "
        "and the arm are hidden. Third-party text below stays in this cache folder.",
        "",
    ]
    for i, p in enumerate(sample, 1):
        place = ".".join(x for x in (p.claim.contract, p.claim.function) if x) or "(no function)"
        out += [
            f"## Pair {i}",
            "",
            f"**Reference** {p.ref.id}: {p.ref.title}",
            f"Files: {', '.join(p.ref.files)}",
            "",
            f"> {p.ref.description[: tj.DESCRIPTION_CAP]}",
            "",
            f"**Claim** points at {p.claim.file}, {place}, lines {p.claim.start_line} to "
            f"{p.claim.end_line}:",
            "",
            f"> {p.claim.note}",
            "",
            "Your verdict: ",
            "",
        ]
        if reveal:
            out += [f"(judges: {p.judged}, combined: {p.combined})", ""]
    return out
