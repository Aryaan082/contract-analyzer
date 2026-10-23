import json

import pytest
from web3 import HTTPProvider, Web3
from web3.providers.rpc.utils import check_if_retry_on_failure

from chain.errors import ChainError
from chain.rpc import REQUEST_TIMEOUT_SECONDS, RpcClient, build_provider
from tests.replay import ADDR, ReplayProvider, fail, ok

CHAIN_ID = ok("eth_chainId", [], "0x1")
HEAD_100 = ok("eth_blockNumber", [], "0x64")
SLOT = 0x360894A13BA1A3210667C828492DB98DCA3E2076CC3735A920A3CA505D382BBC


def client(interactions, block=None):
    provider = ReplayProvider(interactions)
    return RpcClient("ethereum", Web3(provider), block), provider


def methods(provider):
    return [method for method, _ in provider.requests]


def test_latest_is_resolved_once_and_every_read_uses_that_block():
    rpc, provider = client(
        [
            CHAIN_ID,
            HEAD_100,
            ok("eth_getCode", [ADDR, "0x64"], "0x6001"),
            ok("eth_getStorageAt", [ADDR, "0x0", "0x64"], "0x" + "00" * 32),
        ]
    )
    rpc.get_code(ADDR)
    rpc.get_code(ADDR)
    rpc.get_storage_at(ADDR, 0)

    assert methods(provider).count("eth_blockNumber") == 1
    assert methods(provider).count("eth_chainId") == 1
    reads = [params for method, params in provider.requests if method.startswith("eth_get")]
    assert reads and all(params[-1] == "0x64" for params in reads)


def test_explicit_block_replaces_latest_for_all_reads():
    rpc, provider = client(
        [CHAIN_ID, HEAD_100, ok("eth_getCode", [ADDR, "0x32"], "0x6001")], block=50
    )
    rpc.get_code(ADDR)
    assert rpc.block == 50
    assert "latest" not in json.dumps(provider.requests)


def test_block_after_the_head_is_rejected_with_the_head_in_the_message():
    rpc, _ = client([CHAIN_ID, HEAD_100], block=101)
    with pytest.raises(ChainError) as caught:
        rpc.get_code(ADDR)
    assert caught.value.code == "invalid_block"
    assert "100" in caught.value.message


def test_negative_block_is_rejected():
    with pytest.raises(ChainError) as caught:
        RpcClient("ethereum", Web3(ReplayProvider([])), block=-1)
    assert caught.value.code == "invalid_block"


def test_pruned_block_is_state_unavailable_and_never_retried_at_latest():
    pruned = fail("eth_getCode", [ADDR, "0x32"], "missing trie node 0xabc (path )")
    rpc, provider = client([CHAIN_ID, HEAD_100, pruned], block=50)

    with pytest.raises(ChainError) as caught:
        rpc.get_code(ADDR)

    assert caught.value.code == "state_unavailable"
    code_reads = [params for method, params in provider.requests if method == "eth_getCode"]
    assert code_reads == [[ADDR, "0x32"]]


def test_storage_read_returns_the_raw_32_bytes():
    slot_hex = "0x" + f"{SLOT:x}"
    value = "0x" + "ab" * 32
    rpc, _ = client([CHAIN_ID, HEAD_100, ok("eth_getStorageAt", [ADDR, slot_hex, "0x64"], value)])
    assert rpc.get_storage_at(ADDR, SLOT) == bytes.fromhex("ab" * 32)


def test_invalid_address_fails_before_any_network_call():
    rpc, provider = client([])
    with pytest.raises(ChainError) as caught:
        rpc.get_code("not-an-address")
    assert caught.value.code == "invalid_address"
    assert provider.requests == []


def test_provenance_fills_in_as_things_are_learned_and_never_calls_the_node():
    rpc, provider = client([CHAIN_ID, HEAD_100, ok("eth_getCode", [ADDR, "0x64"], "0x6001")])
    assert rpc.provenance(ADDR).model_dump() == {
        "chain": "ethereum",
        "chain_id": None,
        "address": ADDR,
        "block": None,
        "source": "rpc",
    }
    assert provider.requests == []

    rpc.get_code(ADDR)
    prov = rpc.provenance(ADDR)
    assert (prov.chain_id, prov.block, prov.address, prov.source) == (1, 100, ADDR, "rpc")


def test_node_down_before_pinning_leaves_chain_id_and_block_null():
    class Down(ReplayProvider):
        def make_request(self, method, params):
            raise ConnectionError("cannot reach https://eth.invalid/SECRETKEY")

    rpc = RpcClient("ethereum", Web3(Down([])))
    with pytest.raises(ChainError) as caught:
        rpc.get_code(ADDR)
    assert caught.value.code == "rpc_unreachable"
    assert "SECRETKEY" not in caught.value.message
    prov = rpc.provenance(ADDR)
    assert (prov.chain_id, prov.block) == (None, None)


def test_connect_to_an_unconfigured_chain_is_a_structured_error():
    with pytest.raises(ChainError) as caught:
        RpcClient.connect("base", {"RPC_URL_ETHEREUM": "http://eth.invalid"})
    assert caught.value.code == "chain_not_configured"


# -- the real HTTPProvider, with the network replaced by a fake transport ----------------------


class FakeTransport:
    """Stands in for the HTTP POST. `script` maps method to a list of behaviours: an exception
    to raise, or a result/error dict to answer with. The last behaviour repeats."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, endpoint, data, **kwargs):
        request = json.loads(data)
        method = request["method"]
        self.calls.append(method)
        behaviours = self.script[method]
        step = behaviours[min(self.calls.count(method), len(behaviours)) - 1]
        if isinstance(step, BaseException):
            raise step
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], **step}).encode()


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("web3.providers.rpc.rpc.time.sleep", lambda _: None)


def real_client(transport):
    provider = build_provider("http://node.invalid/SECRETKEY")
    provider._request_session_manager.make_post_request = transport
    return RpcClient("ethereum", Web3(provider))


def test_provider_retries_reads_and_has_a_short_timeout():
    provider = build_provider("http://node.invalid")
    assert isinstance(provider, HTTPProvider)
    config = provider.exception_retry_configuration
    assert config.retries >= 3
    for method in ("eth_chainId", "eth_blockNumber", "eth_getCode", "eth_getStorageAt", "eth_call"):
        assert check_if_retry_on_failure(method, config.method_allowlist)
    assert provider.get_request_kwargs()["timeout"] == REQUEST_TIMEOUT_SECONDS


def test_transient_errors_are_retried_until_the_node_answers(no_sleep):
    boom = ConnectionError("down: http://node.invalid/SECRETKEY")
    transport = FakeTransport({"eth_chainId": [boom, boom, {"result": "0x1"}]})
    assert real_client(transport).chain_id == 1
    assert transport.calls == ["eth_chainId"] * 3


def test_exhausted_retries_are_unreachable_and_leak_nothing(no_sleep):
    transport = FakeTransport(
        {"eth_chainId": [ConnectionError("down: http://node.invalid/SECRETKEY")]}
    )
    rpc = real_client(transport)
    with pytest.raises(ChainError) as caught:
        rpc.chain_id  # noqa: B018
    assert caught.value.code == "rpc_unreachable"
    assert "SECRETKEY" not in caught.value.message
    assert len(transport.calls) == build_provider("http://x").exception_retry_configuration.retries


def test_json_rpc_errors_are_answers_not_transient_so_they_are_not_retried(no_sleep):
    transport = FakeTransport(
        {
            "eth_chainId": [{"result": "0x1"}],
            "eth_blockNumber": [{"result": "0x64"}],
            "eth_getCode": [{"error": {"code": -32000, "message": "missing trie node 0xabc"}}],
        }
    )
    with pytest.raises(ChainError) as caught:
        real_client(transport).get_code(ADDR)
    assert caught.value.code == "state_unavailable"
    assert transport.calls.count("eth_getCode") == 1
