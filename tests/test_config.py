import pytest

from chain.config import chain_url, configured_chains
from chain.errors import ChainError


def test_collects_rpc_lines_lowercased_and_sorted():
    env = {"RPC_URL_BASE": "http://b", "RPC_URL_ETHEREUM": "http://e", "HOME": "/x"}
    assert configured_chains(env) == {"base": "http://b", "ethereum": "http://e"}


def test_ignores_blank_values_and_bare_prefix():
    env = {"RPC_URL_BASE": "  ", "RPC_URL_": "http://x", "RPC_URL_OPTIMISM": " http://o "}
    assert configured_chains(env) == {"optimism": "http://o"}


def test_chain_url_ignores_case_and_whitespace():
    assert chain_url(" Base ", {"RPC_URL_BASE": "http://b"}) == "http://b"


def test_unconfigured_chain_names_what_is_configured_but_no_urls():
    env = {"RPC_URL_ETHEREUM": "http://eth.invalid/SECRETKEY"}
    with pytest.raises(ChainError) as caught:
        chain_url("base", env)
    assert caught.value.code == "chain_not_configured"
    assert "ethereum" in caught.value.message
    assert "SECRETKEY" not in caught.value.message
