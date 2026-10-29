"""Scoring the audit items (rules and headline: evals/datasets/audit_items/README.md).

Unit of a run: a FILE. An issue is found by an arm if any of its labelled functions is flagged, at
any severity. Intervals resample files (an issue's file-mates stay together). The headline is
held-out issue recall, pipeline against plain, as points and as a relative improvement
(A - P) / P, each with its interval; "x% better" may be said only if that interval excludes 0.
"""

import json
import statistics
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from evals.repeats import cluster_bootstrap
from evals.source_review.audit_items import SIDES, FileItem
from evals.source_review.compare import (
    RESULTS_DIR,
    ArmRecord,
    _cost,
    _failures,
    _iv,
    _num,
    fmt,
    sign_test_p,
    smallest_split,
)
from evals.source_review.scoring import ratio_interval
from review.types import SEVERITY_RANK

NAN = float("nan")
HARD = 3  # found by this many auditors or fewer: "hard" (a human measure, fixed in advance)


def _rows(files: dict[str, FileItem], records: Sequence[dict], side: str) -> list[dict]:
    """One row per judged issue in a file where both arms answered every request."""
    out = []
    for r in records:
        item = files.get(r["item"])
        if item is None or "P" not in r or item.group != f"audit_{side}":
            continue
        if not (r["P"]["complete"] and r["A"]["complete"]):
            continue
        keys = {
            arm: {f.key for f in ArmRecord(**r[arm]).flag_list() if f.function is not None}
            for arm in ("P", "A")
        }
        for issue in item.issues:
            labelled = item.labels[issue.id]
            out.append(
                {
                    "file": item.id,
                    "issue": issue.id,
                    "severity": issue.severity,
                    "hard": issue.found_by is not None and issue.found_by <= HARD,
                    "P": bool(keys["P"] & labelled),
                    "A": bool(keys["A"] & labelled),
                }
            )
    return out


def _clusters(rows: list[dict]) -> list[list[tuple[int, int]]]:
    by_file: dict[str, list[tuple[int, int]]] = {}
    for x in rows:
        by_file.setdefault(x["file"], []).append((int(x["P"]), int(x["A"])))
    return list(by_file.values())


def _diff(items: list[tuple[int, int]]) -> float:
    return (sum(a for _, a in items) - sum(p for p, _ in items)) / len(items)


def _relative(items: list[tuple[int, int]]) -> float:
    p = sum(p for p, _ in items)
    return (sum(a for _, a in items) - p) / p if p else NAN


def _recall(rows: list[dict], arm: str) -> dict:
    if not rows:
        return {}
    clusters = [[(float(x[arm]), 1.0) for x in grp] for grp in _by_file(rows)]
    return {
        "found": sum(x[arm] for x in rows),
        "of": len(rows),
        "recall": _iv(ratio_interval(clusters)),
    }


def _by_file(rows: list[dict]) -> list[list[dict]]:
    out: dict[str, list[dict]] = {}
    for x in rows:
        out.setdefault(x["file"], []).append(x)
    return list(out.values())


def _side(files: dict[str, FileItem], records: Sequence[dict], side: str) -> dict:
    rows = _rows(files, records, side)
    if not rows:
        return {"issues": 0}
    clusters = _clusters(rows)
    a_only = sum(x["A"] and not x["P"] for x in rows)
    p_only = sum(x["P"] and not x["A"] for x in rows)
    per_file = [sum(a for _, a in c) - sum(p for p, _ in c) for c in clusters]
    out: dict[str, Any] = {
        "issues": len(rows),
        "files": len(clusters),
        "P": _recall(rows, "P"),
        "A": _recall(rows, "A"),
        "difference_points": _iv(cluster_bootstrap(clusters, _diff)),
        "relative_improvement": _iv(cluster_bootstrap(clusters, _relative)),
        "both": sum(x["A"] and x["P"] for x in rows),
        "a_found_p_missed": a_only,
        "p_found_a_missed": p_only,
        "neither": sum(not x["A"] and not x["P"] for x in rows),
        "sign_test_p_issues": round(sign_test_p(a_only, p_only), 4),
        "files_a_ahead": sum(d > 0 for d in per_file),
        "files_p_ahead": sum(d < 0 for d in per_file),
        "sign_test_p_files": round(
            sign_test_p(sum(d > 0 for d in per_file), sum(d < 0 for d in per_file)), 4
        ),
        "needs_for_p_under_0.05": smallest_split(a_only + p_only),
    }
    for name, pick in (
        ("hard", lambda x: x["hard"]),
        ("other", lambda x: not x["hard"]),
        ("high", lambda x: x["severity"] == "H"),
        ("medium", lambda x: x["severity"] == "M"),
    ):
        sub = [x for x in rows if pick(x)]
        out[f"stratum_{name}"] = {
            "n": len(sub),
            "P": sum(x["P"] for x in sub),
            "A": sum(x["A"] for x in sub),
        }
    return out


def _flags(files: dict[str, FileItem], records: Sequence[dict]) -> dict:
    out: dict[str, Any] = {}
    done = [r for r in records if "P" in r and r["P"]["complete"] and r["A"]["complete"]]
    for arm in ("P", "A"):
        counts, medium, matched, total = [], [], 0, 0
        for r in done:
            flags = ArmRecord(**r[arm]).flag_list()
            counts.append(len(flags))
            medium.append(sum(SEVERITY_RANK[f.severity] >= 1 for f in flags))
            keys = files[r["item"]].keys
            matched += sum(f.key in keys for f in flags if f.function is not None)
            total += len(flags)
        out[arm] = {
            "flags_per_file": _num(statistics.fmean(counts)) if counts else None,
            "medium_plus_per_file": _num(statistics.fmean(medium)) if medium else None,
            "flags_in_a_judged_function": matched,
            "flags": total,
        }
    return out


def build_audit_report(files: dict[str, FileItem], records: Sequence[dict], meta: dict) -> dict:
    ran = [r for r in records if "P" in r]
    return {
        "meta": meta,
        "sides": {s: _side(files, records, s) for s in SIDES},
        "flags": _flags(files, records),
        "cost": _cost(records),
        "failures": _failures(records),
        "not_scored": [
            {
                "item": r["item"],
                "why": r.get("skipped") or "a request was refused or failed",
            }
            for r in records
            if "P" not in r or not (r["P"]["complete"] and r["A"]["complete"])
        ],
        "files_run": len(ran),
    }


def render_audit(report: dict) -> list[str]:
    m = report["meta"]
    out = [
        f"# Audit items: plain {m['model']} against our source review ({m['date']})",
        "",
        "**DRAFT LABELS (unreviewed). Public reports: the model may have seen them. One run per "
        "file "
        "at temperature 0. Rules and headline were fixed before any run: "
        "evals/datasets/audit_items/README.md.**",
        f"Code at `{m.get('code_commit', '?')}`{' (DIRTY TREE)' if m.get('dirty') else ''}; "
        f"spent ${m['spent']:.2f} of a cap of ${m['max_spend']:.2f}; "
        f"{report['files_run']} files run.",
        "",
    ]
    for side in SIDES:
        s = report["sides"][side]
        if not s.get("issues"):
            continue
        out += [
            f"## {side}: {s['issues']} judged issues in {s['files']} files",
            "",
            "| | found | recall [95% CI over files] |",
            "|---|---|---|",
            f"| plain | {s['P']['found']}/{s['P']['of']} | {fmt(s['P']['recall'])} |",
            f"| pipeline | {s['A']['found']}/{s['A']['of']} | {fmt(s['A']['recall'])} |",
            "",
            f"- Difference (pipeline minus plain): {fmt(s['difference_points'])} points; "
            f"relative improvement (A - P) / P: {fmt(s['relative_improvement'])}.",
            f"- Paired: both {s['both']}, pipeline only {s['a_found_p_missed']}, plain only "
            f"{s['p_found_a_missed']}, neither {s['neither']}. Sign test p = "
            f"{s['sign_test_p_issues']} over issues, {s['sign_test_p_files']} over files "
            f"({s['files_a_ahead']} files pipeline ahead, {s['files_p_ahead']} plain ahead).",
        ]
        rel = s["relative_improvement"]
        if side != "held_out":
            claim = "dev is exploratory; no claim."
        elif rel[0] is None:
            claim = "relative improvement is undefined (plain found none); read the points above."
        elif rel[1] is not None and rel[1] > 0:
            claim = (
                f"the pipeline found {rel[0] * 100:.0f}% more judged issues than plain "
                f"(interval {rel[1] * 100:.0f}% to {rel[2] * 100:.0f}%)."
            )
        else:
            claim = "none. No improvement is shown."
        out.append("- **Claim allowed by the pre-registration:** " + claim)
        out.append(
            "- By stratum (plain / pipeline found of n): "
            + ", ".join(
                f"{k.removeprefix('stratum_')} {v['P']}/{v['A']} of {v['n']}"
                for k, v in s.items()
                if k.startswith("stratum_")
            )
        )
        out.append("")
    f, c, x = report["flags"], report["cost"], report["failures"]
    out += [
        "## Flags, cost, failures",
        "",
        "| arm | flags/file | medium+/file | in a judged fn | cost | median s | calls |",
        "|---|---|---|---|---|---|---|",
    ]
    for arm in ("P", "A"):
        out.append(
            f"| {arm} | {f[arm]['flags_per_file']} | {f[arm]['medium_plus_per_file']} | "
            f"{f[arm]['flags_in_a_judged_function']}/{f[arm]['flags']} | "
            f"${c[arm]['total_usd']:.3f} | "
            f"{c[arm]['median_seconds']} | {c[arm]['calls']} |"
        )
    out += [
        "",
        "A flag outside every judged function is not necessarily false: the judged list holds only "
        "the "
        "issues the contest accepted.",
        f"Unreadable replies: P {x['P']['unreadable_replies'] or 'none'}, "
        f"A {x['A']['unreadable_replies'] or 'none'}. Arm A coverage: {x['A_status']}.",
    ]
    if report["not_scored"]:
        out += ["", "## Not scored", ""] + [
            f"- {n['item']}: {n['why']}" for n in report["not_scored"]
        ]
    return out


def write_audit_report(report: dict, tag: str, directory: Path = RESULTS_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"audit-{tag}-{report['meta']['date']}"
    js, md = directory / f"{stem}.json", directory / f"{stem}.md"
    js.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    md.write_text("\n".join(render_audit(report)) + "\n")
    return js, md


def today() -> str:
    return date.today().isoformat()
