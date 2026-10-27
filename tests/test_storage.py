"""chain/storage.py: get_storage."""

import pytest
from web3 import Web3

from chain.errors import ChainError, ToolError
from chain.slots import EIP1967_ADMIN
from chain.storage import get_storage, parse_slot, resolve_slot
from tests.world import World, address_word, word

ADDR = "0x" + "a1" * 20
OWNER = "0x" + "b2" * 20


def test_a_set_slot_reports_its_bytes_and_the_address_it_could_be():
    w = World()
    w.storage[(ADDR, 0)] = address_word(OWNER)
    result = get_storage(w, ADDR, 0)
    assert result.data.value == "0x" + "00" * 12 + "b2" * 20
    assert result.data.as_address == Web3.to_checksum_address(OWNER)
    assert not result.data.is_zero and result.data.slot == "0x0"


def test_an_unset_slot_is_all_zero_and_not_an_address():
    data = get_storage(World(), ADDR, 5).data
    assert data.is_zero and data.as_address is None and data.value == "0x" + "00" * 32


def test_a_number_is_not_mistaken_for_an_address_when_it_does_not_fit_one():
    w = World()
    w.storage[(ADDR, 1)] = b"\x01" + bytes(31)
    assert get_storage(w, ADDR, 1).data.as_address is None


def test_a_slot_can_be_given_by_preset_name():
    w = World()
    w.storage[(ADDR, EIP1967_ADMIN)] = address_word(OWNER)
    data = get_storage(w, ADDR, "eip1967-admin").data
    assert data.preset == "eip1967-admin" and data.slot == hex(EIP1967_ADMIN)
    assert data.as_address == Web3.to_checksum_address(OWNER)


@pytest.mark.parametrize(("raw", "slot"), [(7, 7), ("7", 7), ("0x10", 16), (" 0X10 ", 16)])
def test_slots_may_be_numbers_or_text(raw, slot):
    assert parse_slot(raw) == slot
    assert resolve_slot(raw) == (None, slot)


@pytest.mark.parametrize("raw", [-1, 2**256, "", "nope", "1.5", True, None, 1.5])
def test_bad_slots_are_rejected(raw):
    with pytest.raises(ChainError) as caught:
        parse_slot(raw)
    assert caught.value.code == "invalid_slot"


def test_a_bad_slot_is_a_structured_error_with_provenance():
    result = get_storage(World(), ADDR, "nope")
    assert isinstance(result.data, ToolError) and result.data.error.code == "invalid_slot"
    assert result.provenance.address == Web3.to_checksum_address(ADDR)


def test_a_bad_address_is_a_structured_error():
    assert get_storage(World(), "nope", 0).data.error.code == "invalid_address"


def test_a_node_failure_is_a_structured_error():
    class Down(World):
        def get_storage_at(self, address, slot):
            raise ChainError("state_unavailable", "The RPC node no longer has state.")

    assert get_storage(Down(), ADDR, 0).data.error.code == "state_unavailable"


def test_word_helper_matches_what_the_tool_returns():
    w = World()
    w.storage[(ADDR, 2)] = word(255)
    assert get_storage(w, ADDR, 2).data.value.endswith("ff")


def test_an_unexpected_failure_is_an_internal_error_and_the_log_is_scrubbed(caplog):
    class Broken(World):
        def get_storage_at(self, address, slot):
            raise RuntimeError("boom at https://node.invalid/SECRETKEY")

    with caplog.at_level("WARNING", logger="chain.tooling"):
        result = get_storage(Broken(), ADDR, 0)
    assert result.data.error.code == "internal_error"
    assert "SECRETKEY" not in caplog.text and "boom" in caplog.text
