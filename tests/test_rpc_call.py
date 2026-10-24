"""The RPC client's read-only `call` and `block_info`, and what it keeps of the node's URL."""

import pytest
from web3 import Web3

from chain.errors import ChainError
from chain.rpc import RpcClient
from tests.replay import ReplayProvider, ok

CHAIN_ID = ok("eth_chainId", [], "0x1")
HEAD_100 = ok("eth_blockNumber", [], "0x64")
TARGET = "0x2222222222222222222222222222222222222222"


def client(interactions):
    provider = ReplayProvider([CHAIN_ID, HEAD_100, *interactions])
    return RpcClient("ethereum", Web3(provider)), provider


def reply(result=None, error=None):
    return {"result": result} if error is None else {"error": error}


def call_record(data, response):
    return {
        "method": "eth_call",
        "params": [{"to": TARGET, "data": data}, "0x64"],
        "response": response,
    }


def test_call_returns_the_data_of_a_successful_call():
    rpc, _ = client([call_record("0x12345678", reply("0x" + "00" * 31 + "07"))])
    outcome = rpc.call(TARGET, bytes.fromhex("12345678"))
    assert outcome.ok and int.from_bytes(outcome.data, "big") == 7


def test_a_revert_is_an_outcome_with_its_data_not_an_error():
    revert = bytes.fromhex("08c379a0") + b"\x00" * 4
    error = {"code": 3, "message": "execution reverted: nope", "data": "0x" + revert.hex()}
    rpc, _ = client([call_record("0x12345678", reply(error=error))])
    outcome = rpc.call(TARGET, bytes.fromhex("12345678"))
    assert outcome.ok is False and outcome.data == revert


def test_a_bare_revert_has_empty_data():
    error = {"code": 3, "message": "execution reverted"}
    rpc, _ = client([call_record("0x12345678", reply(error=error))])
    outcome = rpc.call(TARGET, bytes.fromhex("12345678"))
    assert outcome.ok is False and outcome.data == b""


def test_call_pins_the_block_and_checks_the_address():
    rpc, provider = client([call_record("0x01", reply("0x"))])
    rpc.call(TARGET, b"\x01")
    sent = [params for method, params in provider.requests if method == "eth_call"]
    assert sent[-1][1] == "0x64"  # web3 also sends extra eth_chainId requests around a call
    with pytest.raises(ChainError) as caught:
        rpc.call("not-an-address", b"\x01")
    assert caught.value.code == "invalid_address"


def test_other_node_errors_stay_errors():
    rpc, _ = client([call_record("0x01", reply(error={"code": -32000, "message": "boom"}))])
    with pytest.raises(ChainError) as caught:
        rpc.call(TARGET, b"\x01")
    assert caught.value.code in ("rpc_error", "internal_error")


def test_block_info_reads_timestamp_and_base_fee_of_the_pinned_block():
    header = {
        "number": "0x64",
        "timestamp": "0x6553f100",
        "baseFeePerGas": "0x3b9aca00",
        "hash": "0x" + "ab" * 32,
        "parentHash": "0x" + "cd" * 32,
    }
    rpc, _ = client(
        [{"method": "eth_getBlockByNumber", "params": ["0x64", False], "response": reply(header)}]
    )
    info = rpc.block_info()
    assert (info.number, info.timestamp, info.base_fee) == (100, 0x6553F100, 10**9)


def test_a_client_made_from_config_keeps_the_url_for_the_sandbox_only():
    env = {"RPC_URL_ETHEREUM": "http://node.invalid/SECRETKEY"}
    rpc = RpcClient.connect("ethereum", env)
    assert rpc.fork_url == "http://node.invalid/SECRETKEY"
    assert "SECRETKEY" not in repr(rpc) and "SECRETKEY" not in str(rpc.provenance())


def test_a_client_built_by_hand_has_no_url():
    rpc, _ = client([])
    assert rpc.fork_url is None
