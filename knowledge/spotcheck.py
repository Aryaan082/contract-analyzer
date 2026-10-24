"""The hand check: a page that shows a record beside its source page in the PDF.

The spec asks for a spot-check of 20 records by hand. This picks them (a fixed random seed, so the
sample is not chosen by whoever wants the check to pass: one from every report first, then random
ones) and writes `knowledge/spotcheck.html`. Open it in a browser: the record is on the left with a
tick box per property, the PDF opens at the record's page on the right. Nothing leaves your machine.

The page text comes from untrusted PDFs, so the page only ever sets it with textContent, never as
HTML. The output is git-ignored: it contains the report text and links to the local PDFs.
"""

import json
import random
from collections import defaultdict
from pathlib import Path

from knowledge.findings import Finding, FindingsFile, load_findings
from knowledge.models import load_manifest
from knowledge.paths import KNOWLEDGE_DIR, REPORTS_DIR

TEMPLATE_PATH = KNOWLEDGE_DIR / "spotcheck_template.html"
OUTPUT_PATH = KNOWLEDGE_DIR / "spotcheck.html"
DEFAULT_SEED = 20261024
DEFAULT_COUNT = 20


def choose_sample(
    files: list[FindingsFile], count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED
) -> list[Finding]:
    """One finding from each report, then random others, in a fixed pseudo-random order."""
    rng = random.Random(seed)
    by_report = {file.report: list(file.findings) for file in files if file.findings}
    chosen: list[Finding] = []
    for report in sorted(by_report):
        if len(chosen) < count:
            chosen.append(rng.choice(by_report[report]))
    taken = {f.id for f in chosen}
    rest = sorted(
        (f for fs in by_report.values() for f in fs if f.id not in taken), key=lambda f: f.id
    )
    rng.shuffle(rest)
    chosen += rest[: max(0, count - len(chosen))]
    rng.shuffle(chosen)
    return chosen


def sample_payload(sample: list[Finding], pdf_names: dict[str, str]) -> list[dict]:
    return [
        {
            "id": f.id,
            "report": f.report,
            "protocol": f.protocol,
            "date": f.date,
            "finding_id": f.finding_id,
            "severity": f.severity,
            "difficulty": f.difficulty,
            "category": f.category,
            "title": f.title,
            "affected_contract": f.affected_contract,
            "description": f.description,
            "recommendation": f.recommendation,
            "taxonomy": list(f.taxonomy),
            "page": f.page,
            "page_end": f.page_end,
            "pdf": f"reports/{pdf_names[f.report]}",
        }
        for f in sample
    ]


def write_sheet(
    count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED, output: Path | None = None
) -> Path:
    files = load_findings()
    manifest = load_manifest()
    pdf_names = {r.slug: r.file for r in manifest.reports}
    sample = choose_sample(files, count, seed)
    per_report: dict[str, int] = defaultdict(int)
    for f in sample:
        per_report[f.report] += 1
    data = json.dumps(sample_payload(sample, pdf_names), ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE_PATH.read_text().replace("/*DATA*/[]", data)
    path = output or OUTPUT_PATH
    path.write_text(html)
    missing = [n for n in pdf_names.values() if not (REPORTS_DIR / n).exists()]
    if missing:
        print(
            "warning: PDFs not downloaded (run `python -m knowledge fetch`):", *missing, sep="\n  "
        )
    return path
