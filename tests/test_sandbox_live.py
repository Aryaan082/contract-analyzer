"""The real sandbox: Foundry forking a live chain. Skipped unless you ask for `-m live`.

    uv run pytest -m live tests/test_sandbox_live.py

Needs Foundry and an RPC URL for Ethereum (RPC_URL_ETHEREUM in `.env` or the environment). Nothing
is sent anywhere: Foundry runs the rehearsal on its own copy of the chain.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from chain.config import load_env
from chain.rpc import RpcClient
from chain.sandbox import SANDBOX_DIR, forge_binary
from chain.simulate import simulate_sell

pytestmark = pytest.mark.live

UNI = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"
STA = "0xa7DE087329BFcda5639247F96140f9DAbe3DeED1"  # takes 1% of every transfer
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
LINK = "0x514910771AF9Ca656af840dff83E8264EcF986CA"
CBBTC = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"  # on Base


@pytest.fixture(scope="module")
def env():
    load_env()
    if not forge_binary() or not os.environ.get("RPC_URL_ETHEREUM"):
        pytest.skip("needs Foundry and RPC_URL_ETHEREUM")
    return os.environ


def test_the_solidity_fixtures_pass_in_every_mode(env):
    """Live pool, empty pool, no pool, a taxed sell and a refused sell on V2, and three V3 cases
    (a real token, a plain fixture, a token the V3 pool refuses), against fixture tokens."""
    done = subprocess.run(
        [forge_binary(), "test", "--match-contract", "FixturesTest"],
        cwd=SANDBOX_DIR,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(Path.home()),
            "FOUNDRY_ETH_RPC_URL": env["RPC_URL_ETHEREUM"],
        },
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, "fixture tests failed"
    assert "9 passed" in done.stdout or "9 tests passed" in done.stdout


@pytest.mark.parametrize("token", [UNI, DAI])
def test_ordinary_tokens_are_sellable_for_just_the_pool_fees(env, token):
    data = simulate_sell(RpcClient.connect("ethereum", env), token).data
    assert (data.verdict, data.pool.source) == ("sellable", "existing")
    assert (data.buy_tax_bps, data.sell_tax_bps) == (0, 0)
    assert 55 <= data.round_trip_loss_bps <= 65  # two 0.3% pool fees


def test_a_one_percent_transfer_burn_is_measured_as_one_percent(env):
    data = simulate_sell(RpcClient.connect("ethereum", env), STA).data
    assert data.verdict == "sellable"
    assert (data.buy_tax_bps, data.sell_tax_bps) == (100, 100)


def test_a_token_whose_deepest_pool_is_v3_is_traded_there_for_just_the_fee_tier(env):
    data = simulate_sell(RpcClient.connect("ethereum", env), LINK).data
    assert (data.verdict, data.pool.venue, data.pool.source) == ("sellable", "v3", "existing")
    assert (data.buy_tax_bps, data.sell_tax_bps) == (0, 0)
    assert data.round_trip_loss_bps == 2 * data.pool.fee_tier // 100  # in and out, one tier each


def test_v3_also_works_on_base(env):
    if not os.environ.get("RPC_URL_BASE"):
        pytest.skip("needs RPC_URL_BASE")
    data = simulate_sell(RpcClient.connect("base", env), CBBTC).data
    assert (data.verdict, data.pool.venue) == ("sellable", "v3")


def test_the_sandbox_leaves_no_files_behind(env):
    simulate_sell(RpcClient.connect("ethereum", env), UNI)
    assert [p.name for p in (SANDBOX_DIR / "results").iterdir() if p.suffix == ".json"] == []
    assert shutil.which("forge") or forge_binary()
