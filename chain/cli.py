"""Command line: `python -m chain <command>`."""

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence

from chain.config import load_env
from chain.rpc import probe_chains


def cmd_list(env: Mapping[str, str]) -> int:
    """Show each configured chain with the chain id its node reports. URLs are never printed."""
    results = probe_chains(env)
    if not results:
        print(
            "No chains configured. Copy .env.example to .env and set an RPC_URL_<NAME> line.",
            file=sys.stderr,
        )
        return 1

    width = max(len(name) for name in [*results, "NAME"])
    print(f"{'NAME':<{width}}  {'CHAIN ID':<10}  STATUS")
    failures = []
    for name, (chain_id, err) in results.items():
        status = (
            "ok" if err is None else "unreachable" if err.code == "rpc_unreachable" else "error"
        )
        print(f"{name:<{width}}  {chain_id if chain_id is not None else '-':<10}  {status}")
        if err is not None:
            failures.append(f"  {name}: {err.message}")
    if failures:
        print("\n" + "\n".join(failures))
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m chain", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log scrubbed RPC failures")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list configured chains and check each node answers")
    serve = commands.add_parser("serve", help="run the local inspector page (127.0.0.1 only)")
    serve.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    args = parser.parse_args(argv)

    # Only our own logger: web3's DEBUG lines include the RPC URL.
    log = logging.getLogger("chain")
    log.handlers[:] = [logging.StreamHandler()]
    log.propagate = False
    log.setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    if env is None:
        load_env()
        env = os.environ

    if args.command == "list":
        return cmd_list(env)
    if args.command == "serve":
        from chain.inspector import serve as run_inspector

        return run_inspector(env, args.port)
    return 2
