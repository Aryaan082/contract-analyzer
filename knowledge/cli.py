"""Command line: `python -m knowledge <command>`.

fetch    download the audit PDFs listed in the manifest into knowledge/reports/ and check them
         (--record fills in any SHA-256 the manifest does not have yet)
parse    read the PDFs into findings files in knowledge/findings/, checking each report against
         its own findings table and totals; a report that fails a check writes nothing
         (--only SLUG, --held-out / --not-held-out choose reports, --dry-run writes nothing)
tag      tag every finding with the taxonomy entries it illustrates (tagging_rules.toml); needs
         no PDFs, so a rule can be changed and re-run at once
spotcheck  write knowledge/spotcheck.html: 20 records picked with a fixed seed, each shown
         beside its source page in the PDF, with a tick box per property
check    validate everything that is committed: taxonomy entries and their signal words, findings
         files, tags
"""

import argparse
import sys
from collections.abc import Sequence

from knowledge.fetch import FetchError, fetch_reports, with_recorded_hashes
from knowledge.findings import save_findings
from knowledge.models import load_manifest, save_manifest
from knowledge.parse.build import build_report
from knowledge.paths import REPORTS_DIR
from knowledge.tagging import TaggingError, TagReport, run_tagging
from knowledge.taxonomy import TaxonomyError, load_taxonomy


def _fetch(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    try:
        results = fetch_reports(manifest, REPORTS_DIR)
    except FetchError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    for result in results:
        print(f"{result.status:16} {result.slug}")
    if args.record:
        save_manifest(with_recorded_hashes(manifest, results))
        print("manifest updated with SHA-256 hashes")
    return 0


def _parse(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    chosen = [
        r
        for r in manifest.reports
        if (not args.only or r.slug in args.only)
        and not (args.held_out and not r.held_out)
        and not (args.not_held_out and r.held_out)
    ]
    failed = 0
    for report in chosen:
        result = build_report(manifest, report, REPORTS_DIR / report.file)
        stated = result.file.stated_counts if result.file else "-"
        status = "ok" if result.file else "FAILED"
        kept = f"{len(result.file.findings)} kept, " if result.file else ""
        print(
            f"{status:7} {report.slug}: {result.parsed_count} findings read, {kept}"
            f"{result.table_rows} table rows, stated totals {stated}"
        )
        for note in result.notes:
            print(f"        note: {note}")
        for problem in result.problems:
            print(f"        problem: {problem}")
        if result.file and not args.dry_run:
            save_findings(result.file)
        failed += result.file is None
    if not args.dry_run:
        _print_tags(run_tagging())
    return 1 if failed else 0


def _print_tags(report: TagReport) -> None:
    print(f"tagged {report.tagged} of {report.total} findings; per entry: {dict(report.per_tag)}")


def _tag(args: argparse.Namespace) -> int:
    try:
        report = run_tagging()
    except (TaggingError, TaxonomyError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    _print_tags(report)
    if args.untagged:
        print("untagged:", *report.untagged, sep="\n  ")
    return 0


def _spotcheck(args: argparse.Namespace) -> int:
    from knowledge.spotcheck import write_sheet

    path = write_sheet(args.count, args.seed)
    print(f"wrote {path}\nopen it in a browser: file://{path}")
    return 0


def _coverage(args: argparse.Namespace) -> int:
    from knowledge.findings import load_findings
    from knowledge.tagging import coverage

    entries = load_taxonomy()
    counts = coverage(load_findings(), list(entries))
    have = [slug for slug, n in counts.items() if n]
    print(f"{len(have)} of {len(entries)} entries have at least one kept finding")
    for group in ("code_bugs", "defi", "upgradeability", "admin_power", "token_tricks"):
        missing = [s for s, e in entries.items() if e.group == group and not counts[s]]
        if missing:
            print(f"  no finding yet ({group}): {', '.join(missing)}")
    return 0


def _check(args: argparse.Namespace) -> int:
    from knowledge.findings import all_findings
    from knowledge.vocabulary import unknown_signals

    try:
        entries = load_taxonomy()
        findings = all_findings()
    except (TaxonomyError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    problems = [
        f"{e.slug}: no tool can say {bad}"
        for e in entries.values()
        for bad in unknown_signals(e.signals)
    ]
    problems += [
        f"{f.id}: tag {t!r} is not a taxonomy entry"
        for f in findings
        for t in f.taxonomy
        if t not in entries
    ]
    for problem in problems:
        print("problem:", problem)
    print(f"{len(entries)} taxonomy entries, {len(findings)} findings, {len(problems)} problems")
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m knowledge")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="download and check the audit PDFs")
    fetch.add_argument("--record", action="store_true", help="record missing SHA-256 hashes")
    fetch.set_defaults(run=_fetch)
    parse = commands.add_parser("parse", help="parse the PDFs into findings files")
    parse.add_argument("--only", action="append", metavar="SLUG")
    parse.add_argument("--held-out", action="store_true", help="only the held-out reports")
    parse.add_argument("--not-held-out", action="store_true", help="only the other reports")
    parse.add_argument("--dry-run", action="store_true", help="check, but write nothing")
    parse.set_defaults(run=_parse)
    tag = commands.add_parser("tag", help="tag findings with taxonomy entries")
    tag.add_argument("--untagged", action="store_true", help="list the findings with no tag")
    tag.set_defaults(run=_tag)
    spot = commands.add_parser("spotcheck", help="write the hand-check page")
    spot.add_argument("--count", type=int, default=20)
    spot.add_argument("--seed", type=int, default=20261024)
    spot.set_defaults(run=_spotcheck)
    cover = commands.add_parser("coverage", help="entries with no kept finding yet")
    cover.set_defaults(run=_coverage)
    check = commands.add_parser("check", help="validate the committed taxonomy and findings")
    check.set_defaults(run=_check)
    args = parser.parse_args(argv)
    return args.run(args)
