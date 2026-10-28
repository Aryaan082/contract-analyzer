"""The noise study's analysis, on scripted traces whose result fingerprints are set by hand."""

import json

import pytest

from evals.cards import Card, CardSet
from evals.generate import cache_state
from evals.noise import (
    analyse,
    attribution_block,
    build_document,
    call_results,
    classify,
    decide_r,
    exact_interval,
    group_runs,
    registration_notes,
    render,
    share_of,
)
from evals.noise_select import Decision, Study
from evals.runs import parse_trace
from tests.evals_support import make_trace

ADDRS = {name: "0x" + format(i, "02x") * 20 for i, name in enumerate("abcdef", start=1)}
DECISION = Decision(
    calm_direction_max=0,
    calm_exact_max=3,
    noisy_direction_min=3,
    noisy_exact_min=7,
    abstain_max=5,
    tools_share=0.5,
)
ID = ("identify_contract", {"address": "x"})


def run_of(name, verdict, repeat, calls=None, *, stopped="report_accepted", state="warm", n=None):
    """One run of contract `name`. `calls` is a list of (tool, arguments, result fingerprint)."""
    calls = calls if calls is not None else [("identify_contract", {"address": name}, "r-id")]
    trace = make_trace(
        address=ADDRS[name],
        block=100,
        verdict=verdict,
        tools=[(tool, args) for tool, args, _ in calls],
        stopped=stopped,
        tags={"repeat": repeat, "cache_state": state},
        max_tool_calls=n or 25,
    )
    for record, (_, _, sha) in zip(trace["calls"], calls, strict=True):
        record["result_sha"] = sha
    return parse_trace(trace, path=f"{name}-{repeat}.json")


def cards_for(classes=None):
    classes = classes or {}
    return CardSet(
        [
            Card.model_validate(
                {
                    "id": name,
                    "address": address,
                    "block": 100,
                    "class": classes.get(name, "flagged"),
                    "split": "dev",
                    "tool_use": True,
                }
            )
            for name, address in ADDRS.items()
        ]
    )


def study_of(ids, repeats=3):
    return Study(
        version=1,
        salt="s",
        repeats=repeats,
        named=(),
        quota={"flagged": len(ids)},
        population_sha256="x",
        ids=tuple(ids),
        decision=DECISION,
    )


# ---- the statistics ------------------------------------------------------------------------------


def test_the_exact_interval_is_wide_for_ten_contracts_and_open_at_the_ends():
    low, high = exact_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.3085, abs=1e-3)  # "zero of ten rules out > 31%"
    low, high = exact_interval(10, 10)
    assert high == 1.0 and low == pytest.approx(0.6915, abs=1e-3)
    low, high = exact_interval(5, 10)
    assert (low, high) == (pytest.approx(0.187, abs=2e-3), pytest.approx(0.813, abs=2e-3))


def test_a_share_of_contracts_has_both_intervals_and_its_n():
    row = share_of([True, False, False, False], resamples=200)
    assert (row["k"], row["n"], row["share"]) == (1, 4, 0.25)
    assert row["exact_95"][0] < 0.25 < row["exact_95"][1]
    assert row["bootstrap"][0] <= 0.25 <= row["bootstrap"][1]
    assert share_of([], resamples=10) == {"k": 0, "n": 0}
    zero = share_of([False] * 10, resamples=200)
    assert zero["bootstrap"] == [0.0, 0.0] and zero["exact_95"][1] > 0.3  # why both are printed


# ---- the fingerprints and the pairs --------------------------------------------------------------


def test_a_run_gives_its_calls_with_their_result_fingerprints():
    run = run_of("a", "high_risk", 1, [("risk_scan", {"address": "x", "b": 1}, "h1"), ID + ("h2",)])
    assert call_results(run) == {
        ("risk_scan", json.dumps({"address": "x", "b": 1}, sort_keys=True)): "h1",
        ("identify_contract", json.dumps({"address": "x"}, sort_keys=True)): "h2",
    }


def test_a_run_without_fingerprints_cannot_be_compared():
    trace = make_trace(address=ADDRS["a"], block=100)
    for record in trace["calls"]:
        record["result_sha"] = None
    assert call_results(parse_trace(trace)) is None
    assert classify(None, {("t", "{}"): "x"}) == "unknown"


def test_a_repeated_call_is_not_compared_by_its_already_run_note():
    run = run_of("a", "high_risk", 1, [ID + ("first",), ID + ("note",)])
    run = parse_trace(_with_duplicate(run))
    assert list(call_results(run).values()) == ["first"]


def _with_duplicate(run):
    trace = make_trace(address=run.address, block=100, tools=[ID, ID])
    trace["calls"][0]["result_sha"] = "first"
    trace["calls"][1].update(result_sha="note", duplicate=True)
    return trace


def test_pairs_are_the_model_when_the_calls_and_results_match_and_the_tools_when_they_do_not():
    a = {("t1", "{}"): "x", ("t2", "{}"): "y"}
    assert classify(a, dict(a)) == "same_inputs"
    assert classify(a, {("t1", "{}"): "x"}) == "calls_differ"  # fewer calls, shared ones agree
    assert classify(a, {("t1", "{}"): "x", ("t3", "{}"): "z"}) == "calls_differ"
    assert classify(a, {("t1", "{}"): "CHANGED", ("t2", "{}"): "y"}) == "results_differ"
    assert classify({("t1", "{}"): "x"}, {("t9", "{}"): "q"}) == "calls_differ"  # nothing shared


def test_attribution_counts_pairs_by_verdict_difference_and_cause():
    same_calls = [("identify_contract", {"address": "a"}, "r1")]
    changed = [("identify_contract", {"address": "a"}, "r2")]
    by_contract = {
        "a": [
            run_of("a", "high_risk", 1, same_calls),
            run_of("a", "high_risk", 2, same_calls),
            run_of("a", "no_red_flags_found", 3, same_calls),  # direction flip, same inputs: model
        ],
        "b": [
            run_of("b", "high_risk", 1, same_calls),
            run_of("b", "elevated_risk", 2, changed),  # verdict differs, same direction, tools
        ],
        "c": [
            run_of("c", "high_risk", 1, same_calls),
            run_of("c", "insufficient_evidence", 2, changed, stopped="turn_limit"),
        ],
    }
    out = attribution_block(by_contract)
    buckets = out["buckets"]
    assert buckets["different_direction"]["pairs"] == 2
    assert buckets["different_direction"]["same_inputs"] == 2
    assert buckets["same_direction_other_verdict"]["results_differ"] == 1
    assert buckets["same_verdict"]["pairs"] == 1 and buckets["same_verdict"]["same_inputs"] == 1
    assert buckets["abstain_involved"]["pairs"] == 1  # apart: never a verdict flip of the model
    assert out["differing_verdict_by_contract"] == {
        "a": {"same_inputs": 2},
        "b": {"results_differ": 1},
    }
    assert out["per_tool"]["identify_contract"] == {
        "shared_calls": 5,
        "differing": 2,
        "share": 0.4,
    }


def test_the_cache_states_of_a_pair_are_counted_for_pairs_that_differ_and_pairs_that_do_not():
    calls = [("identify_contract", {"address": "a"}, "r1")]
    runs = [
        run_of("a", "high_risk", 1, calls, state="cold"),
        run_of("a", "no_red_flags_found", 2, calls, state="warm"),
        run_of("a", "high_risk", 3, calls, state="warm"),
    ]
    mix = attribution_block({"a": runs})["state_mix"]
    assert mix["verdict_differs"] == {"different_state": 1, "same_state": 1}
    assert mix["verdict_same"] == {"different_state": 1}


# ---- the whole analysis --------------------------------------------------------------------------


def scripted_study():
    """Contracts a..e with 3 runs each: a is steady, b flips direction, c differs in wording only,
    d abstains once, e changes its tools."""
    calls = [("identify_contract", {"address": "x"}, "r1"), ("risk_scan", {"address": "x"}, "r2")]
    runs = []
    for n in (1, 2, 3):
        runs.append(run_of("a", "high_risk", n, calls))
    for n, verdict in enumerate(("high_risk", "no_red_flags_found", "high_risk"), start=1):
        runs.append(run_of("b", verdict, n, calls))
    for n, verdict in enumerate(("high_risk", "elevated_risk", "elevated_risk"), start=1):
        runs.append(run_of("c", verdict, n, calls))
    runs += [
        run_of("d", "high_risk", 1, calls),
        run_of("d", "insufficient_evidence", 2, calls[:1], stopped="turn_limit"),
        run_of("d", "high_risk", 3, calls),
    ]
    runs += [
        run_of("e", "high_risk", 1, calls),
        run_of("e", "high_risk", 2, calls[:1]),
        run_of("e", "high_risk", 3, calls + [("get_deployment", {"address": "x"}, "r3")]),
    ]
    return runs


def test_the_analysis_counts_each_kind_of_difference_with_its_n():
    out = analyse(scripted_study(), cards_for(), study_of("abcde"), resamples=200)
    verdict = out["verdict"]
    assert out["runs"] == 15 and out["contracts"] == 5 and out["planned_runs"] == 15
    assert (verdict["exact"]["k"], verdict["exact"]["n"]) == (3, 5)  # b, c and d differ
    assert (verdict["substantive"]["k"], verdict["substantive"]["n"]) == (2, 5)  # d's abstain apart
    assert (verdict["direction"]["k"], verdict["direction"]["n"]) == (1, 5)  # only b
    assert verdict["flipped"]["b"] == {"high_risk": 2, "no_red_flags_found": 1}
    assert verdict["contracts"]["d"]["abstains"] == 1
    assert out["abstains"]["runs"] == 1 and out["abstains"]["of"] == 15
    assert out["abstains"]["by_cause"] == {"turn_limit": 1}
    assert out["abstains"]["contracts_mixed"] == 1
    tools = out["tool_set"]
    assert (tools["set_changed"]["k"], tools["set_changed"]["n"]) == (2, 5)  # d and e
    assert tools["contracts"]["e"]["sometimes"] == {"get_deployment": 1, "risk_scan": 2}
    assert tools["contracts"]["a"]["sometimes"] == {}


def test_a_runs_that_hit_the_tool_cap_are_counted():
    capped = run_of(
        "a",
        "insufficient_evidence",
        1,
        [("identify_contract", {"address": "a"}, "r1")] * 2,
        stopped="turn_limit",
        n=2,
    )
    other = run_of("a", "high_risk", 2)
    out = analyse([capped, other], cards_for(), study_of(["a"], repeats=2), resamples=50)
    assert out["abstains"]["hit_tool_cap_runs"] == 1


def test_runs_outside_the_study_or_matching_no_card_are_left_out_and_said():
    stray = parse_trace(make_trace(address="0x" + "99" * 20, block=100))
    runs = [*scripted_study(), stray]
    out = analyse(runs, cards_for(), study_of("abc"), resamples=50)
    assert out["unmatched_runs"] == 1 and out["outside_study"] == ["d", "e"]
    assert out["runs"] == 9 and out["contracts"] == 3


def test_stability_uses_the_harness_measures_for_scored_contracts():
    out = analyse(scripted_study(), cards_for({"a": "benign"}), study_of("abcde"), resamples=100)
    rates = out["stability"]["verdict_right"]
    assert rates["contracts"] == 5 and rates["runs"] == 15
    assert rates["stable_pass"] + rates["unstable"] + rates["stable_fail"] == 5
    assert "tool_use_pass" in out["stability"]


def test_a_contract_with_one_run_is_left_out_of_the_flip_denominators():
    runs = [
        run_of("a", "high_risk", 1),
        run_of("b", "high_risk", 1),
        run_of("b", "elevated_risk", 2),
    ]
    out = analyse(runs, cards_for(), study_of(["a", "b"], repeats=2), resamples=50)
    assert (out["verdict"]["exact"]["k"], out["verdict"]["exact"]["n"]) == (1, 1)


# ---- the decision rule ---------------------------------------------------------------------------


def analysis_with(direction=0, exact=0, abstains=0, n=10, differing=(0, 0)):
    pairs, by_tools = differing
    return {
        "verdict": {"exact": {"k": exact, "n": n}, "direction": {"k": direction, "n": n}},
        "abstains": {"runs": abstains, "of": 50},
        "attribution": {
            "buckets": {
                "different_direction": {"pairs": pairs, "results_differ": by_tools},
                "same_direction_other_verdict": {"pairs": 0, "results_differ": 0},
            }
        },
    }


@pytest.mark.parametrize(
    ("direction", "exact", "level", "quick", "milestone"),
    [
        (0, 0, "calm", "R=1", "R=3"),
        (0, 3, "calm", "R=1", "R=3"),
        (0, 4, "moderate", "R=1 only to check", "R=3, then 2 more repeats"),
        (1, 1, "moderate", "R=1 only to check", "R=3, then 2 more repeats"),
        (2, 6, "moderate", "R=1 only to check", "R=3, then 2 more repeats"),
        (3, 3, "noisy", "R=3", "R=5 for every contract"),
        (0, 7, "noisy", "R=3", "R=5 for every contract"),
    ],
)
def test_the_decision_rule_maps_what_the_study_finds_to_r(
    direction, exact, level, quick, milestone
):
    out = decide_r(analysis_with(direction, exact), DECISION, 10)
    assert out["level"] == level
    assert out["quick"].startswith(quick) and out["milestone"].startswith(milestone)
    assert "3 or more" in out["floor"]


def test_the_rule_is_not_applied_to_an_incomplete_study():
    out = decide_r(analysis_with(0, 0, n=6), DECISION, 10)
    assert out["level"] is None and "6 of the study's 10" in out["reason"]


def test_many_abstains_or_volatile_tools_override_the_table():
    out = decide_r(analysis_with(abstains=6), DECISION, 10)
    assert out["level"] == "calm" and "abstains: 6 of 50" in out["overrides"][0]
    assert decide_r(analysis_with(abstains=5), DECISION, 10)["overrides"] == []
    out = decide_r(analysis_with(1, 2, differing=(4, 2)), DECISION, 10)
    assert "tools: 2 of 4 pairs" in out["overrides"][0]
    assert decide_r(analysis_with(1, 2, differing=(4, 1)), DECISION, 10)["overrides"] == []


# ---- the document and the printed report ---------------------------------------------------------


def test_the_document_is_json_the_report_names_every_n_and_the_proposal_is_flagged():
    runs = scripted_study()
    out = analyse(runs, cards_for(), study_of("abcde"), resamples=100)
    manifest = {"name": "noise-test", "noise_study": None}
    doc = build_document(manifest, out, runs)
    json.dumps(doc)  # no NaN, no objects JSON cannot hold
    assert doc["manifest"]["kind"] == "noise" and doc["manifest"]["git_commits"] == ["abc12345"]
    text = render(doc)
    assert "15 runs of 15 planned, 5 of 5 contracts" in text
    assert "3 of 5 contracts" in text and "1 of 5 contracts" in text
    assert "Abstains: 1 of 15 runs" in text and "ended because: 1x turn_limit" in text
    assert "a proposal for Aryaan to approve" in text
    assert "b: 2x high_risk, 1x no_red_flags_found" in text
    assert "this run-set records no registration" in text


def test_a_changed_registration_is_noticed_in_the_report():
    from evals.noise_select import NOTE_PATH, STUDY_PATH, files_sha256

    now = files_sha256((STUDY_PATH, NOTE_PATH))
    assert registration_notes({"noise_study": {"files_sha256": now}}) == []
    changed = registration_notes({"noise_study": {"files_sha256": "0" * 64}})
    assert "differ from what was registered" in changed[0]
    dirty = registration_notes({"noise_study": {"files_sha256": now, "uncommitted": ["x"]}})
    assert "uncommitted" in dirty[0]


def test_runs_are_grouped_by_card_in_repeat_order():
    runs = [run_of("a", "high_risk", 2), run_of("a", "high_risk", 1), run_of("b", "high_risk", 1)]
    grouped, found, loose = group_runs(runs, cards_for())
    assert [r.tags["repeat"] for r in grouped["a"]] == [1, 2] and loose == 0
    assert set(found) == {"a", "b"}


# ---- the cache state a run is tagged with --------------------------------------------------------


@pytest.mark.parametrize(
    ("counters", "state"),
    [
        ({}, "no_store"),
        ({"code_store_lookups": 1, "code_store_hits": 0}, "cold"),
        ({"code_store_lookups": 1, "code_store_hits": 1}, "warm"),
        (
            {
                "code_store_lookups": 2,
                "code_store_hits": 2,
                "selector_names_asked": 40,
                "selector_names_from_cache": 40,
            },
            "warm",
        ),
        (
            {
                "code_store_lookups": 1,
                "code_store_hits": 1,
                "selector_names_asked": 40,
                "selector_names_from_cache": 0,
            },
            "mixed",
        ),
        ({"selector_names_asked": 10, "selector_names_from_cache": 4}, "mixed"),
        ({"code_store_lookups": 3, "code_store_hits": 0, "selector_names_asked": 5}, "cold"),
    ],
)
def test_a_run_is_tagged_from_what_its_stores_answered(counters, state):
    assert cache_state(counters) == state
