"""Findings records: one per problem an auditor reported, with where in which report it came from.

A record is plain strings and a few fixed-vocabulary values. It is built from an untrusted PDF, so
every text field is validated on the way in and again on the way out of the JSON files: no control
or invisible characters, no placeholder characters from a failed PDF decode, bounded length. A
record is evidence to quote, never an instruction; consumers must present it as quoted data.

IDs are stable and are what citations use: `<report slug>:<finding id>`, with the page beside it.
"""

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from knowledge.models import SEVERITIES, SLUG_PATTERN, PlainText, Severity
from knowledge.paths import FINDINGS_DIR

FINDING_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
MAX_TITLE = 300
MAX_TARGET = 1200
MAX_DESCRIPTION = 20_000
MAX_RECOMMENDATION = 8_000
MIN_DESCRIPTION = 40


class Finding(BaseModel, frozen=True):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    report: str = Field(pattern=SLUG_PATTERN)
    protocol: PlainText = Field(max_length=200)
    auditor: PlainText = Field(max_length=100)
    date: str = Field(pattern=r"^\d{4}-\d{2}$")
    finding_id: str = Field(pattern=FINDING_ID_PATTERN)  # the report's own identifier
    severity: Severity
    title: PlainText = Field(min_length=3, max_length=MAX_TITLE)
    category: PlainText | None = Field(default=None, max_length=100)  # the auditor's own label
    difficulty: PlainText | None = Field(default=None, max_length=50)
    affected_contract: PlainText | None = Field(default=None, max_length=MAX_TARGET)
    description: PlainText = Field(min_length=MIN_DESCRIPTION, max_length=MAX_DESCRIPTION)
    recommendation: PlainText = Field(min_length=3, max_length=MAX_RECOMMENDATION)
    page: int = Field(ge=1, le=1000)  # physical PDF page where the finding starts
    page_end: int = Field(ge=1, le=1000)
    url: str = Field(pattern=r"^https://raw\.githubusercontent\.com/[A-Za-z0-9._/-]+\.pdf$")
    # Our own annotation, not the auditor's: taxonomy entries this finding illustrates, assigned by
    # the rules in tagging_rules.toml. A hint for retrieval, never evidence.
    taxonomy: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.id != f"{self.report}:{self.finding_id}":
            raise ValueError("id must be '<report>:<finding_id>'")
        if self.page_end < self.page:
            raise ValueError("page_end is before page")
        return self

    @property
    def cite_url(self) -> str:
        """The PDF, opened at the finding's first page."""
        return f"{self.url}#page={self.page}"


class Source(BaseModel, frozen=True):
    repository: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    file: str
    sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    licence: str
    licence_url: str


ExclusionReason = Literal["below_severity", "not_an_exploit"]


class Exclusion(BaseModel, frozen=True):
    """A finding the report lists that is deliberately not kept, with the reason. Keeping these
    short entries means the report's own table can still be matched row for row."""

    finding_id: str = Field(pattern=FINDING_ID_PATTERN)
    severity: Severity
    title: PlainText = Field(min_length=3, max_length=MAX_TITLE)
    reason: ExclusionReason
    why: PlainText | None = Field(default=None, max_length=300)  # the reviewer's reason

    @model_validator(mode="after")
    def _why_iff_reviewed(self) -> Self:
        if (self.reason == "not_an_exploit") != bool(self.why):
            raise ValueError("a reason is given for 'not_an_exploit' and for nothing else")
        return self


class FindingsFile(BaseModel, frozen=True):
    """The kept findings of one report, plus the ones left out, checked against the report's own
    table and totals: kept and excluded together must account for every row and every severity."""

    report: str = Field(pattern=SLUG_PATTERN)
    source: Source
    # Findings per severity as the report itself states them; empty if it states none.
    stated_counts: dict[str, int] = Field(default_factory=dict)
    table_rows: int = Field(ge=0)  # rows in the report's findings table
    findings: tuple[Finding, ...]  # kept: see knowledge/selection.py
    excluded: tuple[Exclusion, ...] = ()

    @model_validator(mode="after")
    def _counts_agree(self) -> Self:
        ids = [f.finding_id for f in self.findings] + [e.finding_id for e in self.excluded]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate finding ids")
        if any(f.report != self.report for f in self.findings):
            raise ValueError("a finding belongs to another report")
        total = len(self.findings) + len(self.excluded)
        if self.table_rows != total:
            raise ValueError(f"{self.table_rows} table rows but {total} findings accounted for")
        if self.stated_counts:
            severities = [f.severity for f in self.findings] + [e.severity for e in self.excluded]
            for severity in SEVERITIES:
                have = severities.count(severity)
                stated = self.stated_counts.get(severity, 0)
                if have != stated:
                    raise ValueError(f"{severity}: report states {stated}, parsed {have}")
        return self


def save_findings(file: FindingsFile, directory: Path | None = None) -> Path:
    path = (directory or FINDINGS_DIR) / f"{file.report}.json"
    text = json.dumps(file.model_dump(mode="json"), indent=2, ensure_ascii=False)
    path.write_text(text + "\n")
    return path


def load_findings(directory: Path | None = None) -> list[FindingsFile]:
    """Every findings file, validated. A bad file raises instead of being skipped."""
    folder = directory or FINDINGS_DIR
    return [
        FindingsFile.model_validate_json(path.read_text()) for path in sorted(folder.glob("*.json"))
    ]


def all_findings(directory: Path | None = None) -> list[Finding]:
    return [f for file in load_findings(directory) for f in file.findings]
