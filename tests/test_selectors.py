import json

import evmole
import pytest

from chain import selectors as selectors_module
from chain.cli import main
from chain.errors import ToolError
from chain.models import Provenance
from chain.selectors import extract_selectors, run_extract_selectors
from references.abi import keccak_hex
from references.library import get_library
from tests.references_support import load_fixture
from tests.replay import ADDR, ReplayProvider, ok

PROVENANCE = Provenance(chain="ethereum", chain_id=1, address=ADDR, block=100, source="rpc")

# Contracts whose real function list is known: the library's (ABIs from source) and the fixtures
# we compiled ourselves. evmole is held to that ground truth exactly.
GROUND_TRUTH = {
    **{p.reference.id: (p.code, p.selectors) for p in get_library().contracts},
    **{
        name: (load_fixture(name).code, load_fixture(name).selectors)
        for name in (
            "FixtureERC20Plain",
            "FixtureERC20MintBlacklist",
            "FixtureERC20Wizard",
            "FixtureERC20TransferCap",
        )
    },
}


@pytest.mark.parametrize("name", sorted(GROUND_TRUTH))
def test_evmole_finds_exactly_the_real_selectors(name):
    code, expected = GROUND_TRUTH[name]
    found = {info.selector for info in extract_selectors(code)}
    assert found == expected  # no function missed, no constant mistaken for one


def test_the_ground_truth_covers_old_and_new_compilers_and_real_deployments():
    assert sum(len(expected) for _, expected in GROUND_TRUTH.values()) > 200
    assert {"usdt-ethereum", "weth9-ethereum", "uni-ethereum", "oz-erc721"} <= set(GROUND_TRUTH)


def test_proxies_answer_to_nothing_of_their_own():
    for name in ("oz-erc1967-proxy", "eip1167-minimal-proxy", "oz-beacon-proxy"):
        assert extract_selectors(GROUND_TRUTH[name][0]) == ()


def test_arguments_and_mutability_are_reported_as_guesses_that_are_usually_right():
    by_selector = {i.selector: i for i in extract_selectors(GROUND_TRUTH["oz-erc20"][0])}
    transfer = by_selector["0xa9059cbb"]
    assert (transfer.arguments, transfer.state_mutability) == ("address,uint256", "nonpayable")
    balance = by_selector["0x70a08231"]
    assert (balance.arguments, balance.state_mutability) == ("address", "view")
    deposit = {i.selector: i for i in extract_selectors(GROUND_TRUTH["weth9-ethereum"][0])}
    assert deposit["0xd0e30db0"].state_mutability == "payable"


def test_output_is_sorted_and_formatted_like_selectors_everywhere_else():
    found = [i.selector for i in extract_selectors(GROUND_TRUTH["usdt-ethereum"][0])]
    assert found == sorted(found) and len(found) == len(set(found))
    assert all(len(s) == 10 and s.startswith("0x") and s == s.lower() for s in found)


@pytest.mark.parametrize(
    "code",
    [
        b"\x00",
        b"\xff" * 64,
        bytes(range(256)) * 4,
        bytes([0x63, 1, 2]),  # a PUSH4 cut off by the end of the code
        bytes([0x60, 0x00, 0x56]),
    ],
)
def test_odd_code_never_crashes_the_extractor(code):
    assert isinstance(extract_selectors(code), tuple)


# --- the tool --------------------------------------------------------------------------------


def test_the_tool_wraps_the_result_with_the_codes_provenance():
    code = GROUND_TRUTH["oz-erc20"][0]
    result = run_extract_selectors("0x" + code.hex(), PROVENANCE)
    assert (result.provenance.chain, result.provenance.block) == ("ethereum", 100)
    assert result.provenance.address == ADDR
    assert result.provenance.source == f"evmole@{selectors_module.version('evmole')}"
    assert result.data.code_hash == keccak_hex(code) and result.data.code_size == len(code)
    assert len(result.data.selectors) == 9


def test_the_tool_accepts_bytes_as_well_as_hex():
    code = GROUND_TRUTH["oz-erc20"][0]
    assert (
        run_extract_selectors(code, PROVENANCE).data
        == run_extract_selectors(code.hex(), PROVENANCE).data
    )


@pytest.mark.parametrize("bad", ["0xzz", "0x123", "0x", "", "0x" + "00" * 200_000])
def test_bad_code_is_a_structured_error_with_the_envelope_intact(bad):
    result = run_extract_selectors(bad, PROVENANCE)
    assert isinstance(result.data, ToolError)
    assert result.data.error.code == "invalid_bytecode"
    assert result.provenance.block == 100


def test_an_analyser_failure_never_leaks_its_text(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret https://rpc.example/KEY123")

    monkeypatch.setattr(evmole, "contract_info", boom)
    result = run_extract_selectors(b"\x00", PROVENANCE)
    assert result.data.error.code == "internal_error"
    assert "KEY123" not in result.model_dump_json()


# --- the command line ------------------------------------------------------------------------

ENV = {"RPC_URL_ETHEREUM": "http://eth.invalid/SECRETKEY1"}


def test_selectors_command_reads_hex_or_a_file(tmp_path, capsys):
    code = GROUND_TRUTH["oz-erc20"][0]
    assert main(["selectors", "--code", "0x" + code.hex()], env={}) == 0
    inline = json.loads(capsys.readouterr().out)
    path = tmp_path / "code.hex"
    path.write_text("0x" + code.hex() + "\n")
    assert main(["selectors", "--code", f"@{path}"], env={}) == 0
    assert json.loads(capsys.readouterr().out)["data"] == inline["data"]
    assert inline["provenance"]["chain"] == "unknown" and len(inline["data"]["selectors"]) == 9


def test_selectors_command_reads_code_from_an_address_at_one_pinned_block(monkeypatch, capsys):
    code = GROUND_TRUTH["oz-erc20"][0]
    node = ReplayProvider(
        [
            ok("eth_chainId", [], "0x1"),
            ok("eth_blockNumber", [], "0x64"),
            ok("eth_getCode", [ADDR, "0x64"], "0x" + code.hex()),
        ]
    )
    monkeypatch.setattr("chain.rpc.build_provider", lambda url: node)
    assert main(["selectors", "--address", ADDR], env=ENV) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["provenance"]["chain_id"], result["provenance"]["block"]) == (1, 100)
    assert result["provenance"]["address"] == ADDR
    assert len(result["data"]["selectors"]) == 9


def test_selectors_command_failures_stay_structured_and_never_print_the_url(capsys):
    assert main(["selectors", "--address", ADDR, "--chain", "nowhere"], env=ENV) == 1
    out = capsys.readouterr().out
    assert json.loads(out)["data"]["error"]["code"] == "chain_not_configured"
    assert "SECRETKEY1" not in out
    assert main(["selectors", "--address", "not-an-address"], env=ENV) == 1
    assert json.loads(capsys.readouterr().out)["data"]["error"]["code"] == "invalid_address"
    assert main(["selectors", "--code", "@/no/such/file"], env={}) == 2
    assert main(["selectors", "--code", "0xzz"], env={}) == 1
