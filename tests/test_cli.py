import pytest
from web3.providers.base import JSONBaseProvider

from chain.cli import main
from tests.replay import ReplayProvider, ok


class DeadProvider(JSONBaseProvider):
    def make_request(self, method, params):
        raise ConnectionError("cannot reach http://dead.invalid/SECRETKEY3")


@pytest.fixture
def fake_nodes(monkeypatch):
    nodes = {
        "http://eth.invalid/SECRETKEY1": ReplayProvider([ok("eth_chainId", [], "0x1")]),
        "http://base.invalid/SECRETKEY2": ReplayProvider([ok("eth_chainId", [], "0x2105")]),
        "http://dead.invalid/SECRETKEY3": DeadProvider(),
    }
    monkeypatch.setattr("chain.rpc.build_provider", lambda url: nodes[url])


ENV = {
    "RPC_URL_ETHEREUM": "http://eth.invalid/SECRETKEY1",
    "RPC_URL_BASE": "http://base.invalid/SECRETKEY2",
    "RPC_URL_DEAD": "http://dead.invalid/SECRETKEY3",
    "UNRELATED": "ignored",
}


def test_no_chains_tells_you_what_to_do(capsys):
    assert main(["list"], env={}) == 1
    assert ".env.example" in capsys.readouterr().err


def test_list_shows_chain_ids_and_marks_the_node_that_does_not_answer(fake_nodes, capsys):
    assert main(["list"], env=ENV) == 1  # one chain failed, so not a clean bill of health

    rows = [line.split() for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert rows[0] == ["NAME", "CHAIN", "ID", "STATUS"]
    assert rows[1:4] == [
        ["base", "8453", "ok"],
        ["dead", "-", "unreachable"],
        ["ethereum", "1", "ok"],
    ]


@pytest.mark.parametrize("flags", [[], ["-v"]])
def test_list_never_prints_urls_or_keys_even_when_verbose(fake_nodes, capsys, flags):
    main([*flags, "list"], env=ENV)
    captured = capsys.readouterr()
    assert "SECRETKEY" not in captured.out + captured.err
    assert "invalid" not in captured.out + captured.err
    if flags:
        assert "rpc call failed" in captured.err  # verbose logging did run, scrubbed


def test_list_exits_zero_when_every_node_answers(fake_nodes):
    healthy = {k: v for k, v in ENV.items() if k != "RPC_URL_DEAD"}
    assert main(["list"], env=healthy) == 0
