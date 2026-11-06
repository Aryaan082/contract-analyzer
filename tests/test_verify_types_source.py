"""The shapes, the code shown to a model and the outline: no model, no forge."""

import pytest

from tests.verify_common import CODE, FILES, candidate
from verify import source
from verify.types import Credit, Verdict


def test_a_candidate_id_is_stable_and_depends_on_the_place_the_lines_and_the_sentence():
    a = candidate()
    assert a.id == candidate().id and len(a.id) == 16
    assert a.id != candidate(note="another sentence").id
    assert a.id != candidate(start_line=7).id
    assert a.id != candidate(function="deposit").id
    assert a.place == ("r", "src/Vault.sol", "Vault", "withdraw")
    assert a.id == candidate(severity="low", layers=frozenset({"pipeline"})).id  # not part of it


def test_credit_and_verdict_refuse_unknown_words():
    with pytest.raises(ValueError, match="unknown credit kind"):
        Credit("trust me", "x")
    with pytest.raises(ValueError, match="unknown status"):
        Verdict("probably", "x")


def test_only_a_confirmed_verdict_carries_credit():
    assert Verdict("confirmed", "r", 1, "abc").credit() == Credit("sandbox", "abc")
    for status in ("not_confirmed", "unverifiable"):
        assert Verdict(status, "r", 1, "abc").credit() is None


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
