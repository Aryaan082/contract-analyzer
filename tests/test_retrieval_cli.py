"""The command line, on the committed data, without any model."""

import json

from retrieval import index as index_module
from retrieval.cli import main


def test_keyword_search_runs_without_a_model_or_an_index(capsys):
    assert main(["search", "who uses tx.origin", "--mode", "bm25", "-k", "3"]) == 0
    out = capsys.readouterr().out
    assert "taxonomy:tx-origin-auth" in out and "bm25=" in out and "cite:" in out


def test_a_filter_reaches_the_search(capsys):
    args = ["search", "drain the vault", "--mode", "bm25", "--filter", "severity=high", "-k", "10"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "[finding]" in out and "[taxonomy]" not in out


def test_json_output_is_valid_and_carries_the_citation(capsys):
    assert main(["search", "initialize", "--mode", "bm25", "--json", "-k", "2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["hits"]) == 2 and payload["hits"][0]["chunk"]["citation"]["document"]


def test_quoted_output_is_available(capsys):
    assert main(["search", "tx.origin", "--mode", "bm25", "-k", "1", "--quoted"]) == 0
    assert "Retrieved reference material" in capsys.readouterr().out


def test_a_missing_index_is_an_error_message_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(index_module, "INDEX_DIR", tmp_path)
    assert main(["search", "owner", "--mode", "dense"]) == 1
    assert "make index" in capsys.readouterr().err


def test_a_bad_filter_value_is_an_error_message(capsys):
    assert main(["search", "owner", "--mode", "bm25", "--filter", "date_from=2024"]) == 1
    assert "error" in capsys.readouterr().err


def test_search_with_no_mode_uses_hybrid_and_says_what_to_run_if_nothing_is_built(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(index_module, "INDEX_DIR", tmp_path)
    assert main(["search", "tx.origin", "-k", "1"]) == 1
    assert "make index" in capsys.readouterr().err
