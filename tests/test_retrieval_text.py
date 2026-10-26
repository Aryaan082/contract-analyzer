"""Tokenizer, BM25 and fusion: the three small pieces behind the keyword half of the search."""

import math

import pytest

from retrieval.bm25 import BM25
from retrieval.fusion import rrf
from retrieval.tokens import identifier_tokens, plain_tokens


def test_identifiers_are_kept_whole_and_split_into_parts():
    assert identifier_tokens("tx.origin") == ["tx.origin", "tx", "origin"]
    assert identifier_tokens("_beforeTokenTransfer") == [
        "_beforetokentransfer",
        "before",
        "token",
        "transfer",
    ]
    assert identifier_tokens("ERC1967") == ["erc1967", "erc", "1967"]
    assert identifier_tokens("DOMAIN_SEPARATOR") == ["domain_separator", "domain", "separator"]
    assert identifier_tokens("onlyOwner") == ["onlyowner", "only", "owner"]


def test_ordinary_words_are_lowercased_and_stop_words_dropped():
    assert identifier_tokens("The owner CAN mint.") == ["owner", "mint"]
    assert identifier_tokens("Reentrancy") == ["reentrancy"]


def test_a_stray_short_number_says_nothing_but_a_year_or_eip_number_does():
    assert "20" not in identifier_tokens("ERC20")
    assert "1967" in identifier_tokens("EIP-1967")


def test_the_plain_tokenizer_does_not_keep_identifiers_whole():
    assert "tx.origin" not in plain_tokens("tx.origin")
    assert plain_tokens("tx.origin") == ["tx", "origin"]


def test_bm25_matches_the_formula_worked_by_hand():
    docs = [["owner", "owner", "mint"], ["owner", "pause"], ["pause", "freeze"]]
    bm25 = BM25(docs, k1=1.5, b=0.75)
    n, avg = 3, 7 / 3
    idf_mint = math.log(1 + (n - 1 + 0.5) / (1 + 0.5))
    idf_owner = math.log(1 + (n - 2 + 0.5) / (2 + 0.5))
    norm_first = 1.5 * (1 - 0.75 + 0.75 * 3 / avg)
    assert bm25.idf["mint"] == pytest.approx(idf_mint)
    expected = idf_mint * 1 * 2.5 / (1 + norm_first) + idf_owner * 2 * 2.5 / (2 + norm_first)
    assert bm25.scores(["mint", "owner"])[0] == pytest.approx(expected)
    assert bm25.scores(["mint"])[1:] == [0.0, 0.0]


def test_a_rare_word_is_worth_more_than_a_common_one():
    bm25 = BM25([["owner", "a"], ["owner", "b"], ["owner", "c"], ["origin", "d"]])
    assert bm25.idf["origin"] > bm25.idf["owner"]
    assert bm25.scores(["origin"])[3] > bm25.scores(["owner"])[0]


def test_repeating_a_word_has_diminishing_returns_and_length_is_discounted():
    bm25 = BM25([["x"], ["x", "x"], ["x", "x", "x", "x"], ["y"] * 30 + ["x"], ["z"]])
    s = bm25.scores(["x"])
    assert s[0] < s[1] < s[2]
    assert s[2] - s[1] < s[1] - s[0] + 1e-9 or s[2] < 2 * s[1]
    assert s[3] < s[0]  # the same single occurrence in a much longer document scores less


def test_an_unknown_query_word_scores_nothing_and_an_empty_corpus_is_fine():
    assert BM25([["a"], ["b"]]).scores(["zzz"]) == [0.0, 0.0]
    assert BM25([]).scores(["a"]) == []


def test_repeated_query_words_are_counted_once():
    bm25 = BM25([["a", "b"], ["c"]])
    assert bm25.scores(["a", "a", "a"]) == bm25.scores(["a"])


def test_fusion_rewards_a_chunk_near_the_top_of_both_lists():
    fused = rrf([[7, 1, 2], [8, 1, 3]], k=60)
    order = [item for item, _ in fused]
    assert order[0] == 1  # 2nd in both lists beats 1st in just one
    assert dict(fused)[1] == pytest.approx(2 / 62)
    assert dict(fused)[7] == pytest.approx(1 / 61)
    assert set(order) == {1, 2, 3, 7, 8}


def test_fusion_ties_keep_the_lower_item_first():
    assert [i for i, _ in rrf([[5], [4]])] == [4, 5]
