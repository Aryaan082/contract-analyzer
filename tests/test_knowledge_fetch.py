"""The manifest and the report download, with a fake downloader: no network."""

import hashlib

import pytest

from knowledge.fetch import (
    FetchError,
    FetchResult,
    download,
    fetch_reports,
    git_blob_sha,
    problems,
    with_recorded_hashes,
)
from knowledge.models import Manifest, ReportSource, load_manifest

PDF = b"%PDF-1.4 a pretend report"


def _report(data: bytes = PDF, **changes) -> ReportSource:
    fields = {
        "slug": "test-tob-2024-01",
        "file": "test.pdf",
        "protocol": "Test",
        "auditor": "Trail of Bits",
        "date": "2024-01",
        "size": len(data),
        "git_blob_sha": git_blob_sha(data),
        "sha256": None,
    }
    return ReportSource(**{**fields, **changes})


def _manifest(*reports: ReportSource) -> Manifest:
    return Manifest(
        repository="trailofbits/publications",
        commit="a" * 40,
        licence="CC-BY-SA-4.0",
        licence_url="https://example.invalid/LICENSE",
        reports=reports,
    )


def test_git_blob_sha_matches_what_git_computes():
    # `printf 'hello\n' | git hash-object --stdin`
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_the_download_url_is_pinned_to_the_commit():
    manifest = _manifest(_report())
    assert manifest.download_url(manifest.reports[0]) == (
        f"https://raw.githubusercontent.com/trailofbits/publications/{'a' * 40}/reviews/test.pdf"
    )


def test_a_good_download_is_saved_and_a_second_run_downloads_nothing(tmp_path):
    manifest = _manifest(_report())
    calls = []

    def fake(url):
        calls.append(url)
        return PDF

    first = fetch_reports(manifest, tmp_path, downloader=fake)
    again = fetch_reports(manifest, tmp_path, downloader=fake)
    assert [r.status for r in first] == ["downloaded"]
    assert [r.status for r in again] == ["already_present"]
    assert len(calls) == 1
    assert (tmp_path / "test.pdf").read_bytes() == PDF
    assert first[0].sha256 == hashlib.sha256(PDF).hexdigest()


@pytest.mark.parametrize(
    ("served", "message"),
    [
        (PDF + b"x", "size is"),
        (b"%PDF-1.4 a different!!!", "git blob hash differs"),
        (b"<html>not a pdf, same length!</html>"[: len(PDF)].ljust(len(PDF)), "not a PDF"),
    ],
)
def test_a_download_that_does_not_match_is_rejected_and_not_left_on_disk(tmp_path, served, message):
    manifest = _manifest(_report())
    with pytest.raises(FetchError, match=message):
        fetch_reports(manifest, tmp_path, downloader=lambda url: served)
    assert not (tmp_path / "test.pdf").exists()


def test_a_recorded_sha256_is_enforced():
    report = _report(sha256="0" * 64)
    assert "SHA-256 differs from the manifest" in problems(report, PDF)


def test_a_damaged_file_on_disk_is_fetched_again(tmp_path):
    manifest = _manifest(_report())
    (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 truncated")
    result = fetch_reports(manifest, tmp_path, downloader=lambda url: PDF)
    assert result[0].status == "downloaded"
    assert (tmp_path / "test.pdf").read_bytes() == PDF


def test_recording_hashes_fills_gaps_and_never_changes_one():
    known = "1" * 64
    manifest = _manifest(_report(sha256=known), _report(slug="other-tob-2024-01", file="o.pdf"))
    results = [FetchResult(r.slug, "downloaded", "2" * 64) for r in manifest.reports]
    updated = with_recorded_hashes(manifest, results)
    assert [r.sha256 for r in updated.reports] == [known, "2" * 64]


@pytest.mark.parametrize(
    "url",
    [
        "http://raw.githubusercontent.com/x/y/z.pdf",  # not https
        "https://example.com/x.pdf",  # not the allowed host
        "https://raw.githubusercontent.com.evil.example/x.pdf",
        "file:///etc/passwd",
    ],
)
def test_only_the_one_https_host_is_ever_fetched(url):
    with pytest.raises(FetchError, match="refusing"):
        download(url)


def test_the_committed_manifest_is_valid_and_pinned():
    manifest = load_manifest()
    assert manifest.licence == "CC-BY-SA-4.0"
    assert manifest.repository == "trailofbits/publications"
    slugs = [r.slug for r in manifest.reports]
    assert len(slugs) == len(set(slugs))
    assert 10 <= len(slugs) <= 15  # the spec's range
    assert all(r.sha256 for r in manifest.reports)  # recorded when first downloaded
    assert all(r.auditor == "Trail of Bits" for r in manifest.reports)
    assert sum(r.held_out for r in manifest.reports) == 2
