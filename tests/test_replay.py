import pytest
from web3 import Web3

from chain.errors import ChainError
from chain.rpc import RpcClient
from tests.replay import ADDR, RecordingProvider, ReplayMiss, ReplayProvider, fail, ok

INTERACTIONS = [
    ok("eth_chainId", [], "0x1"),
    ok("eth_blockNumber", [], "0x64"),
    ok("eth_getCode", [ADDR, "0x64"], "0x6001"),
]


def test_replay_answers_from_the_recording_and_allows_repeats():
    w3 = Web3(ReplayProvider(INTERACTIONS))
    assert w3.eth.chain_id == 1
    assert w3.eth.chain_id == 1
    assert w3.eth.block_number == 100


def test_replay_miss_is_loud_and_not_swallowed_as_a_normal_error():
    rpc = RpcClient("ethereum", Web3(ReplayProvider(INTERACTIONS[:2])))
    with pytest.raises(ReplayMiss):  # not turned into a ChainError by the client's error handling
        rpc.get_code(ADDR)
    assert not issubclass(ReplayMiss, ChainError)


def test_recording_round_trips_results_and_errors_and_never_stores_the_url(tmp_path):
    class FakeNode(ReplayProvider):
        endpoint_uri = "http://secret.invalid/SECRETKEY"

    node = FakeNode([*INTERACTIONS, fail("eth_getStorageAt", [ADDR, "0x0", "0x64"], "boom")])
    recorder = RecordingProvider(node)
    rpc = RpcClient("ethereum", Web3(recorder))
    assert rpc.get_code(ADDR) == bytes.fromhex("6001")
    with pytest.raises(ChainError):
        rpc.get_storage_at(ADDR, 0)

    cassette = tmp_path / "cassette.json"
    recorder.save(cassette)
    assert "SECRETKEY" not in cassette.read_text()
    assert "secret.invalid" not in cassette.read_text()

    replayed = RpcClient("ethereum", Web3(ReplayProvider.from_file(cassette)))
    assert replayed.get_code(ADDR) == bytes.fromhex("6001")
    with pytest.raises(ChainError) as caught:
        replayed.get_storage_at(ADDR, 0)
    assert caught.value.code == "rpc_error"
