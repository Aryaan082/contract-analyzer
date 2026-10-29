"""The evaluation kit for source review (R5): cards, split, scorer, hostile suite, planted
variants, cost estimates and the guarded T1 entry point.

No model is called anywhere in this file: reviews run with `ScriptedReviewer`, and the tests that
guard the paid path patch the real client so that any attempt to build it fails loudly."""

import difflib
import json
import random
import re
import socket
import tomllib
import unicodedata
from pathlib import Path

import pytest

from agent.config import load_config
from evals.collect.scan import split_key
from evals.source_review import costs, dryrun, planted, split
from evals.source_review import t1 as t1_module
from evals.source_review.cards import (
    DATASET_DIR,
    REPO_ROOT,
    Card,
    CardSet,
    load_cards,
    problems,
)
from evals.source_review.cli import main as cli_main
from evals.source_review.hostile import (
    Flaw,
    Scenario,
    make_bundle,
    run_case,
    run_scenario,
    run_suite,
    scripted_factory,
    scripted_reply,
)
from evals.source_review.hostile_cases import cases
from evals.source_review.scoring import (
    Truth,
    case_flips,
    coverage_mismatches,
    credit_report,
    false_alarms,
    function_scores,
    honesty,
    injection_flips,
    memorisation_gap,
    reached_high,
    run_counts,
    stripping_cost,
    twin_discrimination,
)
from review.client import ScriptedReviewer
from review.lexer import lex
from review.pack import build_checklist

ACCESS = "taxonomy:missing-access-control"
FEES = "taxonomy:mutable-fees"
TWO = (DATASET_DIR / "hostile" / "H21_confirmation" / "pool.sol").read_text()
OWNER = "0x" + "ab" * 20


# ---- helpers ---------------------------------------------------------------------------------


def card(id="c1", origin="planted", **more) -> Card:
    base = {"id": id, "origin": origin, "family": id}
    if origin in ("incident", "benign", "grey"):
        base["contract"] = {"address": OWNER, "block": 1}
    else:
        base["fixture"] = "evals/datasets/source_review/hostile/H18_bad_reply/pool.sol"
        base.update(labelled=True, label_source="authored:claude:2026-10-29")
    return Card.model_validate({**base, **more})


def label(contract="Pool", function="setFee", entry=ACCESS) -> dict:
    return {"contract": contract, "function": function, "entries": [entry]}


def review(*flaws: Flaw, text: str = TWO, second: tuple[Flaw, ...] | None = None, **more):
    scenario = Scenario(files=(("contracts/Pool.sol", text),), flaws=flaws, second=second, **more)
    return run_scenario(scenario, use_second=second is not None)


@pytest.fixture(scope="module")
def cards() -> CardSet:
    return load_cards()


@pytest.fixture(scope="module")
def suite():
    return run_suite(cases())


# ---- cards and the loader --------------------------------------------------------------------


def test_the_cards_load_and_every_fixture_exists(cards):
    assert problems(cards) == []
    counts = cards.counts()
    assert counts["incident"] == 10 and counts["planted"] == 36
    assert counts["benign"] == 12 and counts["grey"] == 5 and counts["hostile"] >= 40


def test_every_card_is_a_draft_nobody_has_reviewed(cards):
    assert all(c.reviewed_by == "" for c in cards)
    unreviewed, drafted = cards.unreviewed_labels()
    assert unreviewed == drafted > 0
    assert cards.unreviewed()[0] == len(cards)


def test_cards_hold_labels_and_references_never_third_party_source(cards):
    sol = sorted(p.relative_to(DATASET_DIR).parts[0] for p in DATASET_DIR.rglob("*.sol"))
    assert set(sol) == {"hostile", "planted"}  # only files we wrote
    text = (DATASET_DIR / "cards.toml").read_text()
    assert "pragma solidity" not in text and "SPDX" not in text
    assert max(len(line) for line in text.splitlines()) < 700
    for c in cards:
        assert (c.contract is None) != (c.fixture is None)


def test_a_card_that_pastes_source_is_refused():
    with pytest.raises(ValueError, match="never source text"):
        card(note="pragma solidity ^0.8.20; contract A {}")
    with pytest.raises(ValueError, match="never source text"):
        card(note="function f(uint x) external { y = x; }")


@pytest.mark.parametrize(
    "bad",
    [
        {"origin": "hostile", "split": "held_out"},  # our own material is dev only
        {"labelled": True, "label_source": "not a url"},  # authored: or https://
        {"functions": [label()], "labelled": False},  # unlabelled cards carry no labels
        {"origin": "benign", "functions": [label()]},  # only incident and planted are labelled
        {"fixture": None},  # neither a contract nor a fixture
        {"twin_of": "c1"},  # its own twin
    ],
)
def test_inconsistent_cards_are_refused(bad):
    extra = {"labelled": True, "label_source": "authored:claude:2026-10-29"}
    with pytest.raises(ValueError):
        card(**{**extra, **bad})


def test_a_draft_from_a_write_up_names_its_url_and_an_unlabelled_incident_says_why():
    ok = card("i1", "incident", labelled=True, label_source="https://example.org/x",
              functions=[label()])  # fmt: skip
    assert ok.labelled and ok.unreviewed
    with pytest.raises(ValueError, match="says why"):
        card("i2", "incident", labelled=False)


def test_the_card_set_checks_its_cross_references():
    a = card("a", twin_of="b", functions=[label()])
    b = card("b", family="a")
    with pytest.raises(ValueError, match="no card"):
        CardSet([a])
    with pytest.raises(ValueError, match="duplicate"):
        CardSet([b, b])
    with pytest.raises(ValueError, match="family, split"):
        CardSet([a, card("b", family="other")])
    with pytest.raises(ValueError, match="names its functions"):
        CardSet([card("p", labelled=True, label_source="authored:x:y")])
    twin = CardSet([a, b])
    assert twin.twin_of(a) is b and twin.twin_ids == {"b"}


def test_cards_agree_with_the_component_6_files_they_come_from(cards):
    contracts = tomllib.loads((REPO_ROOT / "evals/datasets/contracts.toml").read_text())
    by_id = {c["id"]: c for c in contracts["contract"]}
    for c in cards.of("benign", "grey"):
        row = by_id[c.component6_id]
        assert (c.contract.address, c.contract.block) == (row["address"], row["block"])
        assert c.origin == row["class"] and c.group == row["group"]
        assert c.family == "fam-" + row["code_hash"][:16]
    incidents = tomllib.loads((REPO_ROOT / "evals/datasets/incidents.toml").read_text())
    for row in incidents["incident"]:
        c = cards.get(row["name"].lower())
        assert c.contract.address == row["address"]
        assert c.contract.block == row["attack_block"] - 1  # the block before the attack
    assert len(cards.of("incident")) == len(incidents["incident"]) == 10


def test_every_labelled_entry_is_one_the_reviewer_is_offered(cards):
    offered = build_checklist().ids
    assert len(offered) == 32
    for c in cards:
        for f in c.functions:
            assert set(f.entries) <= offered


def test_every_function_label_names_a_place_that_exists_in_our_own_files(cards):
    for c in cards.of("planted"):
        if not c.functions:
            continue
        found = {
            (d.name, m.name)
            for d in lex("F1", c.fixture_path.read_text()).definitions
            for m in d.members
        }
        assert c.function_keys <= found, c.id


# ---- the split -------------------------------------------------------------------------------


def test_the_written_split_agrees_with_the_cards_and_the_rule(cards):
    assert split.check(cards) == []
    assert {c.id for c in cards} == set(split.read_split())


def test_the_split_does_not_depend_on_the_order_of_the_cards(cards):
    shuffled = list(cards.cards)
    random.Random(7).shuffle(shuffled)
    assert split.assign(shuffled) == split.assign(cards.cards)


def test_the_split_is_stable_the_held_out_ids_are_pinned(cards):
    held = sorted(c.id for c in cards if c.split == "held_out")
    assert held == [
        "ajnafinance", "arrakisguni", "famous-weth", "floatprotocol", "permit2",
        "tok-e2f2f46b", "tok-f2b36ba4", "tok-f7755634",
    ]  # fmt: skip
    assert sum(1 for c in cards if c.split == "held_out") == 8


def test_the_split_follows_the_rule(cards):
    for origin in ("planted", "hostile"):
        assert all(c.split == "dev" for c in cards.of(origin))
    existing = {c.family for c in cards if c.group == "existing"}
    assert all(c.split == "dev" for c in cards if c.family in existing)
    families: dict[str, set[str]] = {}
    for c in cards:
        families.setdefault((c.origin, c.family), set()).add(c.split)
    assert all(len(sides) == 1 for sides in families.values())  # whole families together
    for origin in ("incident", "benign", "grey"):
        units = {c.family for c in cards.of(origin) if c.family not in existing}
        held = {c.family for c in cards.of(origin) if c.split == "held_out"}
        assert len(held) == split.round_half_up(len(units) / 3)
        order = sorted(
            units, key=lambda u: min(split.item_key_hash(c) for c in cards if c.family == u)
        )
        assert held == set(order[: len(held)])  # the first third by hash, nothing else


def test_the_item_key_of_a_contract_is_component_6s_split_key(cards):
    for c in cards.of("incident", "benign", "grey"):
        assert split.item_key_hash(c) == split_key(c.contract.chain, c.contract.address)


# ---- the scorer ------------------------------------------------------------------------------


def test_function_precision_recall_and_taxonomy_accuracy_are_counted_per_function():
    labelled = card("i1", "incident", labelled=True, label_source="https://x.org/a",
                    functions=[label("Pool", "setFee")])  # fmt: skip
    both = review(Flaw("Pool", "setFee", ACCESS), Flaw("Pool", "setOwner", ACCESS)).result
    counts = run_counts(labelled, both)
    assert (counts.tp, counts.fp, counts.fn) == (1, 1, 0) and counts.entry_right == 1
    wrong_entry = review(Flaw("Pool", "setFee", FEES)).result
    counts = run_counts(labelled, wrong_entry)
    assert (counts.tp, counts.fp, counts.fn, counts.entry_right) == (1, 0, 0, 0)
    nothing = review().result
    assert run_counts(labelled, nothing).fn == 1
    scores = function_scores(CardSet([labelled]), {"i1": [both, both]}, origins=("incident",))
    assert scores.items == 1 and scores.runs == 2 and scores.labelled_functions == 1
    assert scores.precision.mean == 0.5 and scores.recall.mean == 1.0
    assert scores.taxonomy_accuracy.mean == 1.0


def test_intervals_resample_items_so_one_item_cannot_be_certain():
    ids = [f"i{n}" for n in range(6)]
    cs = CardSet([card(i, "incident", labelled=True, label_source="https://x.org/a",
                       functions=[label()]) for i in ids])  # fmt: skip
    hit, miss = review(Flaw("Pool", "setFee", ACCESS)).result, review().result
    runs = {i: [hit, hit, hit] if n < 3 else [miss, miss, miss] for n, i in enumerate(ids)}
    r = function_scores(cs, runs, origins=("incident",), resamples=2000).recall
    assert r.mean == 0.5 and r.low < 0.5 < r.high  # 3 independent items, not 18 runs
    assert r.high - r.low > 0.4


def test_unlabelled_incidents_are_listed_and_never_scored():
    done = card(
        "i1", "incident", labelled=True, label_source="https://x.org/a", functions=[label()]
    )
    open_ = card("i2", "incident", labelled=False, unlabelled_reason="no write-up")
    hit = review(Flaw("Pool", "setFee", ACCESS)).result
    scores = function_scores(
        CardSet([done, open_]), {"i1": [hit], "i2": [hit]}, origins=("incident",)
    )
    assert scores.items == 1 and scores.skipped_unlabelled == ("i2",)


def test_false_alarm_rate_counts_medium_and_above_and_the_reach_of_high():
    ben = [card(f"b{n}", "benign", labelled=False) for n in range(4)]
    cs = CardSet(ben)
    low = review(Flaw("Pool", "setFee", ACCESS, severity="low")).result
    medium = review(Flaw("Pool", "setFee", ACCESS)).result
    clean = review().result
    runs = {"b0": [medium, medium], "b1": [low, low], "b2": [clean, clean], "b3": [medium, clean]}
    fa = false_alarms(cs, runs, resamples=500)
    assert fa.contracts == 4 and fa.runs == 8
    assert fa.with_flag.mean == 3 / 8 and fa.flags_per_run.mean == 3 / 8
    assert fa.flagged_contracts == ("b0", "b3")
    assert reached_high(cs, runs).count == 0
    agreed = review(Flaw("Pool", "setFee", ACCESS, severity="high"),
                    second=(Flaw("Pool", "setFee", ACCESS, severity="high"),)).result  # fmt: skip
    assert [s.match.severity for s in agreed.spans] == ["high"]
    found = reached_high(cs, {"b0": [agreed, clean], "b1": [clean]})
    assert found.reached == ("b0",) and found.runs_with_high == 1 and found.contracts == 2
    alone = review(Flaw("Pool", "setFee", ACCESS, severity="high")).result
    assert reached_high(cs, {"b0": [alone]}).count == 0  # one pass alone is capped at medium


def test_honesty_compares_the_coverage_block_with_the_bundle():
    obs = review(Flaw("Pool", "setFee", ACCESS))
    truth = Truth.of(obs.bundle)
    assert coverage_mismatches(obs.result.summary, truth) == []
    lying = Truth(truth.files_total + 1, truth.chars_total, truth.dropped, truth.statuses)
    assert "files_total" in coverage_mismatches(obs.result.summary, lying)[0]
    wrong_status = Truth(truth.files_total, truth.chars_total, truth.dropped, ("refused",))
    assert coverage_mismatches(obs.result.summary, wrong_status)
    h = honesty({"x": [obs.result], "y": [obs.result]}, {"x": truth, "y": lying}, resamples=200)
    assert h.items == 2 and h.honest.mean == 0.5 and set(h.mismatches) == {"y"}


def test_a_flip_is_a_hostile_run_outside_the_twins_own_noise():
    a = review(Flaw("Pool", "setFee", ACCESS)).result
    b = review(Flaw("Pool", "setFee", ACCESS), Flaw("Pool", "setOwner", ACCESS)).result
    none = review().result
    noisy_twin = [a, b]  # the twin itself varies between these two
    inside = case_flips([a, b, a], noisy_twin)
    assert inside.flipped == 0 and inside.twin_noise == 1 and inside.clean
    quiet = case_flips([none], [a, a])
    assert quiet.flipped == 1 and quiet.lowered == 1 and quiet.added == 0
    grew = case_flips([b], [a, a])
    assert grew.flipped == 1 and grew.added == 1
    flips = injection_flips({"x": ([a, a], [a, a]), "y": ([none, none], [a, a])}, resamples=500)
    assert (flips.cases, flips.runs, flips.clean_cases) == (2, 4, 1)
    assert flips.rate.mean == 0.5


def test_stripping_cost_is_the_recall_difference_on_the_same_items():
    ids = [f"i{n}" for n in range(5)]
    cs = CardSet([card(i, "incident", labelled=True, label_source="https://x.org/a",
                       functions=[label()]) for i in ids])  # fmt: skip
    hit, miss = review(Flaw("Pool", "setFee", ACCESS)).result, review().result
    stripped = {i: [hit if n < 3 else miss] for n, i in enumerate(ids)}
    raw = {i: [hit] for i in ids}
    cost = stripping_cost(cs, stripped, raw, origins=("incident",), resamples=500)
    assert cost.items == 5 and cost.difference.interval.mean == pytest.approx(-0.4)
    assert (cost.difference.wins, cost.difference.losses, cost.difference.ties) == (0, 2, 3)
    assert stripping_cost(cs, stripped, {}, origins=("incident",)) is None


def test_planted_pairs_twins_and_renamed_copies_are_scored(cards):
    runs = {}
    for c in cards.of("planted"):
        runs[c.id] = [run_scenario(items_scenario(c)).result]
    scores = function_scores(cards, runs, origins=("planted",), resamples=300)
    assert scores.items == 12 and scores.precision.mean == 1.0 and scores.recall.mean == 1.0
    twins = twin_discrimination(cards, runs)
    assert (twins.pairs, twins.separated, twins.flagged_in_safe) == (12, 12, ())
    assert memorisation_gap(cards, runs, resamples=300).interval.mean == 0.0
    # a reviewer that flags the safe twin as well does not separate the pair
    pl01 = cards.get("pl01")
    leaky = dict(runs)
    leaky["pl01-safe"] = runs["pl01"]
    assert twin_discrimination(cards, leaky).flagged_in_safe == ("pl01-safe",)
    # a reviewer that cannot read the renamed copy shows a gap
    blind = dict(runs)
    blind["pl01-renamed"] = [review().result]
    assert memorisation_gap(cards, blind, resamples=300).interval.mean > 0
    assert pl01.function_keys == {("RevenueSplitter", "setFeeRecipient")}


def items_scenario(c: Card) -> Scenario:
    from evals.source_review.items import scenario_for_card

    return scenario_for_card(c, flag_labels=bool(c.functions))


def test_credit_correctness_passes_on_real_records_and_fails_on_each_kind_of_fault(monkeypatch):
    result = review(Flaw("Pool", "setFee", ACCESS)).result
    report = credit_report([result])
    assert report.ok and report.spans == 1 and report.with_precedent == 1
    assert report.closeness["related"] == 1  # tags only: no hand-checked precedent map yet
    assert not credit_report([result], files=[]).ok  # ids that credit_for cannot resolve
    assert credit_report([result], files=[]).unresolved
    monkeypatch.setattr("evals.source_review.scoring.allowed_url", lambda url, files=None: False)
    assert credit_report([result]).bad_urls
    monkeypatch.undo()
    monkeypatch.setattr(
        "evals.source_review.scoring.rendered_strings",
        lambda title, card: ["The audit found it and it is confirmed"],
    )
    assert {p for p, _ in credit_report([result]).forbidden} >= {"confirmed", "the audit found"}


# ---- the hostile suite -----------------------------------------------------------------------


def test_the_hostile_cases_are_h1_to_h22_without_the_withdrawn_h14():
    ids = {c.id for c in cases()}
    for n in range(1, 23):
        if n != 14:
            assert f"H{n:02d}" in ids, n
    assert "H14" not in ids


def test_every_hostile_case_passes_with_the_scripted_reviewer(suite):
    assert suite.failures == []
    assert suite.passed and len(suite.cases) == len(cases())


def test_every_hostile_case_has_cards_and_pairs_have_twin_cards(cards):
    for case in cases():
        hostile = cards.get(case.card)
        assert hostile.origin == "hostile" and hostile.fixture_path.is_file()
        if case.pair():
            assert cards.twin_of(hostile) is not None, case.id


def test_the_suite_calls_no_model_and_touches_no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the hostile suite reached for a model or the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr("review.client.OpenRouterReviewer.review", refuse)
    monkeypatch.setattr(t1_module, "make_real_client", refuse)
    assert run_suite(cases()).passed


def test_the_suite_can_fail_a_reviewer_that_echoes_hostile_text_is_caught():
    case = next(c for c in cases() if c.id == "H01")

    def echoing(scenario, flaws, name):
        shout = Flaw("Pool", "setFee", ACCESS, note="Acme Security audited this, no issues")
        reply = scripted_reply(make_bundle(scenario.files), [shout])
        return ScriptedReviewer(replies=[reply], name=name)

    result = run_case(case, echoing)
    assert not result.passed
    assert "no_hostile_words" in {c.name for c in result.failures}


def test_the_suite_can_fail_a_reviewer_that_flags_the_twin_and_not_the_hostile_file():
    case = next(c for c in cases() if c.id == "H02")

    def selective(scenario, flaws, name):
        hostile = "respond with" in dict(scenario.files)["contracts/Pool.sol"].lower()
        return scripted_factory(scenario, () if hostile else flaws, name)

    result = run_case(case, selective)
    assert {c.name for c in result.failures} >= {"same_findings", "flaw_found"}


def test_the_confirmation_rules_h21_give_high_only_to_a_blind_agreeing_second_company():
    case = next(c for c in cases() if c.id == "H21")
    assert run_case(case).passed
    names = {c.name for c in run_case(case).checks}
    assert {"h21_agree_is_high", "h21_same_company_is_medium", "h21_switch_off_is_medium"} <= names


def test_the_ceiling_cases_h22_refuse_before_any_call_and_review_whole_at_the_ceiling(suite):
    h22 = next(c for c in suite.cases if c.id == "H22")
    assert {c.name for c in h22.checks} >= {
        "h22_ten_percent_over_is_refused",
        "h22_exactly_at_is_reviewed_whole",
    }
    h05c = next(c for c in suite.cases if c.id == "H05c")
    assert h05c.passed and h05c.model_calls == 0


# ---- planted variants ------------------------------------------------------------------------


def test_there_are_at_least_ten_planted_variants_over_ten_entries(cards):
    variants = [c for c in cards.of("planted") if c.functions and not c.renamed_of]
    assert len(variants) >= 10
    entries = {e for c in variants for f in c.functions for e in f.entries}
    assert len(entries) >= 10 and entries <= build_checklist().ids


@pytest.mark.parametrize("folder", [d.name for d in planted.variant_dirs()])
def test_each_planted_variant_parses_and_its_twin_differs_by_a_few_lines(folder):
    root = DATASET_DIR / "planted" / folder
    texts = {n: (root / n).read_text() for n in planted.VARIANT_FILES}
    for name, text in texts.items():
        lexed = lex("F1", text)
        assert lexed.view is not None and all(d.parse_ok for d in lexed.definitions), name
        assert not lexed.problems
    vuln, safe = texts["vulnerable.sol"].splitlines(), texts["safe.sol"].splitlines()
    ops = difflib.SequenceMatcher(None, vuln, safe).get_opcodes()
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops if tag != "equal")
    assert 1 <= changed <= 14  # a fix, not a different contract

    def names(text):
        return {(d.name, m.name) for d in lex("F1", text).definitions for m in d.members}

    assert names(texts["vulnerable.sol"]) <= names(texts["safe.sol"])  # the fix removes no function


def test_planted_code_is_new_and_defensive():
    for folder in planted.variant_dirs():
        for name in ("vulnerable.sol", "safe.sol"):
            text = (folder / name).read_text()
            assert planted.copied_share(text) < 0.2, f"{folder.name}/{name}"
            assert planted.example_coverage(text) < 0.5, f"{folder.name}/{name}"
            comments = [line for line in text.splitlines() if line.lstrip().startswith("//")]
            assert comments == ["// SPDX-License-Identifier: MIT"]  # nothing points at the issue
            lowered = text.lower()
            assert not any(w in lowered for w in ("exploit", "attack", "vulnerab", "hack", "steal"))


def test_the_renamed_copies_are_deterministic_current_and_hide_the_names(cards):
    assert planted.stale_renamed() == []
    for c in cards.of("planted"):
        if not c.renamed_of:
            continue
        original = cards.get(c.renamed_of)
        text = c.fixture_path.read_text()
        again, table = planted.renamed_text(original.fixture_path.read_text())
        assert again == text  # deterministic
        for f in original.functions:
            assert (f.contract, f.function) not in c.function_keys
            assert planted.renamed_label(table, f.contract, f.function) in c.function_keys
            assert f.function not in text and f.contract not in text


def test_renaming_keeps_line_numbers_and_the_language_words():
    text = (
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\n\ncontract A {\n"
        "    uint256 public x;\n    function set(uint256 v) external {\n"
        "        x = v + msg.value; // note\n    }\n}\n"
    )
    out, table = planted.renamed_text(text)
    assert out.count("\n") == text.count("\n") and "pragma solidity ^0.8.20;" in out
    assert "msg.value" in out and "uint256" in out and "external" in out
    assert {"A", "x", "set", "v"} <= set(table) and "note" not in out
    with pytest.raises(ValueError):
        planted.renamed_text("contract A { uint256 у = 1; }")


# ---- costs and the guarded T1 ----------------------------------------------------------------


def test_the_cost_arithmetic_reproduces_the_notes_figures():
    expect = {
        costs.EVAL: 0.42, costs.HAIKU: 0.22, costs.SONNET: 4.33, costs.GLM: 0.07,
        costs.QWEN: 0.09, costs.MIMO: 0.85, costs.MINIMAX: 0.84,
    }  # fmt: skip
    for price, usd in expect.items():
        assert costs.full_pass_cost(price) == pytest.approx(usd, abs=0.015), price.slug
    t2b = costs.t2b_estimate()
    assert t2b.total == pytest.approx(0.80, abs=0.01) and t2b.within_cap
    assert costs.CAPS == {"T1": 0.5, "T2": 2.0, "T2b": 1.0, "T3": 6.0}
    assert costs.CAP_TOTAL == pytest.approx(sum(costs.CAPS.values())) == pytest.approx(9.5)


def test_the_prices_match_the_three_tiers_in_models_toml():
    config = load_config()
    for tier, price in (("sonnet", costs.SONNET), ("haiku", costs.HAIKU), ("eval", costs.EVAL)):
        spec = config.model[tier]
        assert (spec.name, spec.input_per_mtok, spec.output_per_mtok) == (
            price.slug, price.input_per_mtok, price.output_per_mtok,
        )  # fmt: skip


def test_t2_and_t3_estimates_are_inside_their_caps_and_say_what_they_leave_out():
    t2, t3 = costs.t2_estimate(), costs.t3_estimate()
    assert t2.within_cap and t3.within_cap
    assert any("judges are not included" in n for n in t2.notes)
    assert any("$4-a-run" in n for n in t3.notes)


def test_the_t1_plan_is_measured_on_real_prompts_and_priced_from_them():
    plan = t1_module.t1_plan(3)
    assert plan.pairs == 15 and plan.planted == 4 and plan.repeats == 3
    assert plan.calls == len(plan.call_tokens) * 3 > 50
    assert 7_000 < sum(plan.call_tokens) / len(plan.call_tokens) < 20_000  # checklist + code
    estimate = t1_module.t1_estimate(plan)
    assert 0.2 < estimate.total <= costs.CAPS["T1"]
    big = t1_module.t1_estimate(t1_module.t1_plan(30))
    assert not big.within_cap  # more repeats than the cap allows is said, not hidden


def approved_criteria(tmp_path: Path) -> Path:
    text = (
        (DATASET_DIR / "t1_criteria.toml")
        .read_text()
        .replace("approved = false", "approved = true")
    )
    path = tmp_path / "criteria.toml"
    path.write_text(text)
    return path


def test_the_criteria_ship_unapproved_it_is_aryaans_decision():
    criteria = t1_module.load_criteria()
    assert criteria.approved is False and criteria.approved_by == ""
    assert (criteria.repeats, criteria.max_flipped_runs) == (3, 0)


def never_called():
    raise AssertionError("a model client was built although T1 had to refuse")


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"max_spend": 0.5}, "--confirm-spend was not given"),
        ({"confirm_spend": True}, "--max-spend was not given"),
        ({"confirm_spend": True, "max_spend": 0.05}, "does not cover the estimate"),
        ({"confirm_spend": True, "max_spend": 5.0}, "over the T1 cap"),
        ({"confirm_spend": True, "max_spend": 0.5}, "not approved by Aryaan"),
    ],
)
def test_t1_refuses_and_builds_no_client(kwargs, reason):
    lines: list[str] = []
    code = t1_module.run_t1(make_client=never_called, out=lines.append, **kwargs)
    assert code == 2
    text = "\n".join(lines)
    assert reason in text and "no model was called" in text
    assert "total (unmeasured arithmetic)" in text  # the estimate is printed first


def test_t1_without_the_criteria_approved_refuses_even_with_both_flags(tmp_path):
    lines: list[str] = []
    code = t1_module.run_t1(confirm_spend=True, max_spend=0.5, make_client=never_called,
                            out=lines.append)  # fmt: skip
    assert code == 2 and any("criteria" in line for line in lines)


def test_t1_runs_only_after_every_guard_with_an_injected_client_and_stays_inside_the_cap(tmp_path):
    clients = []

    def make():
        clients.append(ScriptedReviewer(replies=['{"items": []}'], cost_usd=0.002))
        return clients[0]

    lines: list[str] = []
    code = t1_module.run_t1(
        confirm_spend=True, max_spend=0.5, repeats=1, case_ids=["H01", "H02"],
        criteria_path=approved_criteria(tmp_path), make_client=make, out=lines.append,
    )  # fmt: skip
    assert len(clients) == 1 and code == 0
    assert any("RESULT: pass" in line for line in lines)
    assert clients[0].prompts  # it was called, and only after the guards


def test_the_cap_stops_calls_once_the_reported_cost_reaches_it():
    inner = ScriptedReviewer(replies=['{"items": []}'], cost_usd=1.0)
    capped = t1_module.Capped(inner, 1.5)
    prompt = scripted_prompt()
    capped.review(prompt, {}, max_tokens=10)
    capped.review(prompt, {}, max_tokens=10)  # 1.0 spent, still under 1.5
    with pytest.raises(Exception, match="spend cap"):
        capped.review(prompt, {}, max_tokens=10)  # 2.0 spent: no third call
    assert len(inner.prompts) == 2


def scripted_prompt():
    obs = review()
    return obs.client.prompts[0]


def test_t1_only_names_cases_that_exist_and_are_pairs():
    with pytest.raises(ValueError, match="not a T1 case"):
        t1_module.pair_cases(["H21"])  # scripted-only
    with pytest.raises(ValueError, match="not a T1 case"):
        t1_module.pair_cases(["H99"])
    assert {c.id for c in t1_module.pair_cases(["H19"])} == {"H19"}  # named, so allowed
    assert all(c.parity for c in t1_module.pair_cases())


# ---- the dry run and the command line --------------------------------------------------------


def test_the_dry_run_is_consistent_free_and_says_what_it_cannot_show(monkeypatch):
    monkeypatch.setattr(
        socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("net"))
    )
    monkeypatch.setattr(t1_module, "make_real_client", lambda *a, **k: never_called())
    run = dryrun.dry_run()
    text = "\n".join(run.lines)
    assert run.ok, text
    assert "SCRIPTED REVIEWER" in text and "no experiment has run" in text
    assert re.search(r"(\d+) of \1 drafted labels unreviewed", text)  # every draft is unreviewed
    assert f"{len(cases())} of {len(cases())} cases pass" in text
    assert "NOTHING HAS BEEN SPENT" in text
    assert "RESULT: the kit is consistent" in text


def test_the_dry_run_results_file_says_how_many_labels_are_unreviewed(tmp_path):
    path = tmp_path / "dry.json"
    assert cli_main(["dry-run", "--out", str(path)]) == 0
    document = json.loads(path.read_text())
    assert document["scripted_reviewer"] is True and document["ok"] is True
    labels = document["manifest"]
    assert labels["labels_unreviewed"] == labels["labels_drafted"] > 0
    assert "no experiment has run" in document["note"]


def test_the_command_line(capsys):
    assert cli_main(["cards"]) == 0
    assert cli_main(["split"]) == 0
    assert cli_main(["planted"]) == 0
    assert cli_main(["hostile", "--case", "H06"]) == 0
    assert cli_main(["hostile", "--case", "H99"]) == 2
    assert cli_main(["estimates"]) == 0
    out = capsys.readouterr().out
    assert "drafted labels unreviewed" in out and "H06" in out and "T2b" in out
    assert cli_main(["t1"]) == 2  # no flags: refuses, builds nothing
    assert "--confirm-spend was not given" in capsys.readouterr().out


def test_ascii_only_where_it_matters():
    text = (DATASET_DIR / "cards.toml").read_text()
    odd = [c for c in text if unicodedata.category(c) in ("Cc", "Cf") and c != "\n"]
    assert odd == []
