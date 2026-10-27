"""resolve_proxy: is this contract a proxy, and where does it send calls?

A proxy is a small contract that forwards every call, with DELEGATECALL, to another contract that
holds the logic (the implementation). What a user can do is decided by the implementation, and
who can swap the implementation decides who can change the rules later. So the question is worth
a tool of its own. One hop is followed. The kinds recognised:

- EIP-1167 clone: the target is written into the 45 bytes of code, so there is no storage trail
  and it is matched by the bytes themselves.
- EIP-1967: the implementation address sits at keccak256("eip1967.proxy.implementation") - 1.
- EIP-1967 beacon: the slot holds a BEACON, and the beacon's `implementation()` answers (one
  `eth_call`, so a beacon can swap the logic of many proxies at once).
- legacy OpenZeppelin ("zos", as in USDC): the same idea at keccak256("org.zeppelinos.proxy...").

Anything else that looks like a proxy is `unknown-proxy-like`: never reported as "not a proxy".
Each pointer slot read has one of four outcomes, kept apart on purpose:

- found: an address with code at it
- absent: the slot is empty (all zero)
- invalid: something is there but it is not a usable address
- error: we could not read it (node down, state pruned)

An error is never turned into "absent". If any read failed and nothing was found, the answer is
`undetermined`; if something was found, `partial` says a check was missed. A pointer is trusted
only if the proxy's own code contains DELEGATECALL, otherwise anyone could fake a proxy by writing
a benign contract's address into the slot. That check skips the compiler's metadata tail, whose
bytes can look like opcodes (WETH9 has a stray 0xf4 in its tail); it is evidence, not proof.
"""

import logging
from typing import Literal

from pydantic import BaseModel
from web3 import Web3

from chain.code import CodeStore, read_code
from chain.errors import ChainError
from chain.models import ToolResult, normalize_address
from chain.rpc import RpcClient
from chain.slots import (
    EIP1967_ADMIN,
    EIP1967_BEACON,
    EIP1967_IMPLEMENTATION,
    LEGACY_OZ_ADMIN,
    LEGACY_OZ_IMPLEMENTATION,
)
from chain.tooling import run_tool

log = logging.getLogger(__name__)

SOURCE = "rpc"

# EIP-1167 minimal proxy: a fixed prefix, the 20-byte implementation, a fixed suffix.
CLONE_PREFIX = bytes.fromhex("363d3d373d3d3d363d73")
CLONE_SUFFIX = bytes.fromhex("5af43d82803e903d91602b57fd5bf3")
BEACON_IMPLEMENTATION_CALL = bytes(Web3.keccak(text="implementation()")[:4])

_DELEGATECALL = 0xF4
_PUSH1, _PUSH32 = 0x60, 0x7F

LIMITATIONS = (
    "One hop only: a proxy behind a proxy shows the first implementation, not what that one "
    "forwards to.",
    "Recognised: EIP-1167 clones in their canonical 45-byte form, EIP-1967 (implementation and "
    "beacon) and legacy OpenZeppelin slots. Other clones (for example with PUSH0) and other "
    "patterns (diamonds, Aragon-style) come out as unknown-proxy-like.",
    "unknown-proxy-like means 'delegates calls and no recognised pointer was found'. It also fits "
    "contracts that use DELEGATECALL for other reasons (multicall helpers).",
    "The DELEGATECALL check reads opcodes without running the code: evidence, not proof.",
    "An empty admin slot does not mean nobody can upgrade: the admin can be fixed in the code. "
    "Who may upgrade is not decided here.",
)

ProxyKind = Literal[
    "not-a-proxy",
    "eip1967",
    "eip1967-beacon",
    "legacy-oz",
    "eip1167-clone",
    "unknown-proxy-like",
    "undetermined",
]
Outcome = Literal["found", "absent", "invalid", "error"]
FoundBy = Literal["eip1967_slot", "eip1967_beacon", "legacy_oz_slot", "eip1167_clone"]

_KIND_OF: dict[FoundBy, ProxyKind] = {
    "eip1967_slot": "eip1967",
    "eip1967_beacon": "eip1967-beacon",
    "legacy_oz_slot": "legacy-oz",
    "eip1167_clone": "eip1167-clone",
}


class Implementation(BaseModel, frozen=True):
    address: str
    found_by: FoundBy


class Check(BaseModel, frozen=True):
    name: str  # e.g. "eip1967-implementation"; "beacon-implementation" for the beacon's answer
    slot: str | None  # 0x hex; None for the beacon call
    outcome: Outcome
    address: str | None  # the address found, when the outcome is found
    detail: str | None  # why it is invalid or failed; written by us


class ProxyData(BaseModel, frozen=True):
    kind: ProxyKind
    status: Literal["ok", "partial", "undetermined"]
    implementation: Implementation | None
    beacon: str | None  # the beacon's address, for a beacon proxy
    admin: str | None  # the admin slot's address, if set (informational)
    delegatecall_in_code: bool
    checks: tuple[Check, ...]
    notes: tuple[str, ...]
    limitations: tuple[str, ...] = LIMITATIONS


# -- reading the code ----------------------------------------------------------------------------


def clone_target(code: bytes) -> str | None:
    """The implementation of a canonical EIP-1167 clone, or None if the code is not one."""
    if (
        len(code) == len(CLONE_PREFIX) + 20 + len(CLONE_SUFFIX)
        and code.startswith(CLONE_PREFIX)
        and code.endswith(CLONE_SUFFIX)
    ):
        return normalize_address("0x" + code[len(CLONE_PREFIX) :][:20].hex())
    return None


def _executable_end(code: bytes) -> int:
    """Where the code proper ends: before the compiler's CBOR metadata, whose last two bytes give
    its length. Without a plausible metadata block the whole code counts."""
    if len(code) >= 2:
        length = int.from_bytes(code[-2:], "big")
        start = len(code) - 2 - length
        # A CBOR map starts with a byte in 0xa0..0xb7; solc and vyper both end with one.
        if length > 0 and start > 0 and 0xA0 <= code[start] <= 0xB7:
            return start
    return len(code)


def has_delegatecall(code: bytes) -> bool:
    """Is there a DELEGATECALL opcode in the code proper? Skips PUSH data, so a 0xf4 inside a
    constant does not count, and stops before the metadata tail."""
    end, i = _executable_end(code), 0
    while i < end:
        op = code[i]
        if op == _DELEGATECALL:
            return True
        i += op - _PUSH1 + 2 if _PUSH1 <= op <= _PUSH32 else 1
    return False


# -- reading the pointers ------------------------------------------------------------------------


def _address_in(word: bytes) -> str | None:
    if len(word) != 32 or word[:12] != bytes(12) or word[12:] == bytes(20):
        return None
    return normalize_address("0x" + word[12:].hex())


def _fail(name: str, slot: int | None, err: ChainError) -> Check:
    return Check(
        name=name,
        slot=hex(slot) if slot is not None else None,
        outcome="error",
        address=None,
        detail=err.message,
    )


def _classify_word(name: str, slot: int, word: bytes) -> tuple[Check, str | None]:
    """The check for a slot value, and the address in it if it is an address."""
    if not any(word):
        return Check(name=name, slot=hex(slot), outcome="absent", address=None, detail=None), None
    target = _address_in(word)
    if target is None:
        detail = "The slot holds a value that is not shaped like an address."
        return (
            Check(name=name, slot=hex(slot), outcome="invalid", address=None, detail=detail),
            None,
        )
    return Check(name=name, slot=hex(slot), outcome="found", address=target, detail=None), target


def _read_pointer(
    client: RpcClient, address: str, name: str, slot: int, store: CodeStore | None
) -> tuple[Check, str | None]:
    """Read a slot that should hold the address of a contract. Found only if code is there."""
    try:
        check, target = _classify_word(name, slot, client.get_storage_at(address, slot))
        if target is not None and not read_code(client, target, store).code:
            return (
                Check(
                    name=name,
                    slot=hex(slot),
                    outcome="invalid",
                    address=None,
                    detail=f"The slot holds {target}, which has no code at this block.",
                ),
                None,
            )
    except ChainError as err:
        return _fail(name, slot, err), None
    return check, target


def _read_admin(client: RpcClient, address: str, name: str, slot: int) -> tuple[Check, str | None]:
    try:
        return _classify_word(name, slot, client.get_storage_at(address, slot))
    except ChainError as err:
        return _fail(name, slot, err), None


def _beacon_implementation(
    client: RpcClient, beacon: str, store: CodeStore | None
) -> tuple[Check, str | None]:
    name = "beacon-implementation"
    try:
        answer = client.call(beacon, BEACON_IMPLEMENTATION_CALL)
        target = _address_in(answer.data) if answer.ok else None
        if target is None:
            detail = (
                "The beacon reverted when asked for implementation()."
                if not answer.ok
                else "The beacon's implementation() did not return an address."
            )
            return Check(name=name, slot=None, outcome="invalid", address=None, detail=detail), None
        if not read_code(client, target, store).code:
            detail = f"The beacon answered {target}, which has no code at this block."
            return Check(name=name, slot=None, outcome="invalid", address=None, detail=detail), None
    except ChainError as err:
        return _fail(name, None, err), None
    return Check(name=name, slot=None, outcome="found", address=target, detail=None), target


def trace_proxy(
    client: RpcClient, address: str, code: bytes, store: CodeStore | None = None
) -> ProxyData:
    """The logic of the tool, for callers that already have the code (`identify`). Raises nothing
    for a failed read: that becomes an `error` check."""
    if not code:
        return ProxyData(
            kind="not-a-proxy",
            status="ok",
            implementation=None,
            beacon=None,
            admin=None,
            delegatecall_in_code=False,
            checks=(),
            notes=("There is no code at this address, so there is nothing to forward calls.",),
        )
    delegates = has_delegatecall(code)
    target = clone_target(code)
    if target is not None:
        return ProxyData(
            kind="eip1167-clone",
            status="ok",
            implementation=Implementation(address=target, found_by="eip1167_clone"),
            beacon=None,
            admin=None,
            delegatecall_in_code=True,
            checks=(),
            notes=("An EIP-1167 clone: the target is fixed in its code and cannot be changed.",),
        )

    checks: list[Check] = []
    notes: list[str] = []
    found: list[tuple[FoundBy, str, str | None]] = []  # (how, implementation, beacon)

    check, impl = _read_pointer(
        client, address, "eip1967-implementation", EIP1967_IMPLEMENTATION, store
    )
    checks.append(check)
    if impl is not None:
        found.append(("eip1967_slot", impl, None))

    check, beacon = _read_pointer(client, address, "eip1967-beacon", EIP1967_BEACON, store)
    checks.append(check)
    if beacon is not None:
        beacon_check, via_beacon = _beacon_implementation(client, beacon, store)
        checks.append(beacon_check)
        if via_beacon is not None:
            found.append(("eip1967_beacon", via_beacon, beacon))

    check, legacy = _read_pointer(
        client, address, "legacy-oz-implementation", LEGACY_OZ_IMPLEMENTATION, store
    )
    checks.append(check)
    if legacy is not None:
        found.append(("legacy_oz_slot", legacy, None))

    if len(found) > 1:
        notes.append(
            "More than one pointer is set (" + ", ".join(f[0] for f in found) + "); the first "
            "listed is reported, the rest are in the checks."
        )

    admin: str | None = None
    if found:
        how = found[0][0]
        name, slot = (
            ("legacy-oz-admin", LEGACY_OZ_ADMIN)
            if how == "legacy_oz_slot"
            else ("eip1967-admin", EIP1967_ADMIN)
        )
        check, admin = _read_admin(client, address, name, slot)
        checks.append(check)
        if check.outcome == "absent":
            notes.append(
                "The admin slot is empty. That does not mean nobody can upgrade: the admin can "
                "be fixed in the code."
            )

    errored = any(c.outcome == "error" for c in checks)
    invalid = any(c.outcome == "invalid" for c in checks)
    if errored:
        notes.append(
            "Some reads failed (see the checks), so a pointer may have been missed. "
            "A failed read is not evidence that the slot is empty."
        )

    def done(
        kind: ProxyKind, implementation: Implementation | None, beacon: str | None
    ) -> ProxyData:
        status = "undetermined" if kind == "undetermined" else "partial" if errored else "ok"
        return ProxyData(
            kind=kind,
            status=status,
            implementation=implementation,
            beacon=beacon,
            admin=admin,
            delegatecall_in_code=delegates,
            checks=tuple(checks),
            notes=tuple(notes),
        )

    if found and delegates:
        how, impl_address, beacon_address = found[0]
        return done(
            _KIND_OF[how], Implementation(address=impl_address, found_by=how), beacon_address
        )
    if found:
        notes.append(
            "A pointer slot is set but the code has no DELEGATECALL, so calls are probably not "
            "forwarded there and the pointer is not trusted."
        )
        return done("unknown-proxy-like", None, None)
    if delegates or invalid:
        notes.append(
            "The code delegates calls, or a pointer slot holds something unusable, but no "
            "recognised pointer was found. It may be a proxy of a kind not handled here."
        )
        return done("unknown-proxy-like", None, None)
    if errored:
        return done("undetermined", None, None)
    return done("not-a-proxy", None, None)


# -- the tool ------------------------------------------------------------------------------------


def resolve_proxy(
    client: RpcClient, address: str, store: CodeStore | None = None
) -> ToolResult[ProxyData]:
    """The tool: read the code, then follow one hop."""

    def work(checked: str) -> ProxyData:
        return trace_proxy(client, checked, read_code(client, checked, store).code, store)

    return run_tool(ToolResult[ProxyData], client, address, SOURCE, work)
