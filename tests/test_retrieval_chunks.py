"""Chunks built from the committed taxonomy and findings, recipes and filters."""

import pytest

from retrieval import chunks as chunks_module
from retrieval.chunks import RecipeError, build_chunks, fingerprint, load_recipe
from retrieval.filters import Filters, matches
from tests.retrieval_support import chunk, tiny_corpus


def test_one_chunk_per_entry_and_per_kept_finding_with_unique_ids():
    chunks = build_chunks()
    assert len(chunks) == 54
    assert len({c.id for c in chunks}) == 54
    assert sum(c.record_type == "taxonomy" for c in chunks) == 35
    assert sum(c.record_type == "finding" for c in chunks) == 19


def test_ids_are_the_frozen_ids_the_knowledge_base_uses():
    ids = {c.id for c in build_chunks()}
    assert "taxonomy:tx-origin-auth" in ids
    assert "aave-v3-tob-2021-11:TOB-AAVE-002" in ids


def test_a_finding_chunk_carries_its_citation_and_metadata_but_not_in_its_text():
    finding = next(c for c in build_chunks() if c.id == "morpho-v1-tob-2022-06:TOB-MORPHO-6")
    assert finding.severity == "high" and finding.protocol == "Morpho Protocol V1"
    assert finding.citation.page == 23 and finding.citation.url.endswith("#page=23")
    assert finding.citation.id == "TOB-MORPHO-6"
    assert "Morpho Protocol V1" not in finding.text.split("\n\n")[0]  # metadata is not text


def test_the_code_listing_marker_is_not_part_of_the_text():
    assert not any("[code listing omitted]" in c.text for c in build_chunks())


def test_keywords_reach_the_keyword_side_only():
    entry = next(c for c in build_chunks() if c.id == "taxonomy:tx-origin-auth")
    assert entry.keywords == ("tx.origin",)
    assert entry.keyword_text.endswith("tx.origin")
    assert not entry.text.endswith("tx.origin")


def test_recipes_choose_which_fields_are_in_the_text():
    default = {c.id: len(c.text) for c in build_chunks("default")}
    short = {c.id: len(c.text) for c in build_chunks("short")}
    examples = {c.id: len(c.text) for c in build_chunks("with_examples")}
    assert all(short[i] < default[i] for i in default)
    assert examples["taxonomy:tx-origin-auth"] > default["taxonomy:tx-origin-auth"]
    assert (
        examples["aave-v3-tob-2021-11:TOB-AAVE-002"] == default["aave-v3-tob-2021-11:TOB-AAVE-002"]
    )


def test_an_unknown_recipe_or_field_is_refused(tmp_path, monkeypatch):
    with pytest.raises(RecipeError, match="no recipe"):
        load_recipe("nope")
    bad = tmp_path / "recipes.toml"
    bad.write_text('[x]\nentry = ["title", "colour"]\nfinding = ["title"]\n')
    monkeypatch.setattr(chunks_module, "RECIPES_CONFIG", bad)
    with pytest.raises(RecipeError, match="colour"):
        load_recipe("x")


def test_the_fingerprint_changes_when_a_text_or_the_model_changes():
    corpus = tiny_corpus()
    base = fingerprint(corpus, "model-a", "default")
    edited = [corpus[0].model_copy(update={"text": corpus[0].text + " more"}), *corpus[1:]]
    assert fingerprint(edited, "model-a", "default") != base
    assert fingerprint(corpus, "model-b", "default") != base
    assert fingerprint(corpus, "model-a", "default") == base


def test_filters_by_record_type_severity_protocol_and_group():
    corpus = tiny_corpus()

    def ids(**kw):
        return {c.id for c in corpus if matches(c, Filters(**kw))}

    assert ids(record_type="finding") == {"r1:F-1", "r1:F-2", "r2:F-1"}
    assert ids(severity="high") == {"r1:F-1", "r2:F-1"}  # entries have no severity: dropped
    assert ids(protocol="Beta") == {"r2:F-1"}
    assert ids(group="token_tricks") == {"taxonomy:privileged-mint"}
    assert {c.id for c in corpus if matches(c, None)} == {c.id for c in corpus}


def test_kind_matches_applies_to_and_any_but_never_a_finding():
    corpus = tiny_corpus()
    got = {c.id for c in corpus if matches(c, Filters(kind="erc4626"))}
    assert got == {"taxonomy:tx-origin-auth", "taxonomy:erc4626-share-inflation"}  # one is "any"


def test_date_range_is_inclusive_and_months_are_validated():
    corpus = tiny_corpus()
    got = {c.id for c in corpus if matches(c, Filters(date_from="2022-06", date_to="2024-12"))}
    assert got == {"r1:F-1", "r1:F-2"}
    with pytest.raises(ValueError):
        Filters(date_from="2024")


def test_a_chunk_built_for_a_test_validates_like_a_real_one():
    assert chunk("x:1", "t", "text").citation.id == "x:1"
