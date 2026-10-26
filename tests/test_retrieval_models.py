"""Needs the downloaded models (`make models`); skipped unless run with `-m models`."""

import pytest

from retrieval.embedders import get_embedder, get_reranker
from retrieval.index import build_index
from retrieval.search import Searcher
from tests.retrieval_support import tiny_corpus

pytestmark = pytest.mark.models


def test_the_small_embedder_puts_related_texts_closer(tmp_path):
    embedder = get_embedder("small")
    owner, mint, weather = embedder.embed_passages(
        [
            "an owner can mint unlimited new tokens",
            "privileged account creates additional supply",
            "it will rain tomorrow in the afternoon",
        ]
    )
    assert float(owner @ mint) > float(owner @ weather)
    assert embedder.token_count("word " * 600) > 512


def test_the_small_reranker_prefers_the_passage_that_answers(tmp_path):
    scores = get_reranker("small").score(
        "who can mint new tokens?",
        ["The owner can create new tokens at will.", "The weather will be mild tomorrow."],
    )
    assert scores[0] > scores[1]


def test_the_whole_stack_finds_a_chunk_on_a_tiny_corpus(tmp_path):
    embedder = get_embedder("small")
    index = build_index(embedder, chunks=tiny_corpus(), index_dir=tmp_path)
    searcher = Searcher(index, embedder=embedder, reranker=get_reranker("small"))
    top = searcher.search("an owner mints new tokens", k=1, mode="hybrid_rerank").hits[0]
    assert top.chunk.id == "taxonomy:privileged-mint"
