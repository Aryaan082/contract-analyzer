"""The report manifest: which audit reports the findings come from, and how to get them again.

The PDFs are third-party documents, so they are not committed. The manifest is: it pins the source
repository to one commit and records each file's size, git blob hash and SHA-256, so anyone can
fetch the same bytes and see at once if they differ.
"""

import json
import unicodedata
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


def plain_text(value: str) -> str:
    """Reject text that could hide something: control characters other than newline, invisible
    format characters (zero-width, bidi), unassigned code points, or the U+FFFD that marks a PDF
    decode failure. Returns the value unchanged when it is clean."""
    for ch in value:
        category = unicodedata.category(ch)
        if (
            ch == "\ufffd"
            or category in ("Cf", "Co", "Cn", "Cs")
            or (category == "Cc" and ch != "\n")
        ):
            raise ValueError(f"unexpected character U+{ord(ch):04X}")
    return value


# Text that came from a PDF or a hand-written file: validated wherever it enters a record.
PlainText = Annotated[str, AfterValidator(plain_text)]


Severity = Literal["critical", "high", "medium", "low", "informational", "undetermined"]
SEVERITIES: tuple[Severity, ...] = (
    "critical",
    "high",
    "medium",
    "low",
    "informational",
    "undetermined",
)


class ReportSource(BaseModel, frozen=True):
    slug: str = Field(pattern=SLUG_PATTERN)
    file: str = Field(pattern=r"^[A-Za-z0-9._-]+\.pdf$")  # name inside the source's reviews/ folder
    protocol: str  # as the report's cover names it
    auditor: str
    date: str = Field(pattern=r"^\d{4}-\d{2}$")  # month of the report, from its cover
    size: int
    git_blob_sha: str = Field(pattern=r"^[0-9a-f]{40}$")  # what GitHub lists for the file
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Held out: the parser was developed without looking at this report's output, then run once
    # on it unchanged. See knowledge/README.md.
    held_out: bool = False


class Manifest(BaseModel, frozen=True):
    repository: str  # owner/name on GitHub
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    licence: str
    licence_url: str
    reports: tuple[ReportSource, ...]

    def download_url(self, report: ReportSource) -> str:
        return f"https://raw.githubusercontent.com/{self.repository}/{self.commit}/reviews/{report.file}"

    def report(self, slug: str) -> ReportSource:
        for report in self.reports:
            if report.slug == slug:
                return report
        raise KeyError(slug)


def load_manifest(path: Path | None = None) -> Manifest:
    from knowledge.paths import MANIFEST_PATH

    return Manifest.model_validate_json((path or MANIFEST_PATH).read_text())


def save_manifest(manifest: Manifest, path: Path | None = None) -> None:
    from knowledge.paths import MANIFEST_PATH

    text = json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False)
    (path or MANIFEST_PATH).write_text(text + "\n")
