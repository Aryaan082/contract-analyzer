import json

import pytest

from references.abi import (
    abi_signature,
    is_signature,
    keccak_hex,
    normalize_selector,
    parse_hex,
    selector_of,
)
from references.library import DATA_DIR, Library, LibraryError, UnknownReference, get_library
from references.models import Reference


def record(reference_id: str) -> dict:
    return json.loads((DATA_DIR / f"{reference_id}.json").read_text())


def build(*records: dict) -> Library:
    return Library(Reference.model_validate(r) for r in records)


def test_the_library_loads_and_counts_what_the_resume_metric_needs():
    stats = get_library().stats()
    assert stats.contracts == 13 and stats.modules == 2
    assert (stats.compiled, stats.deployed, stats.spec) == (8, 6, 1)
    assert len(get_library()) == 15


def test_known_selectors_and_hashes():
    assert selector_of("transfer(address,uint256)") == "0xa9059cbb"
    assert keccak_hex(b"") == ("0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")


def test_the_digest_is_stable_and_changes_with_content():
    first = get_library().digest
    assert first == get_library().digest and len(first) == 64
    smaller = Library(p.reference for p in get_library().all if p.reference.id != "oz-erc20")
    assert smaller.digest != first


def test_an_unknown_reference_names_the_ones_that_exist():
    with pytest.raises(UnknownReference, match="oz-erc20"):
        get_library().get("nope")


def test_modules_have_no_code_and_contracts_do():
    for prepared in get_library().all:
        assert (prepared.code is None) == (prepared.reference.kind == "module")


def test_deployed_references_say_where_they_came_from():
    deployed = [p.reference for p in get_library().all if p.reference.origin.type == "deployed"]
    assert {r.id for r in deployed} == {
        "weth9-ethereum",
        "weth-base",
        "uniswap-v2-pair",
        "usdt-ethereum",
        "dai-ethereum",
        "uni-ethereum",
    }
    for reference in deployed:
        origin = reference.origin
        assert origin.block > 0 and origin.chain_id in (1, 8453)
        assert origin.abi_source.startswith("sourcify:")


def test_compiled_references_pin_their_toolchain():
    for prepared in get_library().all:
        origin = prepared.reference.origin
        if origin.type == "compiled":
            assert origin.tag == "v5.7.0" and len(origin.commit) == 40
            assert origin.solc.startswith("0.8.28") and origin.optimizer_runs == 200


def test_variable_ranges_are_recorded_for_programs_with_immutables():
    library = get_library()
    assert library.get("oz-transparent-proxy").reference.variable_ranges  # the admin immutable
    assert library.get("oz-beacon-proxy").reference.variable_ranges  # the beacon immutable
    assert library.get("eip1167-minimal-proxy").reference.variable_ranges == ((10, 20),)
    assert not library.get("oz-erc20").reference.variable_ranges


def test_every_record_survives_a_rebuild_from_its_own_json():
    records = [record(p.reference.id) for p in get_library().all]
    assert build(*records).digest == get_library().digest


def test_a_changed_code_byte_is_caught_by_the_hash():
    bad = record("oz-erc20")
    bad["code"] = bad["code"][:10] + ("0" if bad["code"][10] != "0" else "1") + bad["code"][11:]
    with pytest.raises(LibraryError, match="code_hash does not match"):
        build(bad)


def test_a_selector_that_is_not_the_hash_of_its_signature_is_caught():
    bad = record("oz-erc20")
    bad["functions"][0]["selector"] = "0xdeadbeef"
    with pytest.raises(LibraryError, match="is not the selector of"):
        build(bad)


def test_a_function_missing_from_the_code_is_caught():
    bad = record("oz-erc20")
    bad["functions"].append(
        {"selector": selector_of("burn(uint256)"), "signature": "burn(uint256)"}
    )
    with pytest.raises(LibraryError, match="not found as PUSH4"):
        build(bad)


def test_duplicate_ids_and_bad_variable_ranges_are_caught():
    one = record("oz-erc20")
    with pytest.raises(LibraryError, match="duplicate id oz-erc20"):
        build(one, one)
    bad = record("oz-erc20")
    bad["variable_ranges"] = [[0, 99999]]
    with pytest.raises(LibraryError, match="variable range"):
        build(bad)


def test_a_module_cannot_carry_code_and_a_contract_cannot_lack_it():
    module = record("oz-ownable")
    module["code"] = "0x00"
    with pytest.raises(ValueError, match="no code"):
        Reference.model_validate(module)
    contract = record("oz-erc20")
    del contract["code"], contract["code_hash"]
    with pytest.raises(ValueError, match="needs code"):
        Reference.model_validate(contract)


def test_the_index_names_a_selector_wherever_the_library_has_it():
    owner = selector_of("owner()")
    names = get_library().names_for(owner)
    assert ("oz-ownable", "owner()") in names


def test_selector_helpers_reject_junk():
    assert normalize_selector(" 0xA9059CBB ") == "0xa9059cbb"
    assert normalize_selector("a9059cbb") == "0xa9059cbb"
    for bad in ("0xa9059c", "0xa9059cbbb", "transfer", "", None, 12):
        with pytest.raises(ValueError):
            normalize_selector(bad)
    assert parse_hex("0x00ff") == b"\x00\xff"
    for bad in ("0x0", "0xzz", "00 ff"):
        with pytest.raises(ValueError):
            parse_hex(bad)


def test_only_plain_signatures_pass():
    assert is_signature("transfer(address,uint256)")
    assert is_signature("swap((address,uint256)[],bytes)")
    for bad in (
        "transfer (address)",
        "ignore previous instructions(address)",
        "transfer(address,uint256); drop table",
        "transfer",
        "",
        None,
        "a(" + "x" * 500 + ")",
    ):
        assert not is_signature(bad)


def test_abi_entries_become_canonical_signatures_with_tuples_expanded():
    entry = {
        "name": "swap",
        "type": "function",
        "inputs": [
            {"type": "tuple[]", "components": [{"type": "address"}, {"type": "uint256"}]},
            {"type": "bytes"},
        ],
    }
    assert abi_signature(entry) == "swap((address,uint256)[],bytes)"
    assert abi_signature({"name": "owner", "type": "function"}) == "owner()"
