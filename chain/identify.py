"""identify: what kind of contract is this, is it a protocol's own, and which checks fit it?

The first of the two ways the agent examines a contract:
1. Recognise it with tools. Read the code, work out which standard interfaces it speaks (chain/
   kinds.py), and ask the protocols' own registries whether they list it (chain/registry.py).
2. Read it. Whatever is not an official deployment is read for risky functions and known
   vulnerability patterns by the other components.

A behavioural rehearsal runs only where its kind of contract fits: today the sell rehearsal, and
only for something shaped like a token. The gate is the interface, not "is it a copy of a known
contract": a honeypot is almost never a clean copy of anything, it is an ordinary token with
something added, so a gate on copies would skip exactly the tokens the rehearsal exists to catch.
An unrecognised contract gets no rehearsal and goes straight to reading.
"""

import logging
from importlib.metadata import version
from typing import Literal

from pydantic import BaseModel
from web3 import Web3

from chain.dex import DEXES
from chain.errors import ChainError
from chain.kinds import KindMatch, match_kinds
from chain.models import ToolResult, normalize_address
from chain.registry import REGISTRIES, Finding, check_registries
from chain.rpc import RpcClient
from chain.selectors import extract_selectors, parse_code
from chain.slots import EIP1967_IMPLEMENTATION, LEGACY_OZ_IMPLEMENTATION

log = logging.getLogger(__name__)

# EIP-1167 minimal proxy: a fixed prefix, the 20-byte implementation, a fixed suffix.
_CLONE_PREFIX = bytes.fromhex("363d3d373d3d3d363d73")
_CLONE_SUFFIX = bytes.fromhex("5af43d82803e903d91602b57fd5bf3")

# Interfaces that look like tokens but are a protocol's own bookkeeping, not something traded.
PROTOCOL_TOKEN_KINDS = frozenset(
    {"uniswap_v2_pair", "aave_token", "compound_v2_ctoken", "compound_v3_comet", "erc4626"}
)

LIMITATIONS = (
    "Kinds come from function selectors, which show the interface and say nothing about what the "
    "functions do. A copied interface is not a copied contract.",
    "Only the registries in chain/registry.py are asked (Uniswap V2 and V3, Aave V3, Compound V2 "
    "and V3, on the chains listed there). Not being listed there means 'not known to these', not "
    "'bad'.",
    "Proxies are followed one step (EIP-1967 and legacy OpenZeppelin slots, EIP-1167 clones). "
    "Beacon proxies and nested proxies are not.",
)

Summary = Literal["no_code", "official", "lookalike", "standard", "unrecognized"]


class Implementation(BaseModel, frozen=True):
    address: str
    found_by: Literal["eip1967_slot", "legacy_oz_slot", "eip1167_clone"]


class Check(BaseModel, frozen=True):
    name: Literal["sell_simulation", "code_review"]
    applies: bool
    reason: str  # written here


class Identity(BaseModel, frozen=True):
    summary: Summary
    code_hash: str | None
    code_size: int
    selector_count: int
    implementation: Implementation | None
    kinds: tuple[KindMatch, ...]  # complete matches
    near_misses: tuple[KindMatch, ...]
    registry: tuple[Finding, ...]
    checks: tuple[Check, ...]
    notes: tuple[str, ...]
    limitations: tuple[str, ...] = LIMITATIONS


def _address_in_slot(word: bytes) -> str | None:
    if len(word) != 32 or word[:12] != bytes(12) or word[12:] == bytes(20):
        return None
    return normalize_address("0x" + word[12:].hex())


def _implementation(client: RpcClient, address: str, code: bytes) -> Implementation | None:
    """One step of proxy resolution, enough to classify what sits behind a proxy. chain slice 4's
    resolve_proxy is the full version."""
    if (
        len(code) == len(_CLONE_PREFIX) + 20 + len(_CLONE_SUFFIX)
        and code.startswith(_CLONE_PREFIX)
        and code.endswith(_CLONE_SUFFIX)
    ):
        target = normalize_address("0x" + code[len(_CLONE_PREFIX) :][:20].hex())
        return Implementation(address=target, found_by="eip1167_clone")
    for slot, found_by in (
        (EIP1967_IMPLEMENTATION, "eip1967_slot"),
        (LEGACY_OZ_IMPLEMENTATION, "legacy_oz_slot"),
    ):
        target = _address_in_slot(client.get_storage_at(address, slot))
        if target is not None and client.get_code(target):
            return Implementation(address=target, found_by=found_by)
    return None


def _summary(
    kinds: tuple[KindMatch, ...], findings: tuple[Finding, ...], near: tuple[KindMatch, ...]
) -> Summary:
    statuses = {f.status for f in findings}
    if "claims_official_but_not_listed" in statuses:
        return "lookalike"
    if "official" in statuses:
        return "official"
    if kinds or near or findings:
        return "standard"
    return "unrecognized"


def _sell_check(
    summary: Summary, kinds: tuple[KindMatch, ...], near: tuple[KindMatch, ...], chain_id: int
) -> Check:
    names = {k.kind for k in kinds}
    if summary in ("official", "lookalike", "no_code"):
        why = {
            "official": "It is a protocol's own contract, not a token to trade.",
            "lookalike": "It imitates a protocol contract; a rehearsal would say nothing useful.",
            "no_code": "There is no contract here.",
        }[summary]
        return Check(name="sell_simulation", applies=False, reason=why)
    if "erc20" not in names:
        missing = next((n for n in near if n.kind == "erc20"), None)
        if missing:
            return Check(
                name="sell_simulation",
                applies=False,
                reason="It has most of the ERC20 functions but not all (missing "
                + ", ".join(missing.missing)
                + "), so a DEX could not trade it as it stands.",
            )
        return Check(
            name="sell_simulation",
            applies=False,
            reason="It does not have the ERC20 interface, so there is nothing to buy and sell.",
        )
    protocol = sorted(names & PROTOCOL_TOKEN_KINDS)
    if protocol:
        return Check(
            name="sell_simulation",
            applies=False,
            reason="It has the ERC20 interface but is a protocol's own token "
            f"({', '.join(protocol)}), not one traded on an exchange.",
        )
    if chain_id not in DEXES:
        return Check(
            name="sell_simulation",
            applies=False,
            reason="It is shaped like a token, but no exchange is configured for this chain.",
        )
    return Check(
        name="sell_simulation",
        applies=True,
        reason="It has the ERC20 interface and is not a pool, vault or lending token, so a "
        "buy-then-sell rehearsal is meaningful.",
    )


def _review_check(summary: Summary) -> Check:
    if summary == "official":
        return Check(
            name="code_review",
            applies=False,
            reason="The protocol's own record lists this address, so it is the protocol's "
            "deployed code, not an unknown contract to read.",
        )
    if summary == "no_code":
        return Check(name="code_review", applies=False, reason="There is no contract here.")
    return Check(
        name="code_review",
        applies=True,
        reason="It is not an official deployment, so its code is read for risky functions and "
        "known vulnerability patterns.",
    )


def _identify(client: RpcClient, address: str) -> Identity:
    client.pin()
    chain_id = client.chain_id
    code = client.get_code(address)
    if not code:
        summary: Summary = "no_code"
        return Identity(
            summary=summary,
            code_hash=None,
            code_size=0,
            selector_count=0,
            implementation=None,
            kinds=(),
            near_misses=(),
            registry=(),
            checks=(_sell_check(summary, (), (), chain_id), _review_check(summary)),
            notes=(
                "No code at this address: an ordinary account, or a contract that was destroyed.",
            ),
        )
    raw = parse_code(code)
    selectors = {s.selector for s in extract_selectors(raw)}
    implementation = _implementation(client, address, raw)
    notes = []
    if implementation is not None:
        behind = client.get_code(implementation.address)
        selectors |= {s.selector for s in extract_selectors(parse_code(behind))}
        notes.append(
            "It is a proxy. Its kinds include the functions of the implementation it points to."
        )
    elif not selectors:
        notes.append(
            "No function selectors were found. It may be a proxy of a kind not resolved here, or "
            "code with an unusual dispatcher."
        )
    if chain_id not in REGISTRIES:
        notes.append(f"No protocol registries are configured for chain id {chain_id}.")

    kinds, near = match_kinds(selectors)
    findings = tuple(check_registries(client, address))
    summary = _summary(kinds, findings, near)
    return Identity(
        summary=summary,
        code_hash=Web3.to_hex(Web3.keccak(raw)),
        code_size=len(raw),
        selector_count=len(selectors),
        implementation=implementation,
        kinds=kinds,
        near_misses=near,
        registry=findings,
        checks=(_sell_check(summary, kinds, near, chain_id), _review_check(summary)),
        notes=tuple(notes),
    )


def identify_contract(client: RpcClient, address: str) -> ToolResult[Identity]:
    """The tool. Provenance names the chain, the address and the block everything was read at."""
    shown = None
    try:
        shown = normalize_address(address)
        data = _identify(client, shown)
    except ChainError as err:
        return ToolResult[Identity](
            provenance=client.provenance(shown, source=_source()), data=err.to_tool_error()
        )
    except Exception:
        log.exception("identify failed")
        error = ChainError("internal_error", "Unexpected internal error; see the log.")
        return ToolResult[Identity](
            provenance=client.provenance(shown, source=_source()), data=error.to_tool_error()
        )
    return ToolResult[Identity](provenance=client.provenance(shown, source=_source()), data=data)


def _source() -> str:
    return f"evmole@{version('evmole')}+registries"
