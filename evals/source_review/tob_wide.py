"""The wide key: a secondary, post-hoc view of the dev side (Aryaan, 2026-11-05).

The pre-registered key holds the High, Medium and Undetermined findings of each report. Severity
is the auditors' judgement of impact and difficulty, and the flags of both arms also hit the Low and
Informational findings, so this view adds those that name a Solidity file of the audited code
(findings about tests, documentation or library versions have no such file and are left out). It is
never the headline and never applies to the held-out side, whose rules are frozen.

The extra findings are read from the PDFs into labels (no auditor text is stored here), and each
is judged only against the flags in its own files (`Label.file_scoped`). On the pairs judged so far
that keeps 8% of the pairs and drops about 14% of the `same` verdicts, whose flag lies in another
file than the one the auditors listed, so the wide numbers are lower bounds for both arms.
"""

import dataclasses
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from evals.source_review import tob_items as ti
from evals.source_review import tob_run as tr

_SOL = re.compile(r"[\w\-./]+\.sol")
KEY_NAME = "wide"


def extra_labels(
    cache: Path, reports: Sequence[ti.Report], paths_of: Mapping[str, Sequence[str]]
) -> tuple[list[ti.Label], list[str]]:
    """(the Low and Informational findings that name a file of the bundle, the ids of those that do
    not). `paths_of[slug]` are the display paths of the report's bundle. A target names files by
    their file name; every bundle file with that name is taken."""
    labels: list[ti.Label] = []
    skipped: list[str] = []
    for r in reports:
        parsed = ti.read_pdf(cache / "pdfs" / r.file)
        if parsed.report is None:
            continue
        for raw in parsed.report.findings:
            severity = (raw.severity or "").lower()
            if severity not in ("low", "informational"):
                continue
            fid = raw.finding_id or f"F{raw.number:02d}"
            names = {Path(t).name for t in _SOL.findall(raw.target or "")}
            files = tuple(p for p in paths_of.get(r.slug, ()) if Path(p).name in names)
            if not files:
                skipped.append(f"{r.slug}:{fid}")
                continue
            page = str(raw.page or "")
            labels.append(
                ti.Label(
                    id=f"{r.slug}:{fid}",
                    report=r.slug,
                    finding_id=fid,
                    severity=severity,
                    title=raw.title,
                    target=raw.target or "",
                    files=files,
                    category=raw.category or "",
                    difficulty=raw.difficulty or "",
                    page=int(page) if page.isdigit() else 0,
                )
            )
    return labels, skipped


def widen(ctx):  # ctx: tob_eval_cli.Context (not imported: that module imports this one)
    """The context with the extra findings added to its labels and references. Dev only."""
    if ctx.side != "dev":
        raise ValueError("the wide key is for the dev side only; the held-out rules are frozen")
    paths = {slug: [f.path for f in src.bundle.files] for slug, src in ctx.sources.items()}
    extras, skipped = extra_labels(ctx.cache, ctx.reports, paths)
    labels_of = {slug: list(labs) for slug, labs in ctx.labels_of.items()}
    for lab in extras:
        labels_of.setdefault(lab.report, []).append(lab)
    refs = {**ctx.refs, **tr.references(extras, tr.descriptions(ctx.cache, ctx.reports))}
    return dataclasses.replace(
        ctx, labels_of=labels_of, refs=refs, key=KEY_NAME, wide_skipped=tuple(skipped)
    )
