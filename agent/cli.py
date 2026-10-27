"""Command line: `python -m agent <command>`."""

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence

from agent.config import load_config
from agent.contracts import find_contract, load_candidates, load_contracts
from agent.llm import OpenRouterModel
from agent.loop import run_agent
from agent.prompts import DEFAULT_QUESTION
from agent.report import render_text
from agent.tools import RunContext
from agent.trace import write_trace
from chain.config import DEFAULT_CHAIN, load_env
from chain.errors import ChainError
from chain.rpc import RpcClient


def cmd_contracts() -> int:
    for heading, group in (
        ("fixed five", load_contracts()),
        ("extra candidates", load_candidates()),
    ):
        print(f"{heading}:")
        for contract in group.values():
            print(f"  {contract.id:<13} {contract.chain} {contract.address} block {contract.block}")
            print(f"                {contract.label}")
    return 0


def cmd_analyze(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    config = load_config()
    limits = config.limits
    if args.max_cost is not None:
        limits = limits.model_copy(update={"max_cost_usd": args.max_cost})
    address, chain, block, question = args.address, args.chain, args.block, args.question
    if args.contract:
        chosen = find_contract(args.contract)
        if chosen is None:
            have = ", ".join([*load_contracts(), *load_candidates()])
            print(f"No contract {args.contract!r}. Have: {have}", file=sys.stderr)
            return 2
        address, chain = chosen.address, chosen.chain
        block = args.block if args.block is not None else chosen.block
    if not address:
        print("Give an address, or --contract <id>.", file=sys.stderr)
        return 2
    try:
        client = RpcClient.connect(chain, env, block)
        model = OpenRouterModel(
            config.resolve_model(args.model), timeout=limits.request_timeout_seconds
        )
        run = run_agent(model, RunContext(client, chain), address, question, limits=limits)
    except ChainError as err:
        print(err.message, file=sys.stderr)
        return 2
    if run.error:
        print(run.error, file=sys.stderr)
    print(run.report.model_dump_json(indent=2) if args.json else render_text(run.report))
    if not args.no_trace:
        print(f"trace: {write_trace(run, limits)}", file=sys.stderr)
    return 0 if run.report.validation.accepted else 1


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log scrubbed failures")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("contracts", help="list the fixed set of contracts")
    analyze = commands.add_parser(
        "analyze", help="analyse a contract and print a cited report (calls the model: costs money)"
    )
    analyze.add_argument("address", nargs="?", help="the contract's address")
    analyze.add_argument(
        "--contract", help="an id from `python -m agent contracts` (fixed or extra)"
    )
    analyze.add_argument("--chain", default=DEFAULT_CHAIN, help="default ethereum")
    analyze.add_argument("--block", type=int, help="pin to this block (default: latest)")
    analyze.add_argument("--question", default=DEFAULT_QUESTION)
    analyze.add_argument("--model", help="a tier from models.toml (sonnet, haiku) or a slug")
    analyze.add_argument("--max-cost", type=float, help="cost cap in USD for this run")
    analyze.add_argument("--json", action="store_true", help="print the report as JSON")
    analyze.add_argument("--no-trace", action="store_true", help="do not write agent/runs/*.json")
    args = parser.parse_args(argv)

    # Only our own loggers: web3's DEBUG lines include the RPC URL.
    for name in ("chain", "agent"):
        log = logging.getLogger(name)
        log.handlers[:] = [logging.StreamHandler()]
        log.propagate = False
        log.setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    if args.command == "contracts":
        return cmd_contracts()
    if env is None:
        load_env()
        env = os.environ
    return cmd_analyze(args, env)
