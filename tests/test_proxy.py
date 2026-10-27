"""chain/proxy.py: resolve_proxy and the logic under it."""

import json
from pathlib import Path

import pytest
from web3 import Web3

from chain.errors import ChainError, ToolError
from chain.proxy import (
    BEACON_IMPLEMENTATION_CALL,
    clone_target,
    has_delegatecall,
    resolve_proxy,
    trace_proxy,
)
from chain.rpc import CallOutcome
from chain.slots import (
    EIP1967_ADMIN,
    EIP1967_BEACON,
    EIP1967_IMPLEMENTATION,
    LEGACY_OZ_ADMIN,
    LEGACY_OZ_IMPLEMENTATION,
)
from tests.world import World, address_word

DATA = Path(__file__).resolve().parent.parent / "references" / "data"
PROXY = "0x" + "d4" * 20
IMPL = "0x" + "c3" * 20
BEACON = "0x" + "e5" * 20
ADMIN = "0x" + "f6" * 20
LOGIC = bytes.fromhex("6001600055")
DELEGATING = bytes.fromhex("60006000f4")  # PUSH1 0, PUSH1 0, DELEGATECALL


def fixture_code(name) -> bytes:
    return bytes.fromhex(json.loads((DATA / f"{name}.json").read_text())["code"][2:])


def world(code=DELEGATING):
    w = World()
    w.codes[PROXY] = code
    w.codes[IMPL] = LOGIC
    return w


def set_slot(w, slot, address, owner=PROXY):
    w.storage[(owner, slot)] = address_word(address)


def trace(w, code=DELEGATING):
    return trace_proxy(w, Web3.to_checksum_address(PROXY), code)


# -- the DELEGATECALL scan ---------------------------------------------------------------------


def test_real_proxies_delegate_and_real_tokens_do_not():
    for name in ("oz-erc1967-proxy", "oz-transparent-proxy", "oz-beacon-proxy"):
        assert has_delegatecall(fixture_code(name)), name
    # WETH9 has a stray 0xf4 inside the compiler's metadata tail; a naive scan would count it.
    for name in ("weth9-ethereum", "weth-base", "dai-ethereum", "usdt-ethereum", "oz-erc20"):
        assert not has_delegatecall(fixture_code(name)), name


def test_a_0xf4_inside_push_data_is_not_an_opcode():
    assert not has_delegatecall(bytes.fromhex("60f400"))  # PUSH1 0xf4, STOP
    assert has_delegatecall(bytes.fromhex("6001f4"))  # PUSH1 1, DELEGATECALL


def test_the_metadata_tail_is_skipped_but_only_when_it_looks_like_metadata():
    cbor = bytes.fromhex("a1f4")  # a one-entry CBOR map holding a 0xf4 byte
    tail = b"\x00" + cbor + len(cbor).to_bytes(2, "big")
    assert not has_delegatecall(b"\x00" + tail)  # the 0xf4 is in the tail
    # The same bytes at the end of code with no CBOR map header are code, so they count.
    assert has_delegatecall(b"\x00\x00\xf4\x00\x01")


def test_empty_and_tiny_code_do_not_crash_the_scan():
    assert not has_delegatecall(b"")
    assert not has_delegatecall(b"\x00")
    assert not has_delegatecall(b"\x7f")  # a PUSH32 cut short


def test_clone_target_is_read_from_the_canonical_45_bytes_only():
    clone = bytes.fromhex(
        "363d3d373d3d3d363d73"
        + "d5fe1c1f216b775dfd30638fa7164d41321ef79b"
        + "5af43d82803e903d91602b57fd5bf3"
    )
    assert clone_target(clone) == "0xd5fE1C1F216B775dFd30638fA7164D41321ef79B"
    assert clone_target(clone + b"\x00") is None
    assert clone_target(b"\x00" + clone[1:]) is None


# -- kinds --------------------------------------------------------------------------------------


def test_eip1967_proxy_with_an_admin():
    w = world()
    set_slot(w, EIP1967_IMPLEMENTATION, IMPL)
    set_slot(w, EIP1967_ADMIN, ADMIN)
    data = trace(w)
    assert (data.kind, data.status) == ("eip1967", "ok")
    assert data.implementation.address == Web3.to_checksum_address(IMPL)
    assert data.implementation.found_by == "eip1967_slot"
    assert data.admin == Web3.to_checksum_address(ADMIN)


def test_an_empty_admin_slot_is_noted_not_taken_to_mean_nobody_can_upgrade():
    w = world()
    set_slot(w, EIP1967_IMPLEMENTATION, IMPL)
    data = trace(w)
    assert data.admin is None
    assert any("does not mean nobody can upgrade" in n for n in data.notes)


def test_legacy_openzeppelin_proxy():
    w = world()
    set_slot(w, LEGACY_OZ_IMPLEMENTATION, IMPL)
    set_slot(w, LEGACY_OZ_ADMIN, ADMIN)
    data = trace(w)
    assert (data.kind, data.implementation.found_by) == ("legacy-oz", "legacy_oz_slot")
    assert data.admin == Web3.to_checksum_address(ADMIN)


def test_eip1167_clone_needs_no_storage_at_all():
    clone = bytes.fromhex("363d3d373d3d3d363d73" + IMPL[2:] + "5af43d82803e903d91602b57fd5bf3")
    data = trace_proxy(World(), PROXY, clone)
    assert (data.kind, data.status) == ("eip1167-clone", "ok")
    assert data.implementation.found_by == "eip1167_clone"
    assert data.checks == ()


def beacon_world(answer):
    w = world()
    set_slot(w, EIP1967_BEACON, BEACON)
    w.codes[BEACON] = LOGIC
    w.on(BEACON, "implementation()", answer)
    return w


def test_beacon_proxy_asks_the_beacon_for_the_implementation():
    w = beacon_world(address_word(IMPL))
    data = trace(w)
    assert (data.kind, data.status) == ("eip1967-beacon", "ok")
    assert data.implementation.address == Web3.to_checksum_address(IMPL)
    assert data.implementation.found_by == "eip1967_beacon"
    assert data.beacon == Web3.to_checksum_address(BEACON)
    assert w.calls == [(Web3.to_checksum_address(BEACON), BEACON_IMPLEMENTATION_CALL)]


@pytest.mark.parametrize(
    "answer",
    [None, bytes(32), b"\x01" + bytes(31), b""],
    ids=["reverts", "zero", "not-address", "empty"],
)
def test_a_beacon_that_gives_no_usable_answer_is_invalid_not_a_proxy(answer):
    data = trace(beacon_world(answer))
    assert data.kind == "unknown-proxy-like"  # still delegates, and a beacon is set
    assert data.implementation is None
    assert next(c for c in data.checks if c.name == "beacon-implementation").outcome == "invalid"


def test_a_beacon_answering_with_an_address_that_has_no_code_is_invalid():
    w = beacon_world(address_word("0x" + "99" * 20))
    data = trace(w)
    assert data.implementation is None
    assert "no code" in next(c for c in data.checks if c.name == "beacon-implementation").detail


def test_nothing_recognised_and_no_delegatecall_is_not_a_proxy_and_says_it_was_checked():
    data = trace(world(LOGIC), LOGIC)
    assert (data.kind, data.status, data.delegatecall_in_code) == ("not-a-proxy", "ok", False)
    assert {c.outcome for c in data.checks} == {"absent"}
    assert len(data.checks) == 3


def test_delegatecall_without_a_recognised_pointer_is_unknown_proxy_like_never_not_a_proxy():
    data = trace(world())
    assert (data.kind, data.status) == ("unknown-proxy-like", "ok")
    assert data.implementation is None


def test_a_pointer_slot_on_a_contract_that_cannot_delegate_is_not_trusted():
    w = world(LOGIC)
    set_slot(w, EIP1967_IMPLEMENTATION, IMPL)  # could be bait to borrow the implementation's look
    data = trace(w, LOGIC)
    assert data.kind == "unknown-proxy-like" and data.implementation is None
    assert any("not trusted" in n for n in data.notes)
    # the evidence is still there for anyone who wants it
    assert next(c for c in data.checks if c.name == "eip1967-implementation").outcome == "found"


def test_a_pointer_slot_holding_junk_is_invalid_and_makes_it_proxy_like():
    w = world(LOGIC)
    w.storage[(PROXY, EIP1967_IMPLEMENTATION)] = b"\x01" + address_word(IMPL)[1:]
    data = trace(w, LOGIC)
    assert data.kind == "unknown-proxy-like"
    assert next(c for c in data.checks if c.name == "eip1967-implementation").outcome == "invalid"


def test_a_pointer_to_an_address_with_no_code_is_invalid():
    w = world()
    set_slot(w, EIP1967_IMPLEMENTATION, "0x" + "99" * 20)
    data = trace(w)
    check = next(c for c in data.checks if c.name == "eip1967-implementation")
    assert check.outcome == "invalid" and "no code" in check.detail
    assert data.kind == "unknown-proxy-like"


def test_more_than_one_pointer_reports_the_first_and_says_so():
    w = world()
    other = "0x" + "77" * 20
    w.codes[other] = LOGIC
    set_slot(w, EIP1967_IMPLEMENTATION, IMPL)
    set_slot(w, LEGACY_OZ_IMPLEMENTATION, other)
    data = trace(w)
    assert data.implementation.address == Web3.to_checksum_address(IMPL)
    assert any("More than one pointer" in n for n in data.notes)


# -- failures are never folded into "not a proxy" --------------------------------------------


class Flaky(World):
    """Fails reading chosen slots."""

    def __init__(self, failing):
        super().__init__()
        self.failing = failing

    def get_storage_at(self, address, slot):
        if slot in self.failing:
            raise ChainError("rpc_unreachable", "Could not reach the RPC node after retrying.")
        return super().get_storage_at(address, slot)


def test_a_failed_read_with_nothing_found_is_undetermined_not_not_a_proxy():
    w = Flaky({EIP1967_IMPLEMENTATION})
    data = trace(w, LOGIC)
    assert (data.kind, data.status) == ("undetermined", "undetermined")
    failed = next(c for c in data.checks if c.name == "eip1967-implementation")
    assert failed.outcome == "error" and failed.detail
    assert any("not evidence that the slot is empty" in n for n in data.notes)


def test_a_failed_read_next_to_a_find_is_partial():
    w = Flaky({EIP1967_BEACON})
    w.codes[IMPL] = LOGIC
    set_slot(w, EIP1967_IMPLEMENTATION, IMPL)
    data = trace(w)
    assert (data.kind, data.status) == ("eip1967", "partial")


def test_a_failed_read_on_code_that_delegates_is_proxy_like_and_partial():
    data = trace(Flaky({LEGACY_OZ_IMPLEMENTATION}))
    assert (data.kind, data.status) == ("unknown-proxy-like", "partial")


def test_a_failure_asking_the_beacon_is_an_error_not_an_invalid():
    class Down(World):
        def call(self, to, data):
            raise ChainError("rpc_error", "The RPC node returned an error.")

    w = Down()
    w.codes[PROXY], w.codes[BEACON] = DELEGATING, LOGIC
    set_slot(w, EIP1967_BEACON, BEACON)
    data = trace(w)
    assert next(c for c in data.checks if c.name == "beacon-implementation").outcome == "error"
    assert data.status == "partial"


def test_a_failure_reading_the_target_code_is_an_error_not_an_invalid():
    class NoCode(World):
        def get_code(self, address):
            if address.lower() == IMPL:
                raise ChainError("rpc_unreachable", "Could not reach the RPC node after retrying.")
            return super().get_code(address)

    w = NoCode()
    w.codes[PROXY] = DELEGATING
    set_slot(w, EIP1967_IMPLEMENTATION, IMPL)
    data = trace(w)
    assert next(c for c in data.checks if c.name == "eip1967-implementation").outcome == "error"


def test_no_code_at_the_address_is_not_a_proxy_and_reads_nothing():
    w = World()
    data = trace(w, b"")
    assert data.kind == "not-a-proxy" and data.checks == ()


# -- the tool -----------------------------------------------------------------------------------


def test_the_tool_reads_the_code_itself_and_wraps_the_answer():
    w = world()
    set_slot(w, EIP1967_IMPLEMENTATION, IMPL)
    result = resolve_proxy(w, PROXY)
    assert result.data.kind == "eip1967"
    assert result.provenance.address == Web3.to_checksum_address(PROXY)
    assert (result.provenance.chain, result.provenance.block) == ("ethereum", 100)


def test_the_tool_turns_failures_into_structured_errors():
    assert resolve_proxy(World(), "nope").data.error.code == "invalid_address"

    class Down(World):
        def get_code(self, address):
            raise ChainError("rpc_unreachable", "Could not reach the RPC node after retrying.")

    result = resolve_proxy(Down(), PROXY)
    assert isinstance(result.data, ToolError) and result.data.error.code == "rpc_unreachable"


def test_a_call_outcome_that_reverts_with_data_is_still_just_invalid():
    class Reverting(World):
        def call(self, to, data):
            return CallOutcome(False, bytes.fromhex("08c379a0") + b"attacker text")

    w = Reverting()
    w.codes[PROXY], w.codes[BEACON] = DELEGATING, LOGIC
    set_slot(w, EIP1967_BEACON, BEACON)
    check = next(c for c in trace(w).checks if c.name == "beacon-implementation")
    assert check.outcome == "invalid" and "attacker" not in check.detail
