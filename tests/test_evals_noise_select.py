"""The noise study's contracts: chosen by a rule, recomputed from the cards, refused if edited."""

import subprocess
from pathlib import Path

import pytest

from evals.cards import Card, CardSet, load_cards
from evals.noise_select import (
    NOTE_PATH,
    STUDY_PATH,
    choose,
    files_sha256,
    git_registration,
    load_study,
    pool_of,
    population_sha256,
    rank,
    verify,
)

DEV_CARDS = Path(__file__).resolve().parent.parent / "evals" / "datasets" / "contracts.toml"
HELDOUT_CARDS = DEV_CARDS.with_name("contracts_heldout.toml")


def card(card_id, klass, traits=("token",), *, tool_use=True, split="dev"):
    return Card.model_validate(
        {
            "id": card_id,
            "address": "0x" + format(abs(hash(card_id)) % 16**40, "040x"),
            "block": 100,
            "class": klass,
            "split": split,
            "traits": list(traits),
            "tool_use": tool_use,
        }
    )


QUOTA = {"flagged": 1, "benign": 1}


def test_the_registered_list_is_what_the_rule_gives_from_the_real_cards():
    study = load_study()
    assert verify(study, load_cards(DEV_CARDS)) == []
    assert study.contracts == 10
    assert study.repeats == 5
    assert sum(study.quota.values()) == 10


def test_the_registered_list_has_seaport_each_class_and_no_heldout_card():
    study = load_study()
    cards = load_cards(DEV_CARDS)
    by_id = {c.id: c for c in cards}
    chosen = [by_id[i] for i in study.ids]
    assert "seaport" in study.ids
    assert {c.klass for c in chosen} == {"flagged", "benign", "grey", "beyond_reach"}
    assert all(c.split == "dev" for c in chosen)
    assert {c.id for c in load_cards(HELDOUT_CARDS)}.isdisjoint(study.ids)
    traits = {t for c in chosen for t in c.traits}
    assert {"proxy", "official", "not_a_token", "token"} <= traits


def test_the_rule_reads_only_the_tool_use_dev_cards_and_the_named_ones():
    cards = CardSet(
        [
            card("in-set", "flagged"),
            card("outside", "flagged", tool_use=False),
            card("held", "flagged", split="heldout"),
            card("named", "benign", tool_use=False),
        ]
    )
    assert {c.id for c in pool_of(cards, ["named"])} == {"in-set", "named"}
    assert choose(cards, salt="s", named=["named"], quota=QUOTA) == ["in-set", "named"]


def test_a_named_card_goes_in_first_and_counts_against_its_class():
    cards = CardSet([card("a", "benign"), card("b", "benign"), card("n", "benign", tool_use=False)])
    assert choose(cards, salt="s", named=["n"], quota={"benign": 1}) == ["n"]
    with pytest.raises(ValueError, match="not a dev card"):
        choose(cards, salt="s", named=["missing"], quota={"benign": 1})
    with pytest.raises(ValueError, match="no room"):
        choose(cards, salt="s", named=["n", "a"], quota={"benign": 1})


def test_every_trajectory_trait_is_covered_even_when_the_hash_would_not_cover_it():
    only_one_proxy = [
        card("proxy-one", "beyond_reach", ("proxy", "token")),
        *[card(f"plain-{i}", "beyond_reach", ("token",)) for i in range(8)],
    ]
    cards = CardSet(only_one_proxy)
    for salt in ("a", "b", "c", "d", "e", "f"):
        assert "proxy-one" in choose(cards, salt=salt, named=[], quota={"beyond_reach": 1})


def test_a_class_prefers_a_trait_mix_not_yet_chosen():
    cards = CardSet(
        [
            card("t1", "benign", ("token",)),
            card("t2", "benign", ("token",)),
            card("t3", "benign", ("token",)),
            card("o1", "benign", ("official",)),
        ]
    )
    for salt in ("a", "b", "c", "d"):
        picked = choose(cards, salt=salt, named=[], quota={"benign": 2})
        assert "o1" in picked  # official is covered first, and the second pick is any token


def test_the_choice_does_not_depend_on_the_order_of_the_cards_file():
    rows = [card(f"c{i}", "flagged", ("token",)) for i in range(12)]
    forward = choose(CardSet(rows), salt="s", named=[], quota={"flagged": 3})
    backward = choose(CardSet(rows[::-1]), salt="s", named=[], quota={"flagged": 3})
    assert sorted(forward) == sorted(backward)


def test_the_rank_is_a_hash_of_the_salt_and_the_id():
    assert rank("a", "x") != rank("b", "x")
    assert rank("a", "x") == rank("a", "x")
    assert len(rank("a", "x")) == 64


def test_a_list_edited_by_hand_is_refused():
    study = load_study()
    cards = load_cards(DEV_CARDS)
    swapped = list(study.ids)
    swapped[1] = "asasar"  # a flagged card the rule did not give
    edited = type(study)(**{**study.__dict__, "ids": tuple(swapped)})
    problems = verify(edited, cards)
    assert any("not what the rule gives" in p for p in problems)


def test_a_changed_pool_is_refused_until_the_study_is_registered_again():
    study = load_study()
    cards = list(load_cards(DEV_CARDS))
    extra = card("late-addition", "flagged")
    problems = verify(study, CardSet([*cards, extra]))
    assert any("pool the rule reads changed" in p for p in problems)


def test_editing_a_label_does_not_change_the_pool_hash():
    one = card("x", "flagged")
    reviewed = Card.model_validate({**one.model_dump(by_alias=True), "reviewed_by": "someone"})
    assert population_sha256([one]) == population_sha256([reviewed])


def test_a_hash_covers_both_registration_files():
    assert len(files_sha256()) == 64
    assert STUDY_PATH.exists() and NOTE_PATH.exists()


def git(where, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=where,
        check=True,
        capture_output=True,
    )


def test_registration_needs_the_files_committed_and_unchanged(tmp_path):
    git(tmp_path, "init", "-q")
    study = tmp_path / "noise_study.toml"
    note = tmp_path / "noise-study.md"
    study.write_text("a = 1\n")
    note.write_text("note\n")
    assert git_registration([study, note]).uncommitted  # never committed
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "register")
    registered = git_registration([study, note])
    assert registered.uncommitted == []
    assert registered.commit and len(registered.commit) == 40
    note.write_text("changed after the fact\n")
    assert git_registration([study, note]).uncommitted == ["noise-study.md"]


def test_registration_reports_when_git_cannot_answer(tmp_path):
    study = tmp_path / "noise_study.toml"
    study.write_text("a = 1\n")
    assert git_registration([study]).uncommitted  # not a repository
