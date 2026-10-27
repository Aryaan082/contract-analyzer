"""The Component 1 tools against the real chains and services. Skipped unless you ask for `-m live`.

    uv run pytest -m live tests/test_chain_live.py

Needs RPC_URL_ETHEREUM and RPC_URL_BASE, and for the explorer parts ETHERSCAN_API_KEY, in `.env` or
the environment. Read-only. The contracts are the inspector's examples (chain/inspector.html); their
code sizes and hashes were read with Foundry's `cast`, a separate implementation from web3.py.
Upgradeable proxies change their implementation, so those are checked by kind, not by address.
"""

import os

import pytest

from chain.code import get_code
from chain.config import load_env
from chain.db import PostgresCodeStore, PostgresSelectorStore
from chain.errors import ToolError
from chain.explorer import ExplorerClient, get_deployment, get_verified_source
from chain.profile import Services, build_profile
from chain.proxy import resolve_proxy
from chain.rpc import RpcClient
from chain.signatures import lookup_selector
from chain.storage import get_storage
from tests.chain_support import throwaway_database

pytestmark = pytest.mark.live

WETH9 = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
CLONE = "0x30b044c3568F61d85289c7E44c455712ba3c5a89"
BEACON_PROXY = "0x0d54D4279B9E8c54cD8547c2C75A8Ee81A0BcaE8"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# chain, address, code size, keccak256, proxy kind, how the implementation was found
CASES = {
    "weth9": (
        "ethereum",
        WETH9,
        3124,
        "0xd0a06b12ac47863b5c7be4185c2deaad1c61557033f56c7d4ea74429cbb25e23",
        "not-a-proxy",
        None,
    ),
    "no code": (
        "ethereum",
        "0x0000000000000000000000000000000000000000",
        0,
        "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        "not-a-proxy",
        None,
    ),
    "base weth": (
        "base",
        "0x4200000000000000000000000000000000000006",
        2041,
        "0x8a3a1f6a9f9dce633117adee5b458245835a8645a8c8726a26382a4622508b1c",
        "not-a-proxy",
        None,
    ),
    "compound usdc": (
        "ethereum",
        "0xc3d688B66703497DAA19211EEdff47f25384cdc3",
        1878,
        "0x64952234eab8f3aed74355c49119f627965640b1efeb6b28430b5a31b0d3b192",
        "eip1967",
        "eip1967_slot",
    ),
    "aave v3 pool": (
        "ethereum",
        "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        2400,
        "0x96107dc4006b4c7fecd1827cfb275ffeef31e6194cd50466f85f8eb24ccf2679",
        "eip1967",
        "eip1967_slot",
    ),
    "eip1167 clone": (
        "ethereum",
        CLONE,
        45,
        "0xd37d7cb1b00b1a35c54303484441c301235a1b48e7da36cf2df888ee40df1d1f",
        "eip1167-clone",
        "eip1167_clone",
    ),
    "beacon proxy": (
        "ethereum",
        BEACON_PROXY,
        824,
        "0x9806c8207a455c012b2799be651ac0146d54866f92db90b502e5e2efa283bee9",
        "eip1967-beacon",
        "eip1967_beacon",
    ),
    "usdc": (
        "ethereum",
        USDC,
        2186,
        "0xd80d4b7c890cb9d6a4893e6b52bc34b56b25335cb13716e0d1d31383e6b41505",
        "legacy-oz",
        "legacy_oz_slot",
    ),
    "steth": (
        "ethereum",
        "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84",
        1035,
        "0xb9c1c929064cd21734c102a698e68bf617feefcfa5a9f62407c45401546736bf",
        "unknown-proxy-like",
        None,
    ),
}


@pytest.fixture(scope="module")
def env():
    load_env()
    if not os.environ.get("RPC_URL_ETHEREUM"):
        pytest.skip("needs RPC_URL_ETHEREUM")
    return os.environ


def connect(env, chain, block=None):
    if chain == "base" and not env.get("RPC_URL_BASE"):
        pytest.skip("needs RPC_URL_BASE")
    return RpcClient.connect(chain, env, block)


@pytest.fixture(scope="module")
def explorer(env):
    api = ExplorerClient.from_env(env)
    if api is None:
        pytest.skip("needs ETHERSCAN_API_KEY")
    return api


@pytest.mark.parametrize("name", CASES)
def test_get_code_matches_the_independently_read_size_and_hash(env, name):
    chain, address, size, code_hash, *_ = CASES[name]
    data = get_code(connect(env, chain), address).data
    assert (data.size, data.code_hash, data.has_code) == (size, code_hash, size > 0)


@pytest.mark.parametrize("name", CASES)
def test_resolve_proxy_classifies_the_known_contracts(env, name):
    chain, address, _, _, kind, found_by = CASES[name]
    data = resolve_proxy(connect(env, chain), address).data
    assert (data.kind, data.status) == (kind, "ok")
    assert (data.implementation.found_by if data.implementation else None) == found_by


def test_the_known_proxies_agree_with_the_inspectors_notes(env):
    eth = connect(env, "ethereum")
    compound = resolve_proxy(eth, CASES["compound usdc"][1]).data
    assert compound.admin is not None  # the transparent proxy's admin is set
    aave = resolve_proxy(eth, CASES["aave v3 pool"][1]).data
    assert aave.admin is None and any("does not mean nobody can upgrade" in n for n in aave.notes)
    beacon = resolve_proxy(eth, BEACON_PROXY).data
    assert beacon.beacon == "0x985462C9aA4D6c3Ad59Ae6e1e9c0C11347ED1598"
    assert beacon.implementation.address != beacon.beacon


def test_weth9_has_no_false_delegatecall_from_its_metadata_tail(env):
    assert not resolve_proxy(connect(env, "ethereum"), WETH9).data.delegatecall_in_code


def test_storage_slot_zero_of_weth9_is_the_token_name(env):
    value = get_storage(connect(env, "ethereum"), WETH9, 0).data.value
    assert bytes.fromhex(value[2:]).startswith(b"Wrapped Ether")


def test_the_code_store_answers_a_settled_block_without_the_node(env):
    with throwaway_database() as db:
        store = PostgresCodeStore(db)
        first = get_code(connect(env, "ethereum", block=20_000_000), WETH9, store).data
        second = get_code(connect(env, "ethereum", block=20_000_000), WETH9, store).data
        assert not first.from_store and second.from_store
        assert first.code_hash == second.code_hash == CASES["weth9"][3]


def test_lookup_selector_names_the_common_functions_and_flags_what_it_should():
    result = lookup_selector(["0xa9059cbb", "0x70a08231", "0xabcdef01"])
    entries = {e.selector: e for e in result.data.entries}
    assert entries["0xa9059cbb"].name == "transfer(address,uint256)"
    assert entries["0x70a08231"].name == "balanceOf(address)"
    assert entries["0xabcdef01"].status == "unknown_selector"
    assert entries["0xa9059cbb"].alternatives_hidden > 0  # transfer's selector has junk collisions


def test_lookup_selector_uses_postgres_the_second_time(env):
    with throwaway_database() as db:
        store = PostgresSelectorStore(db)
        first = lookup_selector(["0xa9059cbb", "0x70a08231"], store=store)
        second = lookup_selector(["0xa9059cbb", "0x70a08231"], store=store)
        assert (first.data.asked_database, first.data.from_cache) == (2, 0)
        assert (second.data.asked_database, second.data.from_cache) == (0, 2)
        assert [e.name for e in second.data.entries] == [e.name for e in first.data.entries]


def test_weth9_is_verified_and_its_deployer_is_known(env, explorer):
    eth = connect(env, "ethereum")
    source = get_verified_source(eth, WETH9, explorer).data
    assert source.verified and source.contract_name == "WETH9" and source.file_count == 1
    deployment = get_deployment(eth, WETH9, explorer).data
    assert deployment.found and deployment.creation_block == 4719568
    assert deployment.deployer == "0x4F26FfBe5F04ED43630fdC30A87638d53D0b0876"
    assert not deployment.created_by_contract


def test_a_factory_made_clone_names_its_factory(env, explorer):
    deployment = get_deployment(connect(env, "ethereum"), CLONE, explorer).data
    assert deployment.found and deployment.created_by_contract


def test_an_address_with_no_contract_has_no_creation_record(env, explorer):
    eth = connect(env, "ethereum")
    assert not get_deployment(
        eth, "0x0000000000000000000000000000000000000001", explorer
    ).data.found
    assert not get_verified_source(
        eth, "0x0000000000000000000000000000000000000001", explorer
    ).data.verified


def test_the_explorer_on_base_answers_or_says_plainly_why_not(env, explorer):
    base = connect(env, "base")
    for tool in (get_verified_source, get_deployment):
        result = tool(base, "0x4200000000000000000000000000000000000006", explorer)
        if isinstance(result.data, ToolError):
            assert result.data.error.code in ("explorer_error", "explorer_rate_limited")
            assert "etherscan" not in result.data.error.message.lower()


@pytest.mark.parametrize("name", ["eip1167 clone", "beacon proxy", "usdc", "steth", "weth9"])
def test_profile_runs_every_tool_on_the_known_contracts(env, explorer, name):
    chain, address, size, code_hash, kind, _ = CASES[name]
    with throwaway_database() as db:
        services = Services(
            code_store=PostgresCodeStore(db),
            selector_store=PostgresSelectorStore(db),
            explorer=explorer,
        )
        profile = build_profile(connect(env, chain), address, services)
    assert profile.code.data.code_hash == code_hash
    assert profile.proxy.data.kind == kind
    assert profile.selectors is not None and profile.signatures is not None
    assert profile.verified_source is not None and profile.deployment is not None
    if kind in ("eip1167-clone", "eip1967-beacon", "legacy-oz"):
        assert profile.implementation_selectors is not None
    assert profile.block == profile.code.provenance.block
