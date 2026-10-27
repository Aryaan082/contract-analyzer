"""Command line: `python -m chain <command>`."""

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

from web3 import Web3

from chain.config import DEFAULT_CHAIN, load_env
from chain.errors import ChainError, ToolError
from chain.identify import Identity, identify_contract
from chain.models import Provenance, ToolResult, normalize_address
from chain.profile import Services, build_profile, failed_profile, failed_tools
from chain.rpc import RpcClient, probe_chains
from chain.selectors import SelectorsData, run_extract_selectors
from chain.simulate import DEFAULT_TRADE_WEI, SellSimulation, simulate_sell


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


def cmd_selectors(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    """Print the `{provenance, data}` result. With --address the code is read from the chain
    first (pinned to one block); with --code there is no chain, so provenance says so."""
    if args.code is not None:
        try:
            text = Path(args.code[1:]).read_text() if args.code.startswith("@") else args.code
        except OSError as err:
            print(f"Cannot read {args.code[1:]}: {err.strerror}", file=sys.stderr)
            return 2
        provenance = Provenance(chain="unknown", chain_id=None, address=None, block=None, source="")
    else:
        try:
            address = normalize_address(args.address)
            client = RpcClient.connect(args.chain, env, args.block)
            text = Web3.to_hex(client.get_code(address))
            provenance = client.provenance(address)
        except ChainError as err:
            failed = Provenance(
                chain=args.chain, chain_id=None, address=None, block=None, source=""
            )
            result = ToolResult[SelectorsData](provenance=failed, data=err.to_tool_error())
            print(result.model_dump_json(indent=2))
            return 1
    result = run_extract_selectors(text, provenance)
    print(result.model_dump_json(indent=2))
    return 1 if isinstance(result.data, ToolError) else 0


def _eth_to_wei(text: str) -> int:
    try:
        amount = Decimal(text)
    except InvalidOperation:
        amount = Decimal(-1)
    if not amount.is_finite() or amount <= 0:
        raise ChainError("invalid_amount", "The trade size must be a positive number of ETH.")
    return int(amount * 10**18)


def cmd_simulate_sell(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    """Rehearse a buy and a sell of the token on a scratch copy of the chain. Nothing is sent."""
    try:
        trade_wei = DEFAULT_TRADE_WEI if args.amount_eth is None else _eth_to_wei(args.amount_eth)
        client = RpcClient.connect(args.chain, env, args.block)
    except ChainError as err:
        failed = Provenance(chain=args.chain, chain_id=None, address=None, block=None, source="")
        print(
            ToolResult[SellSimulation](provenance=failed, data=err.to_tool_error()).model_dump_json(
                indent=2
            )
        )
        return 1
    result = simulate_sell(client, args.token, trade_wei)
    print(result.model_dump_json(indent=2))
    return 1 if isinstance(result.data, ToolError) else 0


def cmd_identify(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    """Say what kind of contract this is, whether a protocol's own registry lists it, and which
    further checks fit it."""
    try:
        client = RpcClient.connect(args.chain, env, args.block)
    except ChainError as err:
        failed = Provenance(chain=args.chain, chain_id=None, address=None, block=None, source="")
        print(
            ToolResult[Identity](provenance=failed, data=err.to_tool_error()).model_dump_json(
                indent=2
            )
        )
        return 1
    result = identify_contract(client, args.address)
    print(result.model_dump_json(indent=2))
    return 1 if isinstance(result.data, ToolError) else 0


def cmd_profile(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    """Run every tool on one address and print all their answers as one JSON document. Exit
    status 1 only if the code could not be read; errors from other tools are in the document."""
    try:
        address = normalize_address(args.address)
        client = RpcClient.connect(args.chain, env, args.block)
    except ChainError as err:
        print(failed_profile(args.chain, str(args.address), err).model_dump_json(indent=2))
        return 1
    profile = build_profile(client, address, Services.from_env(env))
    print(profile.model_dump_json(indent=2))
    failed = failed_tools(profile)
    if failed:
        print("Tools that returned an error: " + ", ".join(failed), file=sys.stderr)
    return 1 if "code" in failed else 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m chain", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log scrubbed RPC failures")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list configured chains and check each node answers")
    serve = commands.add_parser("serve", help="run the local inspector page (127.0.0.1 only)")
    serve.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    selectors = commands.add_parser(
        "selectors", help="list the function selectors in a contract's code"
    )
    source = selectors.add_mutually_exclusive_group(required=True)
    source.add_argument("--code", help="runtime bytecode as hex, or @file")
    source.add_argument("--address", help="read the code from this address")
    selectors.add_argument(
        "--chain", default=DEFAULT_CHAIN, help="with --address (default ethereum)"
    )
    selectors.add_argument("--block", type=int, help="with --address: pin to this block")
    identify = commands.add_parser(
        "identify",
        help="what kind of contract this is, whether a protocol lists it, which checks fit",
    )
    identify.add_argument("--address", required=True, help="the contract's address")
    identify.add_argument("--chain", default=DEFAULT_CHAIN, help="default ethereum")
    identify.add_argument("--block", type=int, help="pin to this block (default: latest)")
    profile = commands.add_parser(
        "profile",
        help="run every tool on an address and print one JSON profile with provenance",
    )
    profile.add_argument("address", help="the contract's address")
    profile.add_argument("--chain", default=DEFAULT_CHAIN, help="default ethereum")
    profile.add_argument("--block", type=int, help="pin to this block (default: latest)")
    sell = commands.add_parser(
        "simulate-sell",
        help="rehearse buying then selling a token on a scratch copy of the chain (sends nothing)",
    )
    sell.add_argument("--token", required=True, help="the token's address")
    sell.add_argument("--chain", default=DEFAULT_CHAIN, help="default ethereum")
    sell.add_argument("--block", type=int, help="pin to this block (default: latest)")
    sell.add_argument("--amount-eth", help="size of the rehearsal buy in ETH (default 0.01)")
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
    if args.command == "selectors":
        return cmd_selectors(args, env)
    if args.command == "simulate-sell":
        return cmd_simulate_sell(args, env)
    if args.command == "identify":
        return cmd_identify(args, env)
    if args.command == "profile":
        return cmd_profile(args, env)
    return 2
