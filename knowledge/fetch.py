"""Fetching the audit PDFs listed in the manifest. The only network code in `knowledge/`.

Run as `python -m knowledge fetch`, never while analysing a contract. A download is accepted only if
it is the size and git blob hash the manifest records (and the SHA-256, once recorded), so a
changed or truncated file is rejected instead of quietly parsed. The PDFs are untrusted input.
"""

import hashlib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from knowledge.models import Manifest, ReportSource

ALLOWED_HOST = "raw.githubusercontent.com"
MAX_BYTES = 30_000_000
REQUEST_TIMEOUT_SECONDS = 60

Downloader = Callable[[str], bytes]


class FetchError(Exception):
    """A report could not be fetched or did not match the manifest. Safe to print."""


def download(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise FetchError(f"refusing to fetch from {parsed.hostname!r}")
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BYTES + 1)
    except OSError as err:
        raise FetchError(f"download failed: {type(err).__name__}") from None
    if len(body) > MAX_BYTES:
        raise FetchError("file is larger than expected")
    return body


def git_blob_sha(data: bytes) -> str:
    """The hash git gives a file: sha1 of `blob <length>\\0` followed by the bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def problems(report: ReportSource, data: bytes) -> list[str]:
    found = []
    if not data.startswith(b"%PDF-"):
        found.append("not a PDF")
    if len(data) != report.size:
        found.append(f"size is {len(data)}, manifest says {report.size}")
    if git_blob_sha(data) != report.git_blob_sha:
        found.append("git blob hash differs from the manifest")
    if report.sha256 and hashlib.sha256(data).hexdigest() != report.sha256:
        found.append("SHA-256 differs from the manifest")
    return found


@dataclass(frozen=True, slots=True)
class FetchResult:
    slug: str
    status: str  # "downloaded" | "already_present"
    sha256: str


def fetch_reports(
    manifest: Manifest, dest: Path, *, downloader: Downloader = download
) -> list[FetchResult]:
    """Download every report that is missing or fails its checks. Nothing bad is left on disk."""
    dest.mkdir(parents=True, exist_ok=True)
    results = []
    for report in manifest.reports:
        path = dest / report.file
        if path.exists() and not problems(report, path.read_bytes()):
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            results.append(FetchResult(report.slug, "already_present", sha))
            continue
        data = downloader(manifest.download_url(report))
        wrong = problems(report, data)
        if wrong:
            raise FetchError(f"{report.slug}: {'; '.join(wrong)}")
        path.write_bytes(data)
        results.append(FetchResult(report.slug, "downloaded", hashlib.sha256(data).hexdigest()))
    return results


def with_recorded_hashes(manifest: Manifest, results: list[FetchResult]) -> Manifest:
    """The manifest with a SHA-256 filled in for each report that had none. Never changes one."""
    shas = {r.slug: r.sha256 for r in results}
    reports = tuple(
        r if r.sha256 else r.model_copy(update={"sha256": shas[r.slug]}) for r in manifest.reports
    )
    return manifest.model_copy(update={"reports": reports})
