"""Command line: `python -m references <command>`.

  list    the library: every reference, with counts
  match   which references a contract resembles          (--code and/or --selectors)
  diff    extras and missing functions against one reference
  risk    capabilities the contract appears to have
  add     fetch a deployed contract, check it, and save it as a new reference

`--selectors` and `--code` take the value itself or `@file`. Selectors are comma or space
separated. Give `--code` alone and the selectors are read from it (with evmole); give both and
the selectors given are used.
"""

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import dotenv_values

from chain.errors import ChainError, ToolError
from references.acquire import AcquireError, build_deployed_reference, save_reference
from references.library import LibraryError, get_library
from references.match import DEFAULT_LIMIT
from references.models import NameCandidate
from references.tools import Names, run_diff, run_match, run_risk


def _value(raw: str) -> str:
    return Path(raw[1:]).read_text() if raw.startswith("@") else raw


def _selectors(raw: str) -> list[str]:
    return [s for s in re.split(r"[\s,]+", _value(raw).strip()) if s]


def _load_names(raw: str) -> Names:
    """JSON: {"0xa9059cbb": ["transfer(address,uint256)", {"name": "...", "filtered": true}]}"""
    loaded = json.loads(_value(raw))
    if not isinstance(loaded, dict):
        raise ValueError("names must be a JSON object keyed by selector")
    return {
        selector: [NameCandidate(**i) if isinstance(i, dict) else str(i) for i in items]
        for selector, items in loaded.items()
    }


def _print_result(result) -> int:
    print(result.model_dump_json(indent=2))
    return 1 if isinstance(result.data, ToolError) else 0


def cmd_list() -> int:
    try:
        library = get_library()
    except LibraryError as err:
        print(err, file=sys.stderr)
        return 1
    rows = [
        (
            p.reference.id,
            p.reference.kind,
            p.reference.family,
            p.reference.origin.type,
            str(len(p.reference.functions)),
            str(len(p.code)) if p.code else "-",
        )
        for p in library.all
    ]
    header = ("ID", "KIND", "FAMILY", "ORIGIN", "FUNCTIONS", "CODE BYTES")
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))
    s = library.stats()
    print(
        f"\n{len(library)} entries: {s.contracts} contracts, {s.modules} modules "
        f"({s.compiled} compiled, {s.deployed} deployed, {s.spec} from a standard). "
        f"{s.functions} functions, {s.distinct_selectors} distinct selectors. "
        f"Library digest {s.digest[:12]}."
    )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    env = {k: v for k, v in dotenv_values(args.env_file).items() if v} if args.env_file else None
    if env is None:
        from chain.config import load_env

        env = load_env()
    try:
        reference = build_deployed_reference(
            reference_id=args.id,
            name=args.name,
            family=args.family,
            chain=args.chain,
            address=args.address,
            env=env,
            block=args.block,
        )
        path = save_reference(reference)
    except (AcquireError, ChainError) as err:
        print(f"Not added: {err}", file=sys.stderr)
        return 1
    origin = reference.origin
    print(
        f"Added {reference.id}: {len(reference.functions)} functions, code hash "
        f"{reference.code_hash}, {origin.chain} block {getattr(origin, 'block', '?')}.\n"
        f"Saved {path.name}. Review it, then commit."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m references", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list the reference library")

    for name, help_text in (("match", "find similar references"), ("risk", "risk heuristics")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--selectors", help="4-byte selectors, or @file (default: from --code)")
        sub.add_argument("--code", help="runtime bytecode hex, or @file")
        if name == "match":
            sub.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
        else:
            sub.add_argument("--names", help="JSON of candidate names per selector, or @file")

    diff = commands.add_parser("diff", help="extras and missing functions against a reference")
    diff.add_argument("reference_id")
    diff.add_argument("--selectors", help="4-byte selectors, or @file (default: from --code)")
    diff.add_argument("--code", help="runtime bytecode hex, or @file")
    diff.add_argument("--names", help="JSON of candidate names per selector, or @file")

    add = commands.add_parser("add", help="fetch a deployed contract into the library")
    add.add_argument("--id", required=True, help="stable id, lowercase with dashes")
    add.add_argument("--name", required=True)
    add.add_argument("--family", required=True)
    add.add_argument("--chain", required=True)
    add.add_argument("--address", required=True)
    add.add_argument("--block", type=int, help="pin to a block (default: latest)")
    add.add_argument("--env-file", help="file with RPC_URL_<CHAIN> lines (default: ./.env)")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            return cmd_list()
        if args.command == "add":
            return cmd_add(args)
        names: Mapping | None = _load_names(args.names) if getattr(args, "names", None) else None
        selectors = _selectors(args.selectors) if args.selectors else None
        code = _value(args.code).strip() if getattr(args, "code", None) else None
    except (OSError, ValueError, TypeError) as err:
        print(f"Bad input: {err}", file=sys.stderr)
        return 2
    if args.command == "match":
        return _print_result(run_match(selectors, code, limit=args.limit))
    if args.command == "diff":
        return _print_result(run_diff(selectors, args.reference_id, code_hex=code, names=names))
    return _print_result(run_risk(selectors, code, names=names))
