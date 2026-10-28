"""The offline scoring core: reading traces, the tool-use rules, detection, repeats, citation
validity, time and cost, the results file and the command. Everything runs on synthetic traces
(tests/evals_support.py) and one trace written by a real, scripted agent run."""

import json
import math

import pytest

from agent.config import Limits
from agent.loop import run_agent
from agent.tools import RunContext
from agent.trace import write_trace
from evals import score as score_module
from evals.cards import Card, CardSet, load_cards
from evals.citations import citation_summary, rebuild_attempts
from evals.detection import (
    Confusion,
    by_reason,
    confusion,
    facts_stated,
    forbidden_said,
    grey_check,
    stance,
)
from evals.metrics import Interval
from evals.repeats import cluster_bootstrap, contract_rates, flips
from evals.results_file import build_results, clean, results_name, write_results
from evals.runs import load_runs, load_trace, parse_trace
from evals.timing import cost_summary, describe, exclusion, time_summary
from evals.trajectory import check, rule_failures
from tests.agent_support import (
    TOKEN,
    ScriptedModel,
    call,
    erc20_world,
    offline_services,
    report_body,
    submit,
)
from tests.evals_support import ADDR, IMPL, as_old_trace, make_trace, tools_for


def run_of(**kwargs):
    return parse_trace(make_trace(**kwargs))


def card(**kwargs) -> Card:
    base = {"id": "c", "address": ADDR, "class": "flagged", "block": 100}
    return Card.model_validate({**base, **kwargs})


# ---- reading traces ------------------------------------------------------------------------


def test_a_new_trace_gives_tools_in_order_with_their_seconds_and_a_total():
    run = run_of(tools=tools_for("identify_contract", "risk_scan"))
    assert run.tools == ("identify_contract", "risk_scan")
    assert [c.seconds for c in run.calls] == [1.0, 1.0] and run.has_timing
    assert run.total_seconds == 8.5  # 3 model turns of 2 s, 2 tool calls of 1 s, 0.5 s of setup
    assert run.key == f"ethereum:{ADDR}:100" and run.model == "vendor/cheap"
    assert run.attempts[0].accepted and run.environment["git_commit"] == "abc12345"


def test_an_old_trace_still_gives_tools_and_arguments_but_no_timing():
    run = parse_trace(as_old_trace(make_trace(tools=tools_for("identify_contract", "risk_scan"))))
    assert run.tools == ("identify_contract", "risk_scan")
    assert run.calls[1].arguments == {"address": ADDR} and run.calls[1].evidence_id == "T2"
    assert not run.has_timing and run.total_seconds == run.seconds == 8.0
    assert run.attempts == () and len(run.submissions) == 1
    assert run.calls[0].seconds is None


def test_a_call_the_loop_refused_did_not_run():
    data = as_old_trace(make_trace(tools=tools_for("identify_contract", "risk_scan")))
    data["messages"][5]["content"] = json.dumps({"error": "No more tool calls are allowed."})
    run = parse_trace(data)
    assert run.tools == ("identify_contract",) and run.calls[1].ran is False


def test_a_report_submitted_together_with_other_calls_is_refused_and_is_not_an_attempt():
    data = as_old_trace(make_trace())
    data["messages"] += [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "early",
                    "type": "function",
                    "function": {"name": "submit_report", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "early",
            "content": json.dumps({"accepted": False, "error": "submit_report was not run"}),
        },
    ]
    assert len(parse_trace(data).submissions) == 1


def test_traces_are_found_in_folders_and_other_files_are_skipped(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.json").write_text(json.dumps(make_trace()))
    (tmp_path / "sub" / "b.json").write_text(json.dumps(make_trace(address="0x" + "11" * 20)))
    (tmp_path / "other.json").write_text('{"hello": 1}')
    runs, skipped = load_runs([tmp_path])
    assert len(runs) == 2 and len(skipped) == 1 and "other.json" in skipped[0]
    with pytest.raises(ValueError, match="not an agent trace"):
        load_trace(tmp_path / "other.json")


# ---- cards ---------------------------------------------------------------------------------

CARDS = """
[[contract]]
id = "honey"
address = "0xAB"
class = "flagged"
"""


def test_cards_load_default_their_acceptable_verdicts_and_count_the_unreviewed(tmp_path):
    path = tmp_path / "cards.toml"
    path.write_text(
        f"""
[[contract]]
id = "honey"
address = "{ADDR.upper().replace("0X", "0x")}"
class = "flagged"
reasons = ["honeypot-sell-blocked"]
block = 100

[[contract]]
id = "plain"
address = "0x{"11" * 20}"
class = "benign"
reviewed_by = "aryaan"

[[contract]]
id = "usdc"
address = "0x{"22" * 20}"
class = "grey"
"""
    )
    cards = load_cards(path)
    honey = cards.find(run_of())
    assert honey is not None and honey.id == "honey" and honey.address == ADDR
    assert honey.acceptable == ("elevated_risk", "high_risk")
    assert [c.acceptable for c in cards][1:] == [("no_red_flags_found",), ()]
    assert cards.unreviewed() == (2, 3)
    assert cards.find(run_of(block=101)) is None  # the card pins block 100
    assert [c.id for c in cards.in_split("heldout")] == []


def test_a_duplicate_id_or_a_bad_pattern_is_refused_when_the_file_is_read():
    with pytest.raises(ValueError, match="duplicate card ids"):
        CardSet([card(id="x"), card(id="x")])
    with pytest.raises(ValueError):
        card(facts=["(unclosed"])


# ---- tool use ------------------------------------------------------------------------------


def passed(rule_set, name):
    return next(r.passed for r in rule_set.rules if r.name == name)


def test_the_hygiene_rules_pass_for_a_tidy_run_and_name_what_failed_otherwise():
    tidy = check(run_of(tools=tools_for("identify_contract", "risk_scan")))
    assert tidy.passed and tidy.failed == ()
    untidy = check(
        run_of(
            tools=tools_for("risk_scan", "identify_contract", "risk_scan"),
            stopped="turn_limit",
            max_tool_calls=3,
        )
    )
    assert set(untidy.failed) == {
        "identify_first",
        "no_repeat_calls",
        "under_tool_cap",
        "finished",
    }


def test_a_taxonomy_lookup_may_repeat_but_other_tools_may_not():
    ok = check(run_of(tools=tools_for("identify_contract", "taxonomy_lookup", "taxonomy_lookup")))
    assert passed(ok, "no_repeat_calls")


def test_a_proxy_needs_resolving_and_an_analysis_of_the_implementation():
    proxy = card(traits=["proxy"], implementation=IMPL)
    rest = [("get_verified_source", {"address": ADDR}), ("taxonomy_lookup", {"address": ADDR})]
    good = [
        ("identify_contract", {"address": ADDR}),
        ("resolve_proxy", {"address": ADDR}),
        ("risk_scan", {"address": IMPL}),
        *rest,
    ]
    assert check(run_of(tools=good), proxy).passed
    on_the_proxy_only = [good[0], good[1], ("risk_scan", {"address": ADDR}), *rest]
    bad = check(run_of(tools=on_the_proxy_only), proxy)
    assert bad.failed == ("implementation_analysed",)
    unknown_impl = card(traits=["proxy"])  # no implementation on the card: any other address counts
    assert passed(check(run_of(tools=good), unknown_impl), "implementation_analysed")
    assert not passed(
        check(run_of(tools=on_the_proxy_only), unknown_impl), "implementation_analysed"
    )


def test_a_labelled_reason_requires_the_tools_that_can_show_it():
    honeypot = card(reasons=["honeypot-sell-blocked"], traits=["token"])
    without = check(run_of(tools=tools_for("identify_contract", "get_verified_source")), honeypot)
    sell = "reason:honeypot-sell-blocked:simulate_sell"
    assert sell in without.failed and "sell_rehearsed" in without.failed
    with_sell = check(
        run_of(tools=tools_for("identify_contract", "simulate_sell", "get_verified_source")),
        honeypot,
    )
    assert passed(with_sell, sell) and passed(with_sell, "sell_rehearsed")
    mint = card(reasons=["privileged-mint"])
    reads = tools_for(
        "identify_contract", "risk_scan", "read_getter", "get_verified_source", "taxonomy_lookup"
    )
    assert check(run_of(tools=reads), mint).passed
    who = "reason:privileged-mint:read_getter/read_storage"
    no_read = check(run_of(tools=[t for t in reads if t[0] != "read_getter"]), mint)
    assert no_read.failed == (who,)


def test_a_tool_the_run_was_not_offered_cannot_be_required():
    needs_source = check(run_of(tools=tools_for("identify_contract")), card())
    assert not passed(needs_source, "source_checked")
    skipped = check(
        run_of(tools=tools_for("identify_contract"), unavailable=("get_verified_source",)), card()
    )
    assert passed(skipped, "source_checked") is None


def test_an_official_contract_must_not_be_rehearsed_and_needs_no_context_lookup():
    official = card(traits=["official", "not_a_token"], **{"class": "benign"})
    base = tools_for("identify_contract", "get_verified_source")
    assert check(run_of(tools=base), official).passed
    with_sell = check(run_of(tools=[*base, ("simulate_sell", {"token": ADDR})]), official)
    assert with_sell.failed == ("no_pointless_sell",)


def test_explicit_requires_and_forbids_and_the_failure_counts():
    c = card(requires=["list_selectors"], forbids=["simulate_sell"])
    runs = [
        run_of(
            tools=tools_for(
                "identify_contract", "list_selectors", "get_verified_source", "taxonomy_lookup"
            )
        ),
        run_of(tools=tools_for("identify_contract", "simulate_sell")),
    ]
    results = [check(r, c) for r in runs]
    assert results[0].passed and not results[1].passed
    counts = rule_failures(results)
    assert counts["requires:list_selectors"] == {"applied": 2, "failed": 1}
    assert counts["forbids:simulate_sell"] == {"applied": 2, "failed": 1}
    assert counts["identify_first"] == {"applied": 2, "failed": 0}


# ---- detection -----------------------------------------------------------------------------


def test_precision_recall_and_f1_on_the_worked_example():
    """30 flagged and 30 benign; the agent warns on 24: 21 truly flagged and 3 benign."""
    c = Confusion(tp=21, fn=9, fp=3, tn=27)
    assert c.precision == pytest.approx(0.875) and c.recall == pytest.approx(0.70)
    assert c.f1 == pytest.approx(0.7778, abs=1e-4) and c.coverage == 1.0


def test_an_abstain_on_a_flagged_contract_is_a_miss_and_on_a_benign_one_is_not_an_alarm():
    flagged, benign = card(), card(id="b", **{"class": "benign"})
    pairs = [
        (run_of(verdict="high_risk"), flagged),
        (run_of(verdict="insufficient_evidence"), flagged),
        (run_of(verdict="no_red_flags_found"), benign),
        (run_of(verdict="insufficient_evidence"), benign),
        (run_of(verdict="elevated_risk"), card(id="g", **{"class": "grey"})),  # not scored
    ]
    c = confusion(pairs)
    assert (c.tp, c.fn, c.abstain_flagged, c.fp, c.tn, c.abstain_benign) == (1, 0, 1, 0, 1, 1)
    assert c.recall == 0.5 and c.precision == 1.0 and c.coverage == 0.5


def test_a_measure_with_nothing_to_divide_is_undefined_not_zero():
    only_clear = confusion([(run_of(verdict="no_red_flags_found"), card(**{"class": "benign"}))])
    assert math.isnan(only_clear.precision) and math.isnan(only_clear.recall)
    assert math.isnan(only_clear.f1)


def test_the_verdicts_map_to_stances():
    assert [stance(v) for v in ("high_risk", "elevated_risk")] == ["warn", "warn"]
    assert stance("no_red_flags_found") == "clear" and stance("insufficient_evidence") == "abstain"


def test_recall_is_split_by_labelled_reason():
    two = card(reasons=["honeypot-sell-blocked", "mutable-fees"])
    one = card(id="o", reasons=["privileged-mint"])
    rows = by_reason(
        [
            (run_of(verdict="high_risk"), two),
            (run_of(verdict="no_red_flags_found"), one),
        ]
    )
    assert rows == {
        "honeypot-sell-blocked": {"runs": 1, "warned": 1},
        "mutable-fees": {"runs": 1, "warned": 1},
        "privileged-mint": {"runs": 1, "warned": 0},
    }


def test_facts_forbidden_words_and_the_not_checked_list_are_checked_by_pattern():
    c = card(
        **{"class": "grey"},
        facts=[r"mint function", r"not run"],
        must_not_say=[r"\bCircle\b"],
        min_not_checked=2,
    )
    ok = run_of(not_checked=(("a", "x"), ("b", "y")))
    assert facts_stated(ok, c) and forbidden_said(ok, c) == []
    assert grey_check(ok, c)["not_checked_ok"] is True
    bad = run_of(summary="Circle's stablecoin.", claim="Something else.", not_checked=())
    assert facts_stated(bad, c) is False and forbidden_said(bad, c) == [r"\bCircle\b"]
    assert grey_check(bad, c)["not_checked_ok"] is False
    assert facts_stated(ok, card()) is None  # no facts on the card


# ---- repeats -------------------------------------------------------------------------------


def test_repeat_rates_count_runs_contracts_and_stability():
    rates = contract_rates(
        {"a": [True, True, False], "b": [True, True, True], "c": [False, False, False]},
        resamples=500,
    )
    assert rates.single_run.mean == pytest.approx(5 / 9)
    assert rates.majority.mean == pytest.approx(2 / 3) and rates.all_correct.mean == pytest.approx(
        1 / 3
    )
    assert (rates.stable_pass, rates.unstable, rates.stable_fail) == (1, 1, 1)
    assert (rates.contracts, rates.runs) == (3, 9)
    assert rates.single_run.low <= rates.single_run.mean <= rates.single_run.high


def test_a_verdict_that_flips_is_found_and_a_single_run_cannot_flip():
    result = flips(
        {
            "x": ["elevated_risk", "elevated_risk", "no_red_flags_found"],
            "y": ["high_risk"] * 3,
            "z": ["high_risk"],
        }
    )
    assert result.rate == 0.5 and result.contracts == {
        "x": {"elevated_risk": 2, "no_red_flags_found": 1}
    }
    assert flips({"z": ["high_risk"]}).rate is None


def test_the_cluster_interval_is_repeatable_wider_for_fewer_contracts_and_skips_undefined_draws():
    clusters = [[1.0, 1.0, 0.0], [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [1.0, 0.0, 0.0]]

    def mean(xs):
        return sum(xs) / len(xs)

    a, b = (
        cluster_bootstrap(clusters, mean, resamples=400),
        cluster_bootstrap(clusters, mean, resamples=400),
    )
    assert a == b and a.low <= a.mean <= a.high
    more = cluster_bootstrap(clusters * 5, mean, resamples=400)
    assert (more.high - more.low) < (a.high - a.low)
    only = cluster_bootstrap([[1.0]], mean, resamples=50)
    assert only == Interval(1.0, 1.0, 1.0)

    def sometimes_undefined(xs):
        return float("nan") if sum(xs) == 0 else mean(xs)

    assert cluster_bootstrap(clusters, sometimes_undefined, resamples=200).mean == pytest.approx(
        0.5
    )
    with pytest.raises(ValueError):
        cluster_bootstrap([], mean)


# ---- citation validity ---------------------------------------------------------------------


def test_citation_validity_is_counted_on_the_first_submission():
    runs = [
        run_of(first_try_ok=False),
        run_of(address="0x" + "11" * 20),
        run_of(address="0x" + "22" * 20),
    ]
    summary = citation_summary(runs, resamples=200)
    assert summary.runs == 3 and summary.no_submission == 0
    assert summary.first_attempt_accepted.mean == pytest.approx(2 / 3)
    assert summary.accepted_after_retry == 1 and summary.ended_rejected == 0
    assert (summary.claims_first, summary.claims_failing_first) == (3, 1)
    assert summary.claim_validity.mean == pytest.approx(2 / 3)
    assert summary.problem_codes == {"unknown_evidence": 1}
    assert summary.rebuilt_runs == 0


def test_attempts_of_an_old_trace_are_rebuilt_by_running_the_validator_again():
    old = parse_trace(as_old_trace(make_trace(first_try_ok=False)))
    first, second = rebuild_attempts(old)
    assert not first.accepted and first.parsed and first.claims == 1 and first.claims_failing == 1
    assert ("findings[0]", "unknown_evidence") in first.problems and first.rebuilt
    assert second.accepted and second.problems == () and second.claims_failing == 0
    summary = citation_summary([old], resamples=100)
    assert summary.rebuilt_runs == 1 and summary.accepted_after_retry == 1
    assert summary.first_attempt_accepted.mean == 0.0


def test_a_run_that_ended_before_any_report_is_counted_apart():
    stopped = run_of(stopped="turn_limit")
    summary = citation_summary([stopped, run_of()], resamples=100)
    assert summary.no_submission == 1 and summary.first_attempt_accepted.mean == 1.0
    assert summary.ended_rejected == 0


# ---- time and cost -------------------------------------------------------------------------


def test_a_95th_percentile_is_only_shown_from_forty_runs():
    few = describe([1.0, 2.0, 3.0, 4.0, 10.0])
    assert few["n"] == 5 and few["p50"] == 3.0 and few["max"] == 10.0 and few["p95"] is None
    assert "too small" in few["note"]
    many = describe([float(i) for i in range(1, 101)])
    assert many["n"] == 100 and many["p95"] == 95.0 and many["p50"] == 51.0
    lo, hi = many["p95_interval"]
    assert lo <= 95.0 <= hi
    assert describe([]) == {"n": 0}


def test_runs_that_cannot_be_trusted_for_time_are_left_out_with_a_reason():
    assert exclusion(run_of()) is None
    assert exclusion(run_of(timing=False)) == "no_timing"
    assert exclusion(run_of(tags={"concurrent": True})) == "concurrent"
    assert exclusion(run_of(skew=100.0)) == "slept"
    assert exclusion(run_of(skew=0.5)) is None  # a gap under a second is clock noise
    data = make_trace()
    data["calls"][0]["wall_seconds"] = 60.0  # one tool call that the machine slept through
    assert exclusion(parse_trace(data)) == "slept"


def test_time_is_summarised_per_model_with_the_split_and_the_runs_left_out():
    runs = [
        run_of(
            model="a/fast", tools=tools_for("identify_contract", "simulate_sell"), tool_seconds=2.0
        ),
        run_of(model="a/fast", skew=100.0),
        run_of(model="a/fast", tags={"cache_state": "cold"}, tool_seconds=3.0),
        run_of(model="b/slow", model_seconds=10.0),
    ]
    summary = time_summary(runs)
    fast = summary["a/fast"]
    assert fast["runs"] == 3 and fast["used"] == 2 and fast["left_out"] == {"slept": 1}
    assert fast["end_to_end_s"]["n"] == 2 and fast["per_tool_s"]["simulate_sell"]["n"] == 1
    assert fast["by_cache_state"]["cold"]["n"] == 1
    assert fast["model_s"]["mean"] == fast["model_s"]["p50"] or fast["model_s"]["n"] == 2
    assert summary["b/slow"]["model_s"]["max"] == 20.0


def test_cost_is_summarised_with_the_cached_share_and_tool_calls():
    runs = [run_of(cost=0.01), run_of(cost=0.03, tools=tools_for("identify_contract", "risk_scan"))]
    cost = cost_summary(runs)["vendor/cheap"]
    assert cost["runs"] == 2 and cost["total_usd"] == 0.04 and cost["cached_share"] == 0.75
    assert cost["per_report_usd"]["max"] == 0.03 and cost["tool_calls_mean"] == 1.5


# ---- the results file ----------------------------------------------------------------------


def test_the_results_file_cleans_intervals_and_undefined_numbers(tmp_path):
    out = clean({"p": Interval(0.8, 0.7, 0.9), "r": float("nan"), "n": [1, (2, 3)], "k": {1: "x"}})
    assert out == {
        "p": {"value": 0.8, "low": 0.7, "high": 0.9},
        "r": None,
        "n": [1, [2, 3]],
        "k": {"1": "x"},
    }
    assert results_name(
        __import__("datetime").date(2026, 10, 28), "abcdef1234", "dev run 1"
    ).endswith("2026-10-28-abcdef12-dev-run-1.json")
    results = build_results({"git_commits": ["abcdef1234"]}, {"layer": {"x": Interval(1, 1, 1)}})
    path = write_results(results, "my run", tmp_path)
    saved = json.loads(path.read_text())
    assert saved["schema_version"] == 1 and saved["layer"]["x"]["value"] == 1
    assert path.name.endswith("-abcdef12-my-run.json")


# ---- the whole score -----------------------------------------------------------------------

SET = """
[[contract]]
id = "honey"
address = "{honey}"
class = "flagged"
reasons = ["honeypot-sell-blocked"]
traits = ["token"]
block = 100

[[contract]]
id = "plain"
address = "{plain}"
class = "benign"
block = 100
split = "heldout"

[[contract]]
id = "usdc"
address = "{usdc}"
class = "grey"
min_not_checked = 1
block = 100
"""
HONEY, PLAIN, USDC = ("0x" + c * 40 for c in "123")


def write_set(tmp_path):
    cards = tmp_path / "cards.toml"
    cards.write_text(SET.format(honey=HONEY, plain=PLAIN, usdc=USDC))
    folder = tmp_path / "runs"
    folder.mkdir()
    traces = [
        make_trace(
            address=HONEY,
            verdict="high_risk",
            tools=[("identify_contract", {"address": HONEY}), ("simulate_sell", {"token": HONEY})],
        ),
        make_trace(address=PLAIN, verdict="no_red_flags_found"),
        make_trace(address=USDC, verdict="elevated_risk"),
    ]
    for number, trace in enumerate(traces):
        (folder / f"{number}.json").write_text(json.dumps(trace))
    return cards, folder


def test_scoring_a_set_with_cards_gives_every_layer_and_says_the_labels_are_unreviewed(tmp_path):
    cards_path, folder = write_set(tmp_path)
    runs, _ = load_runs([folder])
    results = score_module.score(runs, load_cards(cards_path), resamples=200)
    json.dumps(results)  # the document is plain JSON
    assert results["manifest"]["unreviewed_labels"] == "3 of 3" and results["manifest"]["notes"]
    assert results["manifest"]["carded_runs"] == 3 and results["manifest"]["git_commits"] == [
        "abc12345"
    ]
    detection = results["detection"]
    assert detection["confusion"]["tp"] == 1 and detection["confusion"]["tn"] == 1
    assert detection["precision"]["value"] == 1.0 and detection["recall"]["value"] == 1.0
    assert detection["grey"]["usdc"] == {"runs": 1, "not_checked_ok": 1, "forbidden": 0}
    assert results["tool_use"]["pass"]["contracts"] == 3
    assert results["answer_quality"]["citations"]["first_attempt_accepted"]["value"] == 1.0
    assert results["answer_quality"]["faithfulness"] is None
    assert {c["id"] for c in results["per_contract"]} == {"honey", "plain", "usdc"}


def test_few_flagged_contracts_are_called_out_in_the_notes_of_every_result(tmp_path):
    cards_path, folder = write_set(tmp_path)
    runs, _ = load_runs([folder])
    cards = load_cards(cards_path)
    notes = score_module.score(runs, cards, resamples=50)["manifest"]["notes"]
    assert any("rests on 1 flagged contract " in n for n in notes)
    held = score_module.score(runs, cards, split="heldout", declared="x", resamples=50)
    assert any("rests on no flagged contract" in n for n in held["manifest"]["notes"])
    assert not any(
        "flagged contract" in n for n in score_module.score(runs, None)["manifest"]["notes"]
    )


def test_the_held_out_split_needs_a_declared_reason_and_leaves_out_per_contract_detail(tmp_path):
    cards_path, folder = write_set(tmp_path)
    runs, _ = load_runs([folder])
    cards = load_cards(cards_path)
    with pytest.raises(ValueError, match="declared reason"):
        score_module.score(runs, cards, split="heldout", resamples=50)
    held = score_module.score(runs, cards, split="heldout", declared="shakedown", resamples=50)
    assert "per_contract" not in held and held["manifest"]["traces"] == 1
    assert held["manifest"]["held_out_declared"] == "shakedown"
    dev = score_module.score(runs, cards, split="dev", resamples=50)
    assert dev["manifest"]["traces"] == 2 and "per_contract" in dev


def test_without_cards_only_the_label_free_metrics_are_computed(tmp_path):
    _, folder = write_set(tmp_path)
    runs, _ = load_runs([folder])
    results = score_module.score(runs, resamples=100)
    assert results["detection"] is None and "pass" not in results["tool_use"]
    assert results["tool_use"]["hygiene_only"]["runs"] == 3
    assert "time" in results and "cost" in results


def test_several_models_pooled_are_flagged_and_one_can_be_chosen(tmp_path):
    runs = [run_of(model="a/one"), run_of(model="a/one"), run_of(model="b/two")]
    pooled = score_module.score(runs, resamples=50)
    assert pooled["manifest"]["models"] == {"a/one": 2, "b/two": 1}
    assert any("pooled" in note for note in pooled["manifest"]["notes"])
    one = score_module.score(runs, model="a/one", resamples=50)
    assert one["manifest"]["models"] == {"a/one": 2} and one["manifest"]["notes"] == []
    assert list(one["cost"]) == ["a/one"]


def test_the_command_prints_the_report_and_saves_the_file(tmp_path, capsys):
    cards, folder = write_set(tmp_path)
    out = tmp_path / "out"
    argv = [
        str(folder),
        "--cards",
        str(cards),
        "--split",
        "dev",
        "--out",
        str(out),
        "--resamples",
        "100",
    ]
    assert score_module.main(argv) == 0
    shown = capsys.readouterr().out
    assert "scored 2 traces" in shown and "3 of 3 unreviewed" in shown and "detection" in shown
    assert len(list(out.glob("*.json"))) == 1


def test_the_command_refuses_the_held_out_split_without_a_declared_reason(tmp_path, capsys):
    cards, folder = write_set(tmp_path)
    assert score_module.main([str(folder), "--cards", str(cards), "--held-out"]) == 1
    assert "--declare" in capsys.readouterr().err
    assert score_module.main([str(folder), "--split", "dev"]) == 1
    assert score_module.main([str(tmp_path / "nothing")]) == 1


# ---- a trace written by a real (scripted) agent run ----------------------------------------


def test_a_trace_written_by_the_agent_is_scored_end_to_end(tmp_path):
    world = erc20_world()
    model = ScriptedModel(
        [
            [call("identify_contract", address=TOKEN), call("list_selectors", address=TOKEN)],
            [submit(**report_body())],
        ]
    )
    agent_run = run_agent(
        model, RunContext(world, world.chain, services=offline_services()), TOKEN, limits=Limits()
    )
    path = write_trace(agent_run, Limits(), tmp_path)
    run = load_trace(path)
    assert run.tools == ("identify_contract", "list_selectors") and run.has_timing
    assert run.attempts[0].accepted and run.metrics["counters"]["code_reads"] == 1
    assert run.calls[0].result_sha and run.environment["cpu_count"]
    results = score_module.score([run], resamples=100)
    assert results["answer_quality"]["citations"]["first_attempt_accepted"]["value"] == 1.0
    assert results["time"]["scripted"]["used"] == 1
    text = score_module.format_report(results)
    assert "scored 1 traces" in text and "tool use" in text
    # the same run with the new keys stripped is scored the same way, with the attempts rebuilt
    data = as_old_trace(json.loads(path.read_text()))
    old = parse_trace(data)
    assert not old.has_timing and rebuild_attempts(old)[0].accepted
