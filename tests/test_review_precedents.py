"""Attaching audit findings to a span: the hand-checked map, tags as a fallback, and the fork check.
No model is involved anywhere in this file or in the module it tests."""

from pathlib import Path

import pytest

from knowledge.findings import load_findings
from knowledge.taxonomy import load_taxonomy
from review.lexer import lex
from review.precedents import (
    GENERIC_NAMES,
    PRECEDENT_MAP_PATH,
    ForkHit,
    MapRow,
    PrecedentMap,
    PrecedentMapError,
    attach,
    check_map,
    declared_names,
    finding_identifiers,
    fork_check,
    load_map,
)

FILES = load_findings()
IDS = [f.id for file in FILES for f in file.findings]
SLUGS = list(load_taxonomy())
MORPHO_6 = "morpho-v1-tob-2022-06:TOB-MORPHO-6"
BALV3_12 = "balancer-v3-tob-2024-12:TOB-BALV3-12"
INIT = "taxonomy:unprotected-initializer"


def test_without_a_map_file_the_map_is_empty_and_tags_apply():
    assert load_map(Path("/nonexistent/precedents.toml")) == PrecedentMap()
    refs = attach(INIT, PrecedentMap(), FILES)
    assert [r.finding_id for r in refs] == [BALV3_12]  # the tag; the close match is missed
    assert (refs[0].relation, refs[0].closeness) == ("same_class", "related")


def test_an_entry_the_map_lists_uses_only_the_map_and_keeps_its_closeness():
    pmap = PrecedentMap(
        rows={"unprotected-initializer": MapRow(close=(MORPHO_6,), related=(BALV3_12,))}
    )
    refs = attach(INIT, pmap, FILES)
    assert [(r.finding_id, r.closeness) for r in refs] == [
        (MORPHO_6, "close"),
        (BALV3_12, "related"),
    ]


def test_an_entry_marked_none_gets_no_precedent_and_no_tag_fallback():
    assert (
        attach(INIT, PrecedentMap(rows={"unprotected-initializer": MapRow(none=True)}), FILES) == []
    )


def test_at_most_three_precedents_are_attached():
    ids = IDS[:5]
    refs = attach(
        INIT, PrecedentMap(rows={"unprotected-initializer": MapRow(related=tuple(ids))}), FILES
    )
    assert len(refs) == 3


def test_the_map_file_is_read_and_a_broken_one_raises(tmp_path):
    good = tmp_path / "precedents.toml"
    good.write_text(
        "[entries.unprotected-initializer]\n"
        f'close = ["{MORPHO_6}"]\n'
        "[entries.privileged-mint]\nnone = true\n"
    )
    pmap = load_map(good)
    assert pmap.row(INIT).close == (MORPHO_6,) and pmap.row("taxonomy:privileged-mint").none
    assert pmap.row("taxonomy:reentrancy") is None
    bad = tmp_path / "bad.toml"
    bad.write_text("[entries.x]\nclose = 5\n")
    with pytest.raises(PrecedentMapError):
        load_map(bad)
    bad.write_text("not toml [[[")
    with pytest.raises(PrecedentMapError):
        load_map(bad)


def test_check_map_reports_every_kind_of_mistake_in_plain_words():
    rows = {
        "unprotected-initializer": MapRow(close=("nope:TOB-X-1",)),
        "made-up-entry": MapRow(none=True),
        "reentrancy": MapRow(none=True, close=(MORPHO_6,)),
        "oracle-manipulation": MapRow(close=(MORPHO_6, MORPHO_6)),
        "flash-loan-attacks": MapRow(related=tuple(IDS[:4])),
    }
    problems = check_map(PrecedentMap(rows=rows), SLUGS, IDS)
    text = "\n".join(problems)
    for expected in (
        "unknown finding nope:TOB-X-1",
        "made-up-entry: not a taxonomy entry",
        "reentrancy: marked none but lists findings",
        "oracle-manipulation: lists a finding twice",
        "flash-loan-attacks: lists 4 findings, the most is 3",
    ):
        assert expected in text


def test_a_complete_map_lists_or_marks_every_entry():
    assert check_map(PrecedentMap(), SLUGS, IDS) == []
    problems = check_map(PrecedentMap(), SLUGS, IDS, complete=True)
    assert len(problems) == len(SLUGS) and "neither listed nor marked none" in problems[0]


def test_the_map_file_in_the_repo_if_there_is_one_passes_the_checks():
    pmap = load_map(PRECEDENT_MAP_PATH)
    assert check_map(pmap, SLUGS, IDS) == []


# ---- the fork check -----------------------------------------------------------------------------


def test_identifiers_are_taken_from_the_findings_text_and_look_like_code():
    aave = next(f for file in FILES for f in file.findings if f.id.endswith("TOB-AAVE-007"))
    words = finding_identifiers(aave)
    assert {"eMode", "onBehalfOf"} <= words and "borrow" not in words and "the" not in words


def test_two_distinct_non_generic_names_make_a_fork_hit():
    hits = fork_check(frozenset({"eMode", "onBehalfOf", "other"}), FILES)
    assert any(
        h.finding_id.endswith("TOB-AAVE-007") and {"eMode", "onBehalfOf"} <= h.names for h in hits
    )


def test_unrelated_code_and_generic_names_are_silent():
    assert fork_check(frozenset({"deposit", "Vault", "setFee"}), FILES) == []
    assert fork_check(frozenset(GENERIC_NAMES), FILES) == []
    assert fork_check(frozenset({"eMode"}), FILES) == []  # one name is not enough


def test_a_fork_hit_attaches_only_to_a_span_that_uses_one_of_the_shared_names():
    hit = ForkHit(finding_id="aave-v3-tob-2021-11:TOB-AAVE-007", names=frozenset({"eMode"}))
    on = attach(INIT, PrecedentMap(rows={"unprotected-initializer": MapRow(none=True)}), FILES,
                forks=[hit], span_text="uint8 c = eMode[user];")  # fmt: skip
    assert [(r.finding_id, r.relation) for r in on] == [(hit.finding_id, "same_code_family")]
    off = attach(INIT, PrecedentMap(rows={"unprotected-initializer": MapRow(none=True)}), FILES,
                 forks=[hit], span_text="fee = f;")  # fmt: skip
    assert off == []
    by_function = attach(INIT, PrecedentMap(), FILES, forks=[hit], function="eMode")
    assert by_function[0].relation == "same_code_family"  # fork hits come first


def test_declared_names_are_definitions_and_their_members():
    text = "contract Pool { uint256 public fee; function setFee(uint256 f) external {} }"
    names = declared_names({"F1": lex("F1", text)})
    assert {"Pool", "fee", "setFee"} <= names
