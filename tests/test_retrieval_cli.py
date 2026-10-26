"""The command line, on the committed data, with fake models."""

import json

import pytest

from retrieval import cli
from retrieval import index as index_module
from retrieval.chunks import build_chunks
from retrieval.cli import main
from retrieval.index import build_index
from tests.retrieval_support import HashingEmbedder, OverlapReranker


@pytest.fixture
def built(tmp_path, monkeypatch):
    """An index of the real 54 chunks made with the fake embedder, and the fakes wired in."""
    embedder = HashingEmbedder()
    build_index(embedder, "default", chunks=build_chunks("default"), index_dir=tmp_path)
    monkeypatch.setattr(index_module, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(cli, "get_embedder", lambda *a, **k: embedder)
    monkeypatch.setattr(cli, "get_reranker", lambda *a, **k: OverlapReranker())


def test_search_prints_hits_with_scores_and_citations(built, capsys):
    assert main(["search", "who uses tx.origin", "-k", "3"]) == 0
    out = capsys.readouterr().out
    assert "taxonomy:tx-origin-auth" in out and "dense=" in out and "cite:" in out
    assert "(dense)" in out  # the default mode


def test_a_filter_reaches_the_search(built, capsys):
    args = ["search", "drain the vault", "--filter", "severity=high", "-k", "10"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "[finding]" in out and "[taxonomy]" not in out


def test_json_output_is_valid_and_carries_the_citation(built, capsys):
    assert main(["search", "initialize", "--json", "-k", "2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["hits"]) == 2 and payload["hits"][0]["chunk"]["citation"]["document"]


def test_quoted_output_is_available(built, capsys):
    assert main(["search", "tx.origin", "-k", "1", "--quoted"]) == 0
    assert "Retrieved reference material" in capsys.readouterr().out


def test_the_reranked_mode_adds_a_rerank_score(built, capsys):
    assert main(["search", "tx.origin", "--mode", "dense_rerank", "-k", "2"]) == 0
    assert "rerank=" in capsys.readouterr().out


def test_there_is_no_keyword_mode(built, capsys):
    with pytest.raises(SystemExit):
        main(["search", "owner", "--mode", "bm25"])
    assert "invalid choice" in capsys.readouterr().err


def test_a_missing_index_is_an_error_message_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(index_module, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(cli, "get_embedder", lambda *a, **k: HashingEmbedder())
    assert main(["search", "owner"]) == 1
    assert "make index" in capsys.readouterr().err


def test_a_bad_filter_value_is_an_error_message(built, capsys):
    assert main(["search", "owner", "--filter", "date_from=2024"]) == 1
    assert "error" in capsys.readouterr().err
