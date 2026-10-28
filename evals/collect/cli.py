"""`python -m evals.collect scan` : discover, inspect and measure candidate tokens, resumably.

Read-only: node reads and Etherscan log reads. Needs ETHERSCAN_API_KEY and RPC_URL_ETHEREUM from the
environment or from the .env file given with --env. Nothing is printed that could hold a key; error
texts come from `chain.errors`.
"""

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values

from chain.errors import ChainError
from evals.collect.chaindata import LiveChain
from evals.collect.existing import fixed_addresses
from evals.collect.rules import HASH_PATH, TRANSFER_TOPIC, ZERO, load_rules, rules_hash
from evals.collect.scan import (
    Candidate,
    Inspection,
    discover,
    family_key,
    inspect,
    late_sellers,
    measure,
    split_key,
)
from evals.collect.store import CACHE, Table


def _log(message: str) -> None:
    print(message, flush=True)


def families_to_measure(inspections: list[Inspection], limit: int) -> list[Inspection]:
    """At most `limit` tokens of each code family: the ones with the smallest split keys."""
    by_family: dict[str, list[Inspection]] = {}
    for item in inspections:
        if item.status == "ok":
            by_family.setdefault(item.code_hash, []).append(item)
    chosen = []
    for members in by_family.values():
        members.sort(key=lambda i: split_key("ethereum", i.token))
        chosen.extend(members[:limit])
    return sorted(chosen, key=lambda i: (i.created, i.token))


def scan(args: argparse.Namespace) -> int:
    rules = load_rules()
    _log(f"rules sha256 {rules_hash()[:16]} (recorded: {_recorded_hash()[:16]})")
    env = dict(dotenv_values(args.env)) if args.env else dict(os.environ)
    data = LiveChain.from_env(env)
    cache = Path(args.cache)
    candidates = Table(cache / "candidates.jsonl")
    inspections = Table(cache / "inspections.jsonl")
    measurements = Table(cache / "measurements.jsonl")
    existing = _existing_hashes(cache, data)

    if not len(candidates):
        for cand in discover(data, rules):
            candidates.add({**cand.__dict__})
        _log(f"discovered {len(candidates)} tokens paired with WETH")
    if args.max_tokens and len(candidates) > args.max_tokens:
        _log(f"note: {len(candidates)} candidates, above --max-tokens {args.max_tokens}")
        return 2

    started, errors = time.monotonic(), Counter()
    todo = [c for t, c in candidates.rows.items() if t not in inspections]
    for number, row in enumerate(todo, 1):
        cand = Candidate(**row)
        try:
            inspections.add(inspect(data, rules, cand, existing).to_json())
        except ChainError as err:
            errors[err.code] += 1
        if number % 100 == 0:
            _log(
                f"inspected {number}/{len(todo)} ({time.monotonic() - started:.0f}s) {dict(errors)}"
            )
    statuses = Counter(r["status"] for r in inspections.rows.values())
    _log(f"inspection: {dict(statuses)}")

    chosen = families_to_measure(
        [Inspection(**r) for r in inspections.rows.values()], rules.max_family_scanned
    )
    todo_m = [i for i in chosen if i.token not in measurements]
    _log(f"to measure: {len(todo_m)} of {len(chosen)} (family cap {rules.max_family_scanned})")
    for number, insp in enumerate(todo_m, 1):
        try:
            measurements.add(measure(data, rules, insp).to_json())
        except ChainError as err:
            errors[err.code] += 1
        if number % 25 == 0:
            _log(
                f"measured {number}/{len(todo_m)} ({time.monotonic() - started:.0f}s) "
                f"{dict(errors)}"
            )
    _backfill_late(data, rules, inspections, measurements, errors)
    _log(f"done: {len(measurements)} measured, errors {dict(errors)}; run again to retry errors")
    return 0


def _backfill_late(data, rules, inspections: Table, measurements: Table, errors: Counter) -> None:
    """Measurements made before the late-seller condition existed get that one number added; only
    tokens with enough buyers need it."""
    todo = [
        r
        for r in measurements.rows.values()
        if r["buyers"] >= rules.min_buyers and r.get("late_sellers") is None
    ]
    _log(f"late sellers to add: {len(todo)}")
    for row in todo:
        insp = Inspection(**inspections.rows[row["token"]])
        try:
            row["late_sellers"] = late_sellers(
                data, rules, insp, [row.get("creator"), row.get("lp_owner")]
            )
            measurements.add(row)
        except ChainError as err:
            errors[err.code] += 1


def _recorded_hash() -> str:
    return HASH_PATH.read_text().strip() if HASH_PATH.exists() else "none"


def _existing_hashes(cache: Path, data: LiveChain) -> set[str]:
    table = Table(cache / "fixed_hashes.jsonl")
    for address in fixed_addresses():
        if address not in table:
            table.add({"token": address, "hash": family_key(data.code(address))})
    return {row["hash"] for row in table.rows.values()}


def verify(args: argparse.Namespace) -> int:
    """Run identify_contract on every fixed contract and incident at its pinned block and keep
    what the tool says: the traits the trajectory rules use, the proxy's implementation, the code
    family. The same tool the agent has, so the card agrees with what the agent can see."""
    import tomllib

    from chain.identify import identify_contract
    from chain.rpc import RpcClient
    from evals.collect.build import INCIDENTS
    from evals.collect.existing import EXISTING, FAMOUS, GREY

    rules = load_rules()
    env = dict(dotenv_values(args.env)) if args.env else dict(os.environ)
    data = LiveChain.from_env(env)
    famous = {c["address"] for c in FAMOUS}
    table = Table(Path(args.cache) / "fixed_facts.jsonl")
    targets = [(c["address"], c["block"]) for c in EXISTING + GREY + FAMOUS]
    if INCIDENTS.exists():
        for row in tomllib.loads(INCIDENTS.read_text()).get("incident", []):
            targets.append((row["address"].lower(), row["attack_block"] - rules.pin_before_attack))
    for address, block in targets:
        client = RpcClient.connect("ethereum", env, block=block)
        result = identify_contract(client, address)
        identity = result.data
        if not hasattr(identity, "summary"):
            table.add({"token": address, "block": block, "error": "identify failed"})
            _log(f"{address[:10]} @ {block}: identify failed")
            continue
        code = client.get_code(address)
        behind_it = identity.implementation
        implementation = behind_it.address.lower() if behind_it else None
        behind = client.get_code(implementation) if implementation else code
        applies = next(c.applies for c in identity.checks if c.name == "sell_simulation")
        traits = []
        if implementation:
            traits.append("proxy")
        if identity.summary == "official":
            traits.append("official")
        elif applies:
            traits.append("token")
        else:
            traits.append("not_a_token")
        senders = None
        if address in famous:
            sample, _ = data.logs(
                address,
                TRANSFER_TOPIC,
                block + 1,
                block + rules.famous_blocks,
                limit=rules.max_transfer_logs,
            )
            senders = len({s.topic_address(1) for s in sample} - {ZERO})
        table.add(
            {
                "token": address,
                "block": block,
                "symbol": _symbol(client, address),
                "senders": senders,
                "summary": identity.summary,
                "traits": traits,
                "implementation": implementation,
                "code_hash": family_key(behind),
                "code_size": len(code),
            }
        )
        _log(f"{address[:10]} @ {block}: {identity.summary} {traits} code {len(code)} bytes")
    return 0


def _symbol(client, address: str) -> str:
    """The token's symbol if it is short plain text, else "": a check that the address is the
    token it is meant to be. Untrusted text, kept only if it is 12 or fewer letters or digits."""
    try:
        out = client.call(address, bytes.fromhex("95d89b41"))
        if not out.ok or len(out.data) < 64:
            return ""
        size = int.from_bytes(out.data[32:64], "big")
        text = out.data[64 : 64 + size].decode("ascii", "ignore")
    except Exception:
        return ""
    return text if text.isalnum() and len(text) <= 12 else ""


def build(args: argparse.Namespace) -> int:
    from evals.collect.build import write_all
    from evals.collect.log import write_log

    rules = load_rules()
    if rules_hash() != _recorded_hash():
        _log("error: label_rules.toml changed after its hash was recorded; refusing to build")
        return 2
    result = write_all(rules, Path(args.cache))
    write_log(rules, result)
    for name in ("dev", "heldout", "disputed"):
        counts = Counter(c["klass"] for c in result[name])
        _log(f"{name}: {len(result[name])} cards {dict(sorted(counts.items()))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.collect")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("scan", help="discover, inspect and measure candidate tokens")
    one.add_argument("--env", help="a .env file with RPC_URL_ETHEREUM and ETHERSCAN_API_KEY")
    one.add_argument("--cache", default=str(CACHE))
    one.add_argument("--max-tokens", type=int, default=1400)
    one.set_defaults(run=scan)
    two = sub.add_parser("verify", help="identify_contract on the fixed contracts and incidents")
    two.add_argument("--env", help="a .env file with RPC_URL_ETHEREUM and ETHERSCAN_API_KEY")
    two.add_argument("--cache", default=str(CACHE))
    two.set_defaults(run=verify)
    three = sub.add_parser("build", help="write contracts.toml and the other dataset files")
    three.add_argument("--cache", default=str(CACHE))
    three.set_defaults(run=build)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
