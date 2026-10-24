import pytest

from references.bytecode import (
    disassemble,
    push4_values,
    push_constants,
    scan_opcodes,
    split_metadata,
)
from references.library import get_library
from tests.references_support import solidity_tail

STOP = b"\x00"
DELEGATECALL = b"\xf4"
SELFDESTRUCT = b"\xff"


def test_push_data_is_not_read_as_instructions():
    code = bytes([0x61, 0xF4, 0xFF]) + DELEGATECALL  # PUSH2 0xf4ff, then a real DELEGATECALL
    steps = [(i.offset, i.opcode, i.operand) for i in disassemble(code)]
    assert steps == [(0, 0x61, b"\xf4\xff"), (3, 0xF4, b"")]


def test_push0_has_no_operand():
    steps = [(i.offset, i.opcode) for i in disassemble(bytes([0x5F, 0x5F, 0x60, 0x01, 0x00]))]
    assert steps == [(0, 0x5F), (1, 0x5F), (2, 0x60), (4, 0x00)]


def test_a_push_cut_off_by_the_end_of_the_code_is_reported():
    code = STOP + bytes([0x62, 0xAA])  # PUSH3 with one operand byte present
    assert scan_opcodes(code).truncated_push is True
    assert scan_opcodes(STOP + bytes([0x60, 0xAA])).truncated_push is False


def test_scan_finds_real_dangerous_opcodes_with_offsets():
    code = STOP + DELEGATECALL + STOP + SELFDESTRUCT + DELEGATECALL
    scan = scan_opcodes(code)
    assert (scan.delegatecall_count, scan.delegatecall_offsets) == (2, (1, 4))
    assert (scan.selfdestruct_count, scan.selfdestruct_offsets) == (1, (3,))
    assert scan.metadata_skipped is False


def test_scan_skips_push_data():
    code = bytes([0x7F]) + bytes([0xF4, 0xFF] * 16) + STOP  # PUSH32 full of 0xf4 and 0xff
    scan = scan_opcodes(code)
    assert (scan.delegatecall_count, scan.selfdestruct_count) == (0, 0)


def test_scan_skips_the_metadata_tail():
    # The WETH9 false positive Component 1 found: a plain linear scan of the whole code sees a
    # DELEGATECALL byte in the hash inside the compiler's metadata tail.
    code = STOP + solidity_tail()
    assert DELEGATECALL in code and SELFDESTRUCT in code  # the bytes are there...
    scan = scan_opcodes(code)
    assert (scan.delegatecall_count, scan.selfdestruct_count) == (0, 0)  # ...but not counted
    assert scan.metadata_skipped is True
    assert scan.bytes_scanned == 1


def test_scan_of_real_weth9_has_no_delegatecall():
    code = get_library().get("weth9-ethereum").code
    assert code.count(0xF4) >= 1  # a naive byte count would flag it
    assert scan_opcodes(code).delegatecall_count == 0


def test_scan_of_a_real_proxy_finds_its_delegatecall():
    assert scan_opcodes(get_library().get("oz-erc1967-proxy").code).delegatecall_count == 1


def test_metadata_is_read_from_real_contracts():
    library = get_library()
    assert split_metadata(library.get("oz-erc20").code)[1].solc == "0.8.28"
    old = split_metadata(library.get("weth9-ethereum").code)[1]
    assert old.keys == ("bzzr0",) and old.solc is None  # solc 0.4.19 wrote no version
    modern = split_metadata(library.get("uni-ethereum").code)[1]
    assert modern.solc == "0.5.16" and "experimental" in modern.keys


def test_split_returns_the_body_without_the_tail():
    body, metadata = split_metadata(STOP + STOP + solidity_tail())
    assert body == STOP + STOP
    assert metadata.length == 41 + 2


@pytest.mark.parametrize(
    "code",
    [
        b"",
        b"\x00\x01",
        STOP * 10 + b"\x00\x00",  # length field 0
        STOP * 10 + b"\xff\xff",  # length larger than the code
        STOP * 10 + b"\x00\x09",  # length points at bytes that are not CBOR
        bytes(range(1, 40)) + b"\x00\x08",  # random bytes, plausible length
    ],
)
def test_code_without_a_valid_tail_is_left_whole(code):
    assert split_metadata(code) == (code, None)


def test_a_cbor_map_with_no_known_key_is_not_a_tail():
    cbor = b"\xa1\x63foo\x41\x01"  # {"foo": h'01'}
    code = STOP * 4 + cbor + len(cbor).to_bytes(2, "big")
    assert split_metadata(code) == (code, None)


def test_a_cbor_map_with_leftover_bytes_is_not_a_tail():
    cbor = b"\xa1\x64solc\x43\x00\x08\x1c" + b"\x00"  # one byte more than the map uses
    code = STOP * 4 + cbor + len(cbor).to_bytes(2, "big")
    assert split_metadata(code) == (code, None)


def test_push_constants_keep_long_operands_only():
    code = bytes([0x60, 0x80]) + bytes([0x63, 1, 2, 3, 4]) + bytes([0x65, 1, 2, 3, 4, 5, 6])
    assert push_constants(code) == {bytes([1, 2, 3, 4]), bytes([1, 2, 3, 4, 5, 6])}


def test_push_constants_can_exclude_values_and_skip_variable_ranges():
    code = bytes([0x63, 1, 2, 3, 4]) + bytes([0x64, 9, 9, 9, 9, 9])
    assert push_constants(code, exclude=frozenset({bytes([1, 2, 3, 4])})) == {bytes([9] * 5)}
    assert push_constants(code, skip_ranges=[(7, 2)]) == {bytes([1, 2, 3, 4])}  # touches offset 7
    assert push_constants(code, skip_ranges=[(100, 5)]) == push_constants(code)


def test_push4_values_are_formatted_like_selectors():
    assert push4_values(bytes([0x63, 0xA9, 0x05, 0x9C, 0xBB, 0x00])) == {"0xa9059cbb"}
