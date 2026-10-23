import pytest
from pydantic import BaseModel, ValidationError
from web3 import Web3

from chain.errors import ChainError
from chain.models import Address, Provenance, ToolResult, normalize_address

LOWER = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
CHECKSUMMED = Web3.to_checksum_address(LOWER)


class CodeInfo(BaseModel):
    code_hash: str
    size: int


def test_lowercase_address_is_checksummed():
    assert normalize_address(LOWER) == CHECKSUMMED
    assert CHECKSUMMED != LOWER


def test_all_uppercase_carries_no_checksum_and_is_accepted():
    assert normalize_address("0x" + LOWER[2:].upper()) == CHECKSUMMED


def test_correct_checksum_is_accepted_unchanged():
    assert normalize_address(CHECKSUMMED) == CHECKSUMMED


def wrong_checksum() -> str:
    flipped = [c.swapcase() if c.isalpha() else c for c in CHECKSUMMED[2:]]
    return "0x" + "".join(flipped)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0x1234",
        "vitalik.eth",
        "abcdefabcdefabcdefabcdefabcdefabcdefabcd",  # no 0x prefix
        "0x" + "zz" * 20,
        LOWER + "00",
        wrong_checksum(),
        None,
        12345,
    ],
)
def test_bad_addresses_raise_invalid_address(raw):
    with pytest.raises(ChainError) as caught:
        normalize_address(raw)
    assert caught.value.code == "invalid_address"


def test_address_type_validates_inside_a_pydantic_model():
    class Input(BaseModel):
        address: Address

    assert Input(address=LOWER).address == CHECKSUMMED
    with pytest.raises(ValidationError):
        Input(address="nope")


def test_success_and_failure_share_one_envelope():
    prov = Provenance(chain="ethereum", chain_id=1, address=CHECKSUMMED, block=100, source="rpc")
    success = ToolResult[CodeInfo](provenance=prov, data=CodeInfo(code_hash="0x01", size=3))
    failure = ToolResult[CodeInfo](
        provenance=prov,
        data=ChainError("state_unavailable", "pruned").to_tool_error(),
    )

    assert success.model_dump()["data"] == {"code_hash": "0x01", "size": 3}
    assert failure.model_dump() == {
        "provenance": {
            "chain": "ethereum",
            "chain_id": 1,
            "address": CHECKSUMMED,
            "block": 100,
            "source": "rpc",
        },
        "data": {"error": {"code": "state_unavailable", "message": "pruned"}},
    }
    # Parsing the JSON back picks the right shape for each.
    assert ToolResult[CodeInfo].model_validate_json(success.model_dump_json()) == success
    assert ToolResult[CodeInfo].model_validate_json(failure.model_dump_json()) == failure


def test_provenance_fields_never_obtained_are_null():
    prov = Provenance(chain="base", chain_id=None, address=None, block=None, source="rpc")
    assert prov.model_dump()["chain_id"] is None
    assert prov.model_dump()["block"] is None
