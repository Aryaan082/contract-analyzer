"""chain/code.py: get_code and the code store."""

import pytest
from web3 import Web3

from chain.code import FINALITY_DEPTH, code_hash_of, get_code, read_code
from chain.errors import ChainError, ToolError
from tests.chain_support import MemoryCodeStore
from tests.world import World

ADDR = "0x" + "a1" * 20
CHECKSUMMED = Web3.to_checksum_address(ADDR)
CODE = bytes.fromhex("6001600055")
# keccak256 of the empty string, as printed by Foundry's `cast keccak 0x`.
EMPTY_HASH = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def world(code=CODE, head=None):
    w = World()
    w.codes[ADDR] = code
    w.head = w.block + FINALITY_DEPTH if head is None else head
    return w


def test_the_hash_is_keccak256_of_the_bytes():
    assert code_hash_of(b"") == EMPTY_HASH
    assert code_hash_of(CODE) == Web3.to_hex(Web3.keccak(CODE))


def test_the_tool_reports_hash_size_and_leaves_the_bytes_out_unless_asked():
    result = get_code(world(), ADDR)
    assert result.data.has_code and result.data.size == 5
    assert result.data.code_hash == code_hash_of(CODE)
    assert result.data.code is None
    assert get_code(world(), ADDR, include_code=True).data.code == "0x6001600055"


def test_provenance_names_the_chain_the_address_and_the_block():
    p = get_code(world(), ADDR).provenance
    assert (p.chain, p.chain_id, p.address, p.block, p.source) == (
        "ethereum",
        1,
        CHECKSUMMED,
        100,
        "rpc",
    )


def test_no_code_is_a_result_with_the_hash_of_nothing():
    data = get_code(World(), ADDR).data
    assert not data.has_code and data.size == 0 and data.code_hash == EMPTY_HASH


def test_a_bad_address_is_a_structured_error():
    result = get_code(World(), "nope")
    assert isinstance(result.data, ToolError) and result.data.error.code == "invalid_address"


def test_a_node_failure_is_a_structured_error_with_provenance():
    class Down(World):
        def get_code(self, address):
            raise ChainError("rpc_unreachable", "Could not reach the RPC node after retrying.")

    result = get_code(Down(), ADDR)
    assert result.data.error.code == "rpc_unreachable"
    assert result.provenance.address == CHECKSUMMED


# -- the store ----------------------------------------------------------------------------------


def test_every_read_is_saved_with_where_it_was_seen():
    store = MemoryCodeStore()
    read = read_code(world(), ADDR, store)
    assert not read.from_store
    assert store.saves == [(1, CHECKSUMMED, 100, code_hash_of(CODE), CODE)]


def test_a_settled_block_is_answered_from_the_store_without_asking_the_node():
    store = MemoryCodeStore()
    read_code(world(), ADDR, store)

    class NoNode(World):
        def get_code(self, address):
            raise AssertionError("the node must not be asked")

    second = NoNode()
    second.head = second.block + FINALITY_DEPTH
    read = read_code(second, ADDR, store)
    assert read.from_store and read.code == CODE


def test_a_block_too_close_to_the_head_is_always_read_from_the_node():
    store = MemoryCodeStore()
    read_code(world(head=100 + FINALITY_DEPTH - 1), ADDR, store)
    again = read_code(world(head=100 + FINALITY_DEPTH - 1), ADDR, store)
    assert not again.from_store
    assert len(store.saves) == 2  # still recorded each time


def test_latest_is_never_taken_from_the_store():
    store = MemoryCodeStore()
    read_code(world(head=100), ADDR, store)
    assert not read_code(world(head=100), ADDR, store).from_store
    assert store.finds == []  # not even asked


def test_stored_code_that_does_not_match_its_hash_is_ignored():
    store = MemoryCodeStore()
    store.rows[1, ADDR, 100] = (code_hash_of(CODE), b"\x00corrupt")
    read = read_code(world(), ADDR, store)
    assert not read.from_store and read.code == CODE
    assert store.rows[1, ADDR, 100] == (code_hash_of(CODE), CODE)  # the record is repaired


def test_a_destroyed_contract_is_saved_too():
    store = MemoryCodeStore()
    read_code(World(), ADDR, store)
    assert store.saves[0][3] == EMPTY_HASH


def test_clones_share_one_hash():
    a, b = "0x" + "a1" * 20, "0x" + "b2" * 20
    w = World()
    w.codes[a] = w.codes[b] = CODE
    assert read_code(w, a).code_hash == read_code(w, b).code_hash


@pytest.mark.parametrize("flag", [True, False])
def test_from_store_is_reported(flag):
    store = MemoryCodeStore()
    w = world()
    if flag:
        read_code(w, ADDR, store)
    assert get_code(w, ADDR, store).data.from_store is flag
