"""The layer table `make eval` prints: one line per layer, from the results document, or the words
"no run-set yet" where there is nothing to score. Plain text, no model."""

from typing import Any

NO_RUNSET = "no run-set yet (make eval-run)"


def iv(value: dict[str, Any] | None) -> str:
    if not value:
        return "n/a"
    return f"{value['value']:.2f} [{value['low']:.2f}-{value['high']:.2f}]"


def num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


Row = tuple[str, str, str, str]


def retrieval_rows(rows: list[tuple[str, str, Any]] | None) -> list[Row]:
    """`rows` are (variant, split, Summary) as `evals.run.run_retrieval` collects them."""
    if rows is None:
        return [("retrieval", "-", "recall@5 / MRR", "not run (--no-retrieval)")]
    return [
        (f"retrieval[{name}]", f"{s.n} {split}", "recall@5 / MRR", f"{s.recall} / {s.mrr}")
        for name, split, s in rows
    ]


def agent_rows(results: dict[str, Any] | None) -> list[Row]:
    if results is None:
        return [
            (layer, "-", metric, NO_RUNSET)
            for layer, metric in (
                ("tool use", "pass rate (single run / all-correct)"),
                ("detection", "precision / recall / F1 (abstains)"),
                ("answer quality", "citation validity / faithfulness"),
                ("time", "end-to-end p50 / p95 (s)"),
                ("cost", "$ per report (p50 / max)"),
            )
        ]
    manifest = results["manifest"]
    runset = manifest.get("runset") or {}
    shape = f"{manifest['carded_runs'] or manifest['traces']} runs"
    rows: list[Row] = []

    tool = results["tool_use"]
    if "pass" in tool:
        p = tool["pass"]
        rows.append(
            (
                "tool use",
                f"{p['contracts']} contracts, {p['runs']} runs",
                "pass rate (single run / all-correct)",
                f"{iv(p['single_run'])} / {iv(p['all_correct'])}",
            )
        )
    else:
        h = tool["hygiene_only"]
        rows.append(
            (
                "tool use",
                f"{h['runs']} runs",
                "hygiene rules pass (no cards matched)",
                iv(h["pass"]),
            )
        )

    det = results.get("detection")
    if det and "confusion" in det:
        rows.append(
            (
                "detection",
                f"{det['scored_contracts']} contracts, {det['scored_runs']} runs",
                "precision / recall / F1 (coverage)",
                f"{iv(det['precision'])} / {iv(det['recall'])} / {iv(det['f1'])} "
                f"({num(det['coverage'])})",
            )
        )
    else:
        rows.append(
            (
                "detection",
                shape,
                "precision / recall / F1",
                "nothing scored (no flagged or benign card)",
            )
        )

    cites = results["answer_quality"]["citations"]
    rows.append(
        (
            "answer quality",
            f"{cites['runs']} runs",
            "citation validity: first submit accepted / claims valid",
            f"{iv(cites['first_attempt_accepted'])} / {iv(cites['claim_validity'])}",
        )
    )
    faith = results["answer_quality"].get("faithfulness")
    if faith:
        for judge, found in faith["per_judge"].items():
            claims = found["claims"]
            rows.append(
                (
                    "",
                    f"{claims['reports']} reports, {claims['labelled']} claims",
                    f"faithfulness [{judge}]: claims supported / reports with none unsupported",
                    f"{iv(claims['item_level'])} / {iv(claims['report_level'])}",
                )
            )
    else:
        judges = (runset.get("judges") or {}).get("slugs")
        rows.append(
            (
                "",
                "-",
                "faithfulness",
                "not judged (no judge was named for this run-set)"
                if not judges
                else "judge named but no judgements found",
            )
        )

    for model, t in results["time"].items():
        e = t["end_to_end_s"]
        p95 = e["p95"] if e.get("p95") is not None else "n/a (n<40)"
        value = (
            f"{e['p50']} / {p95} (left out: {t['left_out'] or 'none'})"
            if e.get("n")
            else "no usable timing"
        )
        rows.append(
            (
                f"time[{model}]",
                f"{t['used']} of {t['runs']} runs",
                "end-to-end p50 / p95 (s)",
                value,
            )
        )
    for model, c in results["cost"].items():
        rows.append(
            (
                f"cost[{model}]",
                f"{c['runs']} runs",
                "$ per report (p50 / max)",
                f"{c['per_report_usd']['p50']} / {c['per_report_usd']['max']} "
                f"(total ${c['total_usd']:.4f})",
            )
        )
    return rows


def format_table(rows: list[Row]) -> str:
    header: Row = ("layer", "n", "metric", "value [95% CI]")
    everything = [header, *rows]
    widths = [max(len(r[i]) for r in everything) for i in range(3)]
    lines = []
    for layer, n, metric, value in everything:
        lines.append(f"{layer:<{widths[0]}}  {n:<{widths[1]}}  {metric:<{widths[2]}}  {value}")
    return "\n".join(lines)


def layer_table(
    retrieval: list[tuple[str, str, Any]] | None, results: dict[str, Any] | None
) -> str:
    return format_table([*retrieval_rows(retrieval), *agent_rows(results)])
