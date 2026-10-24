"""From a PDF in the manifest to a validated findings file (`python -m knowledge parse`)."""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from knowledge.findings import Finding, FindingsFile, Source
from knowledge.models import Manifest, ReportSource
from knowledge.parse.check import check_parsed
from knowledge.parse.pdf_text import read_lines, repair_unknown_characters
from knowledge.parse.tob import ParsedReport, ParseError, RawFinding, parse_report
from knowledge.selection import Review, SelectionError, load_reviews, select


@dataclass(slots=True)
class BuildResult:
    slug: str
    file: FindingsFile | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cover_date: str | None = None
    parsed_count: int = 0
    table_rows: int = 0


def _record(manifest: Manifest, report: ReportSource, raw: RawFinding) -> Finding:
    finding_id = raw.finding_id or f"F{raw.number:02d}"
    return Finding(
        id=f"{report.slug}:{finding_id}",
        report=report.slug,
        protocol=report.protocol,
        auditor=report.auditor,
        date=report.date,
        finding_id=finding_id,
        severity=raw.severity,  # type: ignore[arg-type]  # checked by check_parsed and the model
        title=raw.title,
        category=raw.category,
        difficulty=raw.difficulty,
        affected_contract=raw.target,
        description=raw.description,
        recommendation=raw.recommendation,
        page=raw.page,
        page_end=raw.page_end,
        url=manifest.download_url(report),
    )


def _file(
    manifest: Manifest, report: ReportSource, parsed: ParsedReport, reviews: dict[str, Review]
) -> tuple[FindingsFile | None, list[str]]:
    records = [_record(manifest, report, raw) for raw in parsed.findings]
    kept, excluded, problems = select(records, reviews, report.slug)
    if problems:
        return None, problems
    file = FindingsFile(
        report=report.slug,
        source=Source(
            repository=manifest.repository,
            commit=manifest.commit,
            file=report.file,
            sha256=report.sha256,
            licence=manifest.licence,
            licence_url=manifest.licence_url,
        ),
        stated_counts={k: v for k, v in parsed.exposure.items() if v},
        table_rows=len(parsed.table_rows),
        findings=tuple(kept),
        excluded=tuple(excluded),
    )
    return file, []


def build_report(manifest: Manifest, report: ReportSource, pdf: Path) -> BuildResult:
    """Parse one report and check it. `file` is set only if every check passed."""
    result = BuildResult(report.slug)
    try:
        lines, unrepaired = repair_unknown_characters(read_lines(pdf))
        parsed = parse_report(lines)
    except (ParseError, ValueError, OSError) as err:
        result.problems.append(f"could not parse: {err}")
        return result
    result.cover_date = parsed.cover_date
    result.parsed_count, result.table_rows = len(parsed.findings), len(parsed.table_rows)
    checked = check_parsed(parsed, unrepaired)
    result.problems, result.notes = checked.problems, checked.notes
    if parsed.cover_date and parsed.cover_date != report.date:
        result.problems.append(f"cover says {parsed.cover_date}, manifest says {report.date}")
    if result.problems:
        return result
    try:
        result.file, selection_problems = _file(manifest, report, parsed, load_reviews())
        result.problems += selection_problems
    except SelectionError as err:
        result.problems.append(str(err))
    except ValidationError as err:
        result.problems += [
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors()[:10]
        ]
    return result
