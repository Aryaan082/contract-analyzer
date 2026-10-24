"""identify against the real chains. Skipped unless you ask for `-m live`.

    uv run pytest -m live tests/test_identify_live.py

Needs RPC_URL_ETHEREUM (and RPC_URL_BASE for the Base case) in `.env` or the environment. Read-only.
"""

import os

import pytest

from chain.config import load_env
from chain.identify import identify_contract
from chain.rpc import RpcClient

pytestmark = pytest.mark.live

OFFICIAL = {
    "uniswap v2 pair": ("ethereum", "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc", "uniswap_v2"),
    "uniswap v3 pool": ("ethereum", "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640", "uniswap_v3"),
    "aave v3 pool": ("ethereum", "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2", "aave_v3"),
    "aave v3 aToken": ("ethereum", "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c", "aave_v3"),
    "compound v2 cToken": ("ethereum", "0x39AA39c021dfbaE8faC545936693aC917d5E7563", "compound_v2"),
    "compound v3 market": ("ethereum", "0xc3d688B66703497DAA19211EEdff47f25384cdc3", "compound_v3"),
    "base aave v3 pool": ("base", "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5", "aave_v3"),
    "base compound v3 market": (
        "base",
        "0xb125E6687d4313864e53df431d5425969c15Eb2F",
        "compound_v3",
    ),
}
UNI = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # behind a legacy OpenZeppelin proxy


@pytest.fixture(scope="module")
def env():
    load_env()
    if not os.environ.get("RPC_URL_ETHEREUM"):
        pytest.skip("needs RPC_URL_ETHEREUM")
    return os.environ


@pytest.mark.parametrize("label", OFFICIAL)
def test_each_protocols_own_contracts_are_listed_by_that_protocol(env, label):
    chain, address, registry = OFFICIAL[label]
    if chain == "base" and not os.environ.get("RPC_URL_BASE"):
        pytest.skip("needs RPC_URL_BASE")
    result = identify_contract(RpcClient.connect(chain, env), address)
    assert result.data.summary == "official", result.data.registry
    assert {f.registry for f in result.data.registry} == {registry}
    assert not any(c.applies for c in result.data.checks)  # not rehearsed, not read


def test_an_ordinary_token_is_rehearsed_and_read(env):
    result = identify_contract(RpcClient.connect("ethereum", env), UNI)
    assert result.data.summary == "standard"
    assert all(c.applies for c in result.data.checks)


def test_a_token_behind_a_proxy_is_classified_by_its_implementation(env):
    result = identify_contract(RpcClient.connect("ethereum", env), USDC)
    assert result.data.implementation is not None
    assert "erc20" in {k.kind for k in result.data.kinds}


def test_an_ordinary_account_is_not_a_contract(env):
    dead = "0x000000000000000000000000000000000000dEaD"
    assert identify_contract(RpcClient.connect("ethereum", env), dead).data.summary == "no_code"
