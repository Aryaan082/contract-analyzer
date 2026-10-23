from web3 import Web3

from chain import slots


def keccak_int(text: str) -> int:
    return int.from_bytes(Web3.keccak(text=text), "big")


def test_eip1967_slots_are_keccak_of_the_name_minus_one():
    assert keccak_int("eip1967.proxy.implementation") - 1 == slots.EIP1967_IMPLEMENTATION
    assert keccak_int("eip1967.proxy.admin") - 1 == slots.EIP1967_ADMIN
    assert keccak_int("eip1967.proxy.beacon") - 1 == slots.EIP1967_BEACON


def test_legacy_openzeppelin_slots_are_keccak_of_the_name():
    assert keccak_int("org.zeppelinos.proxy.implementation") == slots.LEGACY_OZ_IMPLEMENTATION
    assert keccak_int("org.zeppelinos.proxy.admin") == slots.LEGACY_OZ_ADMIN


def test_the_implementation_slot_matches_the_value_published_in_the_eip():
    # EIP-1967 text: 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc
    assert hex(slots.EIP1967_IMPLEMENTATION) == (
        "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
    )


def test_presets_cover_every_constant():
    assert set(slots.PRESETS) == {
        "eip1967-implementation",
        "eip1967-admin",
        "eip1967-beacon",
        "legacy-oz-implementation",
        "legacy-oz-admin",
    }
