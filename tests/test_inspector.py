import http.client
import json
import threading

import pytest
from web3.providers.base import JSONBaseProvider

from chain.errors import ChainError
from chain.inspector import (
    InspectorServer,
    describe_slot,
    inspect,
    parse_block,
    parse_slot,
    parse_slots,
)
from chain.slots import EIP1967_ADMIN, EIP1967_BEACON, EIP1967_IMPLEMENTATION
from tests.replay import ADDR, ReplayProvider, fail, ok

ENV = {"RPC_URL_ETHEREUM": "http://eth.invalid/SECRETKEY1"}
# Both hashes come from Foundry's cast (`cast keccak 0x` and `cast keccak 0x6001`), a separate
# implementation from the one under test.
EMPTY_KECCAK = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
KECCAK_6001 = "0x309c67890bde4c575dc23d2cc3b5c3a3d599e312e980e9b61b5bc8f3cd87c8bb"

CHAIN_ID = ok("eth_chainId", [], "0x1")
HEAD = ok("eth_blockNumber", [], "0x64")
# Slot 0 of WETH9, read from mainnet with cast: "Wrapped Ether" as a Solidity short string.
WETH_SLOT0 = "0x577261707065642045746865720000000000000000000000000000000000001a"


@pytest.fixture
def node(monkeypatch):
    """Point the one configured chain at a replay provider the test fills in."""
    state = {}

    def install(interactions):
        state["provider"] = ReplayProvider(interactions)
        monkeypatch.setattr("chain.rpc.build_provider", lambda url: state["provider"])
        return state["provider"]

    return install


def code_and_slot(code="0x6001"):
    return [
        CHAIN_ID,
        HEAD,
        ok("eth_getCode", [ADDR, "0x64"], code),
        ok("eth_getStorageAt", [ADDR, "0x0", "0x64"], WETH_SLOT0),
    ]


# -- parsing -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, None), ("", None), (" 12 ", 12), ("0x10", 16), ("0", 0)]
)
def test_parse_block(raw, expected):
    assert parse_block(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "1.5", "0xzz"])
def test_parse_block_rejects_garbage(raw):
    with pytest.raises(ChainError) as caught:
        parse_block(raw)
    assert caught.value.code == "invalid_block"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", 0), ("0x10", 16), (str(2**256 - 1), 2**256 - 1)],
)
def test_parse_slot(raw, expected):
    assert parse_slot(raw) == expected


@pytest.mark.parametrize("raw", ["", "-1", "nope", str(2**256)])
def test_parse_slot_rejects_garbage_and_out_of_range(raw):
    with pytest.raises(ChainError) as caught:
        parse_slot(raw)
    assert caught.value.code == "invalid_slot"


def test_parse_slots_takes_numbers_and_preset_names_and_drops_duplicates():
    parsed = parse_slots("0, EIP1967-implementation ,0x0, 0x10,,")
    assert parsed == [(None, 0), ("eip1967-implementation", EIP1967_IMPLEMENTATION), (None, 16)]


@pytest.mark.parametrize("raw", [None, "", " , "])
def test_parse_slots_empty_means_none_requested(raw):
    assert parse_slots(raw) == []


def test_parse_slots_rejects_unknown_names_and_too_many():
    for raw in ("eip1967-nonsense", ",".join(str(i) for i in range(9))):
        with pytest.raises(ChainError) as caught:
            parse_slots(raw)
        assert caught.value.code == "invalid_slot"


# -- describing a storage value -------------------------------------------------------------------


def test_describe_slot_reads_a_solidity_short_string():
    # The real slot 0 of WETH9.
    assert describe_slot(bytes.fromhex(WETH_SLOT0[2:]))["as_short_string"] == "Wrapped Ether"


def test_describe_slot_reads_an_address():
    value = bytes(12) + bytes.fromhex("c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2")
    described = describe_slot(value)
    assert described["as_address"] == "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    assert described["as_short_string"] is None


def test_describe_slot_makes_no_claims_about_zero_or_odd_values():
    zero = describe_slot(bytes(32))
    assert zero["as_address"] is None and zero["as_short_string"] is None
    assert zero["as_integer"] == "0"
    odd_length_byte = bytes(31) + b"\x03"  # long-string marker, not a short string
    assert describe_slot(odd_length_byte)["as_short_string"] is None


def test_describe_slot_rejects_a_string_with_junk_between_text_and_length():
    value = b"hi" + b"\x01" + bytes(28) + b"\x04"
    assert describe_slot(value)["as_short_string"] is None


# -- inspect() ---------------------------------------------------------------------


def test_inspect_success_returns_the_small_envelope_and_the_raw_bytes(node):
    node(code_and_slot())
    out = inspect(ENV, {"chain": "ethereum", "address": ADDR, "slots": "0"})

    result = out["tool_result"]
    assert result["provenance"] == {
        "chain": "ethereum",
        "chain_id": 1,
        "address": ADDR,
        "block": 100,
        "source": "rpc",
    }
    code = result["data"]["code"]
    assert (code["has_code"], code["size"], code["keccak256"]) == (True, 2, KECCAK_6001)
    assert result["data"]["storage"] == [{"slot": "0x0", "name": None, "value": WETH_SLOT0}]
    # The agent-facing result stays small: no bytecode in it.
    assert "bytecode" not in json.dumps(result)
    assert out["raw"]["bytecode"] == "0x6001"
    assert out["raw"]["storage"][0]["as_short_string"] == "Wrapped Ether"


def test_inspect_an_address_with_no_code(node):
    node(code_and_slot(code="0x")[:3])
    out = inspect(ENV, {"chain": "ethereum", "address": ADDR})
    code = out["tool_result"]["data"]["code"]
    assert (code["has_code"], code["size"], code["keccak256"]) == (False, 0, EMPTY_KECCAK)
    assert out["tool_result"]["data"]["storage"] == []
    assert out["raw"]["storage"] == []


def test_inspect_defaults_to_ethereum(node):
    node(code_and_slot()[:3])
    assert inspect(ENV, {"address": ADDR})["tool_result"]["provenance"]["chain"] == "ethereum"


def test_inspect_failure_keeps_the_envelope_with_what_is_known(node):
    pruned = fail("eth_getCode", [ADDR, "0x32"], "missing trie node 0xabc (path )")
    node([CHAIN_ID, HEAD, pruned])
    out = inspect(ENV, {"chain": "ethereum", "address": ADDR, "block": "50"})

    result = out["tool_result"]
    assert result["data"]["error"]["code"] == "state_unavailable"
    assert result["provenance"]["chain_id"] == 1
    assert result["provenance"]["block"] == 50
    assert out["raw"] is None


@pytest.mark.parametrize(
    ("query", "code", "address_known"),
    [
        ({"address": "nope"}, "invalid_address", False),
        ({"address": ADDR, "block": "x"}, "invalid_block", True),
        ({"address": ADDR, "slots": "x"}, "invalid_slot", True),
        ({"address": ADDR, "chain": "base"}, "chain_not_configured", True),
    ],
)
def test_inspect_input_errors_make_no_network_call(node, query, code, address_known):
    provider = node([])
    out = inspect(ENV, query)
    assert out["tool_result"]["data"]["error"]["code"] == code
    assert (out["tool_result"]["provenance"]["address"] is not None) is address_known
    assert provider.requests == []


# The runtime code of a real EIP-1167 clone on Ethereum (0x30b0…5a89), read with cast. Its
# size and hash below also come from cast, a separate implementation from the one under test.
CLONE_CODE = (
    "0x363d3d373d3d3d363d73d5fe1c1f216b775dfd30638fa7164d41321ef79b5af43d82803e903d91602b57fd5bf3"
)
CLONE_KECCAK = "0xd37d7cb1b00b1a35c54303484441c301235a1b48e7da36cf2df888ee40df1d1f"
ZERO_WORD = "0x" + "00" * 32


def hexslot(slot):
    return hex(slot)


def test_a_clone_has_code_but_every_standard_slot_is_empty(node):
    node(
        [
            CHAIN_ID,
            HEAD,
            ok("eth_getCode", [ADDR, "0x64"], CLONE_CODE),
            ok("eth_getStorageAt", [ADDR, hexslot(EIP1967_IMPLEMENTATION), "0x64"], ZERO_WORD),
            ok("eth_getStorageAt", [ADDR, hexslot(EIP1967_ADMIN), "0x64"], ZERO_WORD),
            ok("eth_getStorageAt", [ADDR, hexslot(EIP1967_BEACON), "0x64"], ZERO_WORD),
        ]
    )
    out = inspect(
        ENV,
        {"address": ADDR, "slots": "eip1967-implementation,eip1967-admin,eip1967-beacon"},
    )
    data = out["tool_result"]["data"]
    assert (data["code"]["size"], data["code"]["keccak256"]) == (45, CLONE_KECCAK)
    assert [s["name"] for s in data["storage"]] == [
        "eip1967-implementation",
        "eip1967-admin",
        "eip1967-beacon",
    ]
    assert all(s["value"] == ZERO_WORD for s in data["storage"])


def test_several_slots_are_all_read_at_the_one_pinned_block(node):
    impl_word = "0x" + "00" * 12 + "63e749153baf1838f63ca22c275370bd2b1ceb15"
    provider = node(
        [
            CHAIN_ID,
            HEAD,
            ok("eth_getCode", [ADDR, "0x64"], "0x6001"),
            ok("eth_getStorageAt", [ADDR, hexslot(EIP1967_IMPLEMENTATION), "0x64"], impl_word),
            ok("eth_getStorageAt", [ADDR, "0x0", "0x64"], ZERO_WORD),
        ]
    )
    out = inspect(ENV, {"address": ADDR, "slots": "eip1967-implementation, 0"})

    assert [s["value"] for s in out["tool_result"]["data"]["storage"]] == [impl_word, ZERO_WORD]
    assert out["raw"]["storage"][0]["as_address"] == "0x63e749153BaF1838f63cA22c275370BD2B1cEB15"
    methods = [method for method, _ in provider.requests]
    assert methods.count("eth_blockNumber") == 1
    reads = [params for method, params in provider.requests if method == "eth_getStorageAt"]
    assert [params[-1] for params in reads] == ["0x64", "0x64"]


def test_inspect_node_down_reports_unreachable_without_leaking(monkeypatch):
    class Down(JSONBaseProvider):
        def make_request(self, method, params):
            raise ConnectionError("cannot reach http://eth.invalid/SECRETKEY1")

    monkeypatch.setattr("chain.rpc.build_provider", lambda url: Down())
    out = inspect(ENV, {"chain": "ethereum", "address": ADDR})
    assert out["tool_result"]["data"]["error"]["code"] == "rpc_unreachable"
    assert "SECRETKEY" not in json.dumps(out)


# -- the HTTP server ---------------------------------------------------------------


@pytest.fixture
def server(node):
    node([*code_and_slot(), ok("eth_chainId", [], "0x1")])
    srv = InspectorServer(("127.0.0.1", 0), ENV)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, host=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{port}")
    conn.endheaders()
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def test_the_page_is_served_with_security_headers(server):
    response, body = request(server, "GET", "/")
    assert response.status == 200
    assert b"Chain tools inspector" in body
    assert "default-src 'none'" in response.getheader("Content-Security-Policy")
    assert response.getheader("Cache-Control") == "no-store"
    assert response.getheader("X-Content-Type-Options") == "nosniff"


def test_the_page_only_renders_chain_data_with_textcontent(server):
    _, body = request(server, "GET", "/")
    assert b"innerHTML" not in body
    assert b"insertAdjacentHTML" not in body


def test_chains_endpoint_lists_status_and_never_the_url(server):
    response, body = request(server, "GET", "/api/chains")
    assert response.status == 200
    assert json.loads(body) == {
        "chains": [{"name": "ethereum", "chain_id": 1, "status": "ok", "message": None}]
    }
    assert b"SECRETKEY" not in body and b"eth.invalid" not in body


def test_inspect_endpoint_end_to_end(server):
    response, body = request(server, "GET", f"/api/inspect?chain=ethereum&address={ADDR}&slots=0")
    payload = json.loads(body)
    assert response.status == 200
    assert payload["tool_result"]["data"]["code"]["size"] == 2
    assert payload["raw"]["storage"][0]["as_short_string"] == "Wrapped Ether"


def test_a_foreign_host_header_is_rejected(server):
    response, _ = request(server, "GET", "/api/chains", host="evil.example")
    assert response.status == 403


def test_only_get_is_served(server):
    response, _ = request(server, "POST", "/api/inspect")
    assert response.status == 501


def test_unknown_paths_are_404(server):
    response, _ = request(server, "GET", "/.env")
    assert response.status == 404


def test_the_server_binds_to_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"
