"""chain/profile.py and `python -m chain profile`."""

import json

import pytest
from web3 import Web3

import chain.cli as cli
from chain.errors import ChainError
from chain.explorer import ExplorerClient
from chain.profile import Services, build_profile, failed_tools
from chain.signatures import SignatureServiceError
from chain.slots import EIP1967_IMPLEMENTATION
from tests.chain_support import MemoryCodeStore, MemorySelectorStore, cand
from tests.references_support import load_fixture
from tests.test_explorer import CREATION, Fake, ok, refusal, row
from tests.world import World, address_word

TOKEN = "0x" + "a1" * 20
PROXY = "0x" + "d4" * 20
IMPL = "0x" + "c3" * 20
ERC20 = load_fixture("FixtureERC20Plain").code
DELEGATING = bytes.fromhex("60006000f4")


def names(chunk):
    return {s: (cand(f"fn_{s[2:]}(uint256)"),) for s in chunk}


def services(**overrides):
    opener = Fake(getsourcecode=ok([row()]), getcontractcreation=ok([CREATION]))
    parts = {
        "code_store": MemoryCodeStore(),
        "selector_store": MemorySelectorStore(),
        "explorer": ExplorerClient("k", opener=opener, sleep=lambda s: None),
        "fetch_names": names,
    }
    return Services(**{**parts, **overrides}), opener


def token_world():
    w = World()
    w.codes[TOKEN] = ERC20
    return w


def proxy_world():
    w = World()
    w.codes[PROXY], w.codes[IMPL] = DELEGATING, ERC20
    w.storage[(PROXY, EIP1967_IMPLEMENTATION)] = address_word(IMPL)
    return w


def test_a_plain_token_gets_every_tool_run_once():
    svc, opener = services()
    p = build_profile(token_world(), TOKEN, svc)
    assert (p.address, p.chain, p.chain_id, p.block) == (
        Web3.to_checksum_address(TOKEN),
        "ethereum",
        1,
        100,
    )
    assert p.code.data.has_code and p.proxy.data.kind == "not-a-proxy"
    assert p.identity.data.summary == "standard"
    assert len(p.selectors.data.selectors) > 5
    assert p.implementation_selectors is None and p.implementation_source is None
    assert p.signatures.data.named == len(p.selectors.data.selectors)
    assert p.verified_source.data.verified and p.deployment.data.found
    assert p.skipped == {}
    assert len(opener.requests) == 2  # source and creation, nothing else


def test_every_part_carries_its_own_provenance():
    p = build_profile(token_world(), TOKEN, services()[0])
    sources = {
        "code": p.code.provenance.source,
        "proxy": p.proxy.provenance.source,
        "selectors": p.selectors.provenance.source,
        "signatures": p.signatures.provenance.source,
        "verified_source": p.verified_source.provenance.source,
        "deployment": p.deployment.provenance.source,
    }
    assert sources["code"] == "rpc" and sources["signatures"] == "sourcify-4byte"
    assert sources["verified_source"] == sources["deployment"] == "etherscan-v2"
    assert sources["selectors"].startswith("evmole@")
    # The chain reads agree on the block; the explorer's say they are not block-bound.
    assert {p.code.provenance.block, p.proxy.provenance.block, p.selectors.provenance.block} == {
        100
    }
    assert p.verified_source.provenance.block is None


def test_a_proxy_adds_the_implementations_selectors_and_source():
    svc, opener = services()
    p = build_profile(proxy_world(), PROXY, svc)
    assert p.proxy.data.kind == "eip1967"
    assert p.implementation_selectors.provenance.address == Web3.to_checksum_address(IMPL)
    assert len(p.implementation_selectors.data.selectors) > 5
    # the proxy answers to few functions of its own; the names cover the implementation's too
    own = {s.selector for s in p.selectors.data.selectors}
    behind = {s.selector for s in p.implementation_selectors.data.selectors}
    assert {e.selector for e in p.signatures.data.entries} == own | behind
    assert len(opener.requests) == 3  # source, creation, and the implementation's source


def test_no_code_skips_what_needs_code_and_still_asks_the_explorer():
    svc, _ = services()
    p = build_profile(World(), TOKEN, svc)
    assert not p.code.data.has_code and p.identity.data.summary == "no_code"
    assert p.selectors is None and p.signatures is None
    assert set(p.skipped) == {"extract_selectors", "lookup_selector"}
    assert p.deployment is not None


def test_a_failed_code_read_stops_the_run_and_says_why():
    class Down(World):
        def get_code(self, address):
            raise ChainError("rpc_unreachable", "Could not reach the RPC node after retrying.")

    svc, opener = services()
    p = build_profile(Down(), TOKEN, svc)
    assert p.code.data.error.code == "rpc_unreachable"
    assert p.proxy is None and p.signatures is None and p.deployment is None
    assert "everything else" in p.skipped and opener.requests == []
    assert failed_tools(p) == ["code"]


def test_one_tool_failing_does_not_hide_the_others():
    def broken(chunk):
        raise SignatureServiceError("HTTP 503")

    opener = Fake(
        getsourcecode=refusal("NOTOK", "Invalid API Key"), getcontractcreation=ok([CREATION])
    )
    explorer = ExplorerClient("k", opener=opener, sleep=lambda s: None)
    svc, _ = services(fetch_names=broken, explorer=explorer, selector_store=None)
    p = build_profile(token_world(), TOKEN, svc)
    assert p.signatures.data.error.code == "lookup_unavailable"
    assert p.verified_source.data.error.code == "explorer_error"
    assert p.deployment.data.found and p.proxy.data.kind == "not-a-proxy"
    assert failed_tools(p) == ["signatures", "verified_source"]


def test_without_an_explorer_key_the_explorer_parts_say_so():
    svc, _ = services(explorer=None)
    p = build_profile(token_world(), TOKEN, svc)
    assert p.verified_source.data.error.code == "explorer_not_configured"
    assert p.deployment.data.error.code == "explorer_not_configured"
    assert "explorer tools" in p.skipped


def test_the_code_is_saved_as_a_side_effect():
    svc, _ = services()
    build_profile(proxy_world(), PROXY, svc)
    saved = {a for _, a, *_ in svc.code_store.saves}
    assert {Web3.to_checksum_address(PROXY), Web3.to_checksum_address(IMPL)} <= saved


def test_the_whole_profile_is_json_and_round_trips_its_envelopes():
    p = build_profile(proxy_world(), PROXY, services()[0])
    doc = json.loads(p.model_dump_json())
    assert doc["code"]["provenance"]["block"] == 100 and "data" in doc["proxy"]
    assert doc["skipped"] == {}


# -- the command ---------------------------------------------------------------------------


@pytest.fixture
def fake_chain(monkeypatch):
    world = token_world()

    class Client:
        @staticmethod
        def connect(chain, env, block=None):
            if chain not in ("ethereum", "base"):
                raise ChainError(
                    "chain_not_configured", f"No RPC URL configured for chain {chain!r}."
                )
            world.chain = chain
            return world

    monkeypatch.setattr(cli, "RpcClient", Client)
    monkeypatch.setattr(cli.Services, "from_env", classmethod(lambda cls, env: services()[0]))
    return world


def test_profile_command_prints_one_json_document(fake_chain, capsys):
    assert cli.main(["profile", TOKEN], env={}) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["chain"] == "ethereum" and doc["code"]["data"]["has_code"]


def test_profile_command_takes_a_chain(fake_chain, capsys):
    cli.main(["profile", TOKEN, "--chain", "base"], env={})
    assert json.loads(capsys.readouterr().out)["chain"] == "base"


def test_an_unknown_chain_is_a_profile_with_an_error_and_exit_1(fake_chain, capsys):
    assert cli.main(["profile", TOKEN, "--chain", "nowhere"], env={}) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["code"]["data"]["error"]["code"] == "chain_not_configured"


def test_a_bad_address_is_a_profile_with_an_error_and_exit_1(fake_chain, capsys):
    assert cli.main(["profile", "0xnope"], env={}) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["code"]["data"]["error"]["code"] == "invalid_address"


def test_errors_from_other_tools_are_named_on_stderr_but_do_not_fail_the_command(
    fake_chain, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli.Services, "from_env", classmethod(lambda cls, env: services(explorer=None)[0])
    )
    assert cli.main(["profile", TOKEN], env={}) == 0
    captured = capsys.readouterr()
    assert "verified_source" in captured.err and "deployment" in captured.err
    json.loads(captured.out)  # stdout stays pure JSON
