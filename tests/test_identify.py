"""chain/identify.py: what a contract is, whether a protocol lists it, and which checks fit."""

import json
from pathlib import Path

import pytest
from web3 import Web3

import chain.identify as identify
from chain.dex import DEXES, UNISWAP_V3
from chain.errors import ChainError, ToolError
from chain.identify import identify_contract
from chain.kinds import KINDS, selector_of
from chain.selectors import SelectorInfo
from chain.slots import EIP1967_IMPLEMENTATION, LEGACY_OZ_IMPLEMENTATION
from tests.references_support import FIXTURES, load_fixture
from tests.world import World, address_word, args_of, word

TOKEN = "0x" + "a1" * 20
CHECKSUMMED = Web3.to_checksum_address(TOKEN)
OTHER = "0x" + "b2" * 20
PAIR = "0x" + "11" * 20
FAKE = "0x" + "22" * 20
IMPL = "0x" + "c3" * 20
PROXY = "0x" + "d4" * 20
V2_FACTORY = DEXES[1][0].factory
ERC20_CODE = load_fixture("FixtureERC20Plain").code
CLONE_CODE = bytes.fromhex(json.loads((FIXTURES / "clone_ethereum.json").read_text())["code"][2:])
CLONE_TARGET = "0xd5fe1c1f216b775dfd30638fa7164d41321ef79b"
JUNK = b"\x60\x00\x60\x00\xfd"
DELEGATING = b"\x60\x00\x60\x00\xf4"  # PUSH1 0, PUSH1 0, DELEGATECALL: enough to count as a proxy
PAIR_CODE = bytes.fromhex(
    json.loads(
        (
            Path(__file__).resolve().parent.parent / "references" / "data" / "uniswap-v2-pair.json"
        ).read_text()
    )["code"][2:]
)


def check(result, name):
    return next(c for c in result.data.checks if c.name == name)


def ask(world, address=TOKEN):
    return identify_contract(world, address)


def spec(name):
    return next(k for k in KINDS if k.name == name)


def selectors_of(*names, without=()):
    return tuple(
        SelectorInfo(selector=selector_of(s), arguments=None, state_mutability=None)
        for n in names
        for s in spec(n).signatures
        if s not in without
    )


def pair_world(claimed_factory=V2_FACTORY, listed=True):
    world = World()
    world.codes[PAIR.lower()] = PAIR_CODE
    world.on(PAIR, "getReserves()", word(5) + word(6) + word(0))
    world.on(PAIR, "token0()", address_word(TOKEN))
    world.on(PAIR, "token1()", address_word(OTHER))
    world.on(PAIR, "factory()", address_word(claimed_factory))
    expected = args_of(("address", TOKEN), ("address", OTHER))
    world.on(
        V2_FACTORY,
        "getPair(address,address)",
        lambda args: address_word(PAIR if listed and args == expected else "0x" + "00" * 20),
    )
    return world


# --- the two ways -----------------------------------------------------------------------------


def test_a_plain_token_is_recognised_by_shape_then_rehearsed_then_read():
    world = World()
    world.codes[TOKEN.lower()] = ERC20_CODE
    result = ask(world)
    data = result.data
    assert data.summary == "standard" and [k.kind for k in data.kinds] == ["erc20"]
    assert data.registry == () and data.implementation is None
    assert check(result, "sell_simulation").applies and check(result, "code_review").applies
    assert result.provenance.source.startswith("evmole@") and result.provenance.source.endswith(
        "+registries"
    )
    assert (result.provenance.block, result.provenance.address) == (100, CHECKSUMMED)


def test_an_official_pair_is_neither_rehearsed_nor_read():
    world = pair_world()
    result = ask(world, PAIR)
    data = result.data
    assert data.summary == "official"
    assert {k.kind for k in data.kinds} == {"erc20", "uniswap_v2_pair"}
    assert [(f.registry, f.status) for f in data.registry] == [("uniswap_v2", "official")]
    sell = check(result, "sell_simulation")
    assert not sell.applies and "protocol's own" in sell.reason
    assert not check(result, "code_review").applies


def test_a_lookalike_pair_is_read_but_not_rehearsed():
    world = pair_world(listed=False)
    result = ask(world, PAIR)
    assert result.data.summary == "lookalike"
    assert result.data.registry[0].status == "claims_official_but_not_listed"
    assert not check(result, "sell_simulation").applies
    assert check(result, "code_review").applies


def test_a_contract_with_no_known_shape_gets_no_rehearsal_and_goes_to_reading():
    world = World()
    world.codes[TOKEN.lower()] = JUNK
    result = ask(world)
    assert result.data.summary == "unrecognized" and result.data.kinds == ()
    sell = check(result, "sell_simulation")
    assert not sell.applies and "does not have the ERC20 interface" in sell.reason
    assert check(result, "code_review").applies
    assert any("No function selectors" in note for note in result.data.notes)


def test_the_gate_is_the_interface_not_being_a_copy_of_something_known():
    # An ERC20 with a mint and a blacklist is no clean copy of anything, and is exactly the token
    # the rehearsal is for.
    world = World()
    world.codes[TOKEN.lower()] = load_fixture("FixtureERC20MintBlacklist").code
    result = ask(world)
    assert check(result, "sell_simulation").applies


def test_no_code_means_nothing_applies():
    result = ask(World())
    assert result.data.summary == "no_code" and result.data.code_hash is None
    assert not any(c.applies for c in result.data.checks)


# --- tokens that are not what the rehearsal is for --------------------------------------------


def test_a_token_missing_a_function_is_not_rehearsed_and_the_reason_names_it(monkeypatch):
    world = World()
    world.codes[TOKEN.lower()] = JUNK
    broken = selectors_of("erc20", without=("allowance(address,address)",))
    monkeypatch.setattr(identify, "extract_selectors", lambda code: broken)
    result = ask(world)
    assert result.data.summary == "standard" and result.data.kinds == ()
    sell = check(result, "sell_simulation")
    assert not sell.applies and "allowance(address,address)" in sell.reason


def test_a_protocol_token_that_nothing_lists_is_still_not_rehearsed(monkeypatch):
    world = World()
    world.codes[TOKEN.lower()] = JUNK
    both = selectors_of("erc20", "aave_token")
    monkeypatch.setattr(identify, "extract_selectors", lambda code: both)
    result = ask(world)
    sell = check(result, "sell_simulation")
    assert not sell.applies and "aave_token" in sell.reason
    assert check(result, "code_review").applies


def test_a_token_on_a_chain_without_an_exchange_is_not_rehearsed():
    world = World(chain_id=999)
    world.codes[TOKEN.lower()] = ERC20_CODE
    result = ask(world)
    assert not check(result, "sell_simulation").applies
    assert "no exchange is configured" in check(result, "sell_simulation").reason
    assert any("No protocol registries" in note for note in result.data.notes)


# --- proxies ----------------------------------------------------------------------------------


def test_an_eip1167_clone_is_classified_by_what_it_points_at():
    world = World()
    world.codes[PROXY.lower()] = CLONE_CODE
    world.codes[CLONE_TARGET] = ERC20_CODE
    result = ask(world, PROXY)
    implementation = result.data.implementation
    assert (implementation.address.lower(), implementation.found_by) == (
        CLONE_TARGET,
        "eip1167_clone",
    )
    assert [k.kind for k in result.data.kinds] == ["erc20"]
    assert any("proxy" in note for note in result.data.notes)


@pytest.mark.parametrize(
    ("slot", "found_by"),
    [(EIP1967_IMPLEMENTATION, "eip1967_slot"), (LEGACY_OZ_IMPLEMENTATION, "legacy_oz_slot")],
)
def test_a_slot_proxy_is_classified_by_its_implementation(slot, found_by):
    world = World()
    world.codes[PROXY.lower()] = DELEGATING
    world.codes[IMPL.lower()] = ERC20_CODE
    world.storage[(PROXY.lower(), slot)] = address_word(IMPL)
    result = ask(world, PROXY)
    assert result.data.implementation.found_by == found_by
    assert [k.kind for k in result.data.kinds] == ["erc20"]
    assert check(result, "sell_simulation").applies


def test_a_slot_on_a_contract_that_cannot_delegate_is_not_trusted():
    world = World()
    world.codes[PROXY.lower()] = JUNK  # no DELEGATECALL, so the slot may be bait
    world.codes[IMPL.lower()] = ERC20_CODE
    world.storage[(PROXY.lower(), EIP1967_IMPLEMENTATION)] = address_word(IMPL)
    data = ask(world, PROXY).data
    assert data.implementation is None
    assert data.proxy_kind == "unknown-proxy-like"
    assert "erc20" not in [k.kind for k in data.kinds]


def test_a_slot_that_points_at_nothing_is_ignored():
    world = World()
    world.codes[PROXY.lower()] = DELEGATING
    world.storage[(PROXY.lower(), EIP1967_IMPLEMENTATION)] = address_word(IMPL)  # no code there
    assert ask(world, PROXY).data.implementation is None


def test_a_slot_holding_more_than_an_address_is_ignored():
    world = World()
    world.codes[PROXY.lower()] = DELEGATING
    world.codes[IMPL.lower()] = ERC20_CODE
    world.storage[(PROXY.lower(), EIP1967_IMPLEMENTATION)] = b"\x01" + address_word(IMPL)[1:]
    assert ask(world, PROXY).data.implementation is None


# --- failures ---------------------------------------------------------------------------------


def test_a_bad_address_is_a_structured_error():
    result = ask(World(), "not-an-address")
    assert isinstance(result.data, ToolError) and result.data.error.code == "invalid_address"
    assert result.provenance.address is None


def test_code_too_large_to_analyse_is_a_structured_error():
    world = World()
    world.codes[TOKEN.lower()] = b"\x00" * 100_001
    result = ask(world)
    assert result.data.error.code == "invalid_bytecode"
    assert result.provenance.address == CHECKSUMMED


def test_a_node_failure_is_a_structured_error_with_provenance():
    class Down(World):
        def get_code(self, address):
            raise ChainError("rpc_unreachable", "Could not reach the RPC node after retrying.")

    result = ask(Down())
    assert result.data.error.code == "rpc_unreachable" and result.provenance.block == 100


def test_unexpected_failures_never_leak_their_text():
    class Broken(World):
        def get_code(self, address):
            raise RuntimeError("secret https://rpc.example/KEY123")

    result = ask(Broken())
    assert result.data.error.code == "internal_error"
    assert "KEY123" not in result.model_dump_json()


def test_the_limits_always_travel_with_a_result():
    world = World()
    world.codes[TOKEN.lower()] = ERC20_CODE
    data = json.loads(ask(world).model_dump_json())["data"]
    assert "selectors" in " ".join(data["limitations"])


# --- the command line -------------------------------------------------------------------------


def test_identify_command_prints_the_result(monkeypatch, capsys):
    from chain.cli import main

    world = World()
    world.codes[TOKEN.lower()] = ERC20_CODE

    class FakeRpc:
        @staticmethod
        def connect(chain, env, block=None):
            return world

    monkeypatch.setattr("chain.cli.RpcClient", FakeRpc)
    assert main(["identify", "--address", TOKEN], env={}) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["data"]["summary"] == "standard"
    assert printed["provenance"]["block"] == 100


def test_identify_command_failures_print_a_structured_error(capsys):
    from chain.cli import main

    assert main(["identify", "--address", TOKEN, "--chain", "nowhere"], env={}) == 1
    assert json.loads(capsys.readouterr().out)["data"]["error"]["code"] == "chain_not_configured"


def test_the_v3_deployment_the_rehearsal_uses_is_the_one_the_registry_knows():
    for chain_id, deployment in UNISWAP_V3.items():
        assert deployment.wrapped_native == DEXES[chain_id][0].wrapped_native
