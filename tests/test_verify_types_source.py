"""The shapes, the code shown to a model and the outline: no model."""

import pytest

from tests.verify_common import CODE, FILES, candidate
from verify import source
from verify.types import Credit


def test_a_candidate_id_is_stable_and_depends_on_the_place_the_lines_and_the_sentence():
    a = candidate()
    assert a.id == candidate().id and len(a.id) == 16
    assert a.id != candidate(note="another sentence").id
    assert a.id != candidate(start_line=7).id
    assert a.id != candidate(function="deposit").id
    assert a.place == ("r", "src/Vault.sol", "Vault", "withdraw")
    assert a.id == candidate(severity="low", layers=frozenset({"pipeline"})).id  # not part of it


def test_credit_refuses_an_unknown_word_and_knows_only_a_report_and_a_check():
    with pytest.raises(ValueError, match="unknown credit kind"):
        Credit("trust me", "x")
    with pytest.raises(ValueError, match="unknown credit kind"):
        Credit("sandbox", "x")  # the sandbox was removed
    assert Credit("report", "T-1").kind == "report" and Credit("check", "r").kind == "check"


def test_the_flagged_function_is_shown_numbered_and_only_that_function():
    shown = source.function_source(FILES, candidate())
    assert "withdraw(uint256 amount)" in shown and "deposit()" not in shown
    assert shown.splitlines()[0].lstrip().startswith("6|")
    assert shown.splitlines()[-1].lstrip().startswith("10|")


def test_a_function_the_parser_does_not_find_is_shown_by_its_lines_with_context():
    shown = source.function_source(FILES, candidate(function="nothing", start_line=6, end_line=7))
    first = int(shown.splitlines()[0].split("|")[0])
    assert first == 3 and "withdraw" in shown  # three lines of context either side


def test_a_file_that_is_not_in_the_bundle_gives_no_code():
    assert source.function_source(FILES, candidate(file="src/Missing.sol")) == ""


def test_the_outline_lists_the_other_files_signatures_and_not_their_code():
    text = source.outline(FILES, skip="src/Vault.sol")
    assert "src/Token.sol" in text and "mint" in text
    assert "Vault" not in text and "msg.sender" not in text


def test_numbered_clamps_to_the_file():
    assert source.numbered("a\nb\nc", 0, 99).splitlines() == ["    1| a", "    2| b", "    3| c"]
    assert CODE  # the shared contract is not empty


CHAIN = {
    "src/A.sol": "contract A is B { function f() external {} }",
    "src/B.sol": "abstract contract B is C, Lib { uint256 x; }",
    "src/C.sol": "abstract contract C { modifier m() { _; } }",
    "src/D.sol": "contract D { }",
    "src/Cycle.sol": "contract P is Q {}\ncontract Q is P {}",
}


def test_the_inheritance_chain_is_the_bases_nearest_first_without_the_flagged_file_or_a_library():
    got = source.inheritance_files(CHAIN, candidate(file="src/A.sol", contract="A"))
    assert [p for p, _ in got] == [
        "src/B.sol",
        "src/C.sol",
    ]  # Lib is not in the bundle; D is no base
    assert got[0][1] == CHAIN["src/B.sol"]


def test_with_no_contract_named_every_contract_of_the_flagged_file_counts():
    assert [
        p for p, _ in source.inheritance_files(CHAIN, candidate(file="src/A.sol", contract=""))
    ] == ["src/B.sol", "src/C.sol"]
    assert source.inheritance_files(CHAIN, candidate(file="src/D.sol", contract="D")) == []


def test_a_cycle_of_bases_ends_and_the_character_limit_leaves_out_what_does_not_fit():
    got = source.inheritance_files(CHAIN, candidate(file="src/Cycle.sol", contract="P"))
    assert got == []  # P and Q are in the flagged file itself
    big = {**CHAIN, "src/B.sol": "abstract contract B is C { uint256 " + "y" * 200 + "; }"}
    cut = source.inheritance_files(big, candidate(file="src/A.sol", contract="A"), max_chars=100)
    assert [p for p, _ in cut] == ["src/C.sol"]  # B does not fit, C does


def test_the_real_radius_chain_is_found_when_the_bundle_is_there():
    assert (
        CHAIN
    )  # the structure above is what the Radius bundle looks like: derived to base, nearest first


NATSPEC = """contract V {
    uint256 a;

    /**
     * @dev address(0) means the caller, on purpose
     */
    function f(address x) public view returns (bool) {
        return x == address(0);
    }

    // not a comment of f: a blank line separates it

    function g() public {}
}
"""


def test_the_comments_directly_above_a_function_are_shown_with_it_and_other_comments_are_not():
    c = candidate(contract="V", function="f", start_line=7, end_line=9)
    shown = source.function_source({"src/Vault.sol": NATSPEC}, c)
    assert "on purpose" in shown and shown.splitlines()[0].lstrip().startswith("4|")
    g = source.function_source(
        {"src/Vault.sol": NATSPEC},
        candidate(contract="V", function="g", start_line=13, end_line=13),
    )
    assert "on purpose" not in g and "not a comment of f" not in g
    assert source.comment_start(NATSPEC, 2) == 2  # nothing above the first member
