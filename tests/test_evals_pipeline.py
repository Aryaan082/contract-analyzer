"""Part E of Component 6: `make eval`, `make eval-run`, `make eval-heldout`, the spend rules,
run-sets and their summaries, and the parked CI workflow. Everything runs on scripted agent runs and
a scripted judge: no network, no money, no model."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from evals import pipeline, spend
from evals.generate import RunResult
from evals.judge.rubric import load_rubric
from evals.runs import parse_trace
from evals.runset import (
    build_summary,
    describe_dataset,
    differences,
    list_runsets,
    run_from_dict,
    run_to_dict,
)
from evals.table import NO_RUNSET
from tests.evals_pipeline_support import (
    ADDRESSES,
    EVAL_MODEL,
    forbidden_runner,
    generate_run_set,
    make_where,
    retrieval_spy,
    scripted_runner,
)
from tests.judge_support import FakeJudge, oracle

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def where(tmp_path):
    return make_where(tmp_path)


# -- the estimate -------------------------------------------------------------------------------


def test_the_estimate_is_runs_times_the_measured_cost_per_run():
    est = spend.estimate_generation(EVAL_MODEL, 87)
    assert est.agent_usd == pytest.approx(87 * 0.0056) and est.judge_usd == 0
    assert est.total == est.agent_usd and est.ceiling == 0.05
    assert est.seconds == pytest.approx(87 * 42.0)


def test_a_judge_adds_one_call_per_claim_and_one_per_summary_at_its_measured_price():
    est = spend.estimate_generation(EVAL_MODEL, 10, ("qwen/qwen3.7-flash", "z-ai/glm-5.3-flash"))
    calls = -(-10 * (12.1 + 1) // 1)  # 131 calls for each judge
    assert est.judge_calls == 2 * calls
    assert est.judge_usd == pytest.approx(calls * (0.046 / 103 + 0.098 / 103))
    assert est.total == pytest.approx(est.agent_usd + est.judge_usd)


def test_a_model_or_judge_with_no_measured_cost_has_no_estimate_unless_one_is_given():
    with pytest.raises(spend.UnknownCost):
        spend.estimate_generation("vendor/unknown", 5)
    with pytest.raises(spend.UnknownCost):
        spend.estimate_generation(EVAL_MODEL, 5, ("vendor/judge",))
    given = spend.estimate_generation("vendor/unknown", 5, per_run_usd=0.01)
    assert given.agent_usd == pytest.approx(0.05) and "not measured" in given.notes[0]
    assert spend.estimate_generation(EVAL_MODEL, 5, ("v/j",), judge_call_usd=0.01).judge_usd > 0


def test_sonnet_is_expensive_and_the_eval_tier_is_not():
    assert spend.estimate_generation("anthropic/claude-sonnet-5.5", 3).expensive
    assert not spend.estimate_generation(EVAL_MODEL, 100).expensive
    assert not spend.estimate_generation("anthropic/claude-haiku-5.5", 100).expensive


def test_every_cost_figure_says_where_it_comes_from():
    assert all(c.source and c.typical < c.ceiling for c in spend.AGENT_COSTS.values())
    assert set(spend.JUDGE_CALL_USD) == set(spend.JUDGE_PASS)
    assert {"qwen/qwen3.7-flash", "z-ai/glm-5.3-flash"} <= set(spend.JUDGE_PASS)
    assert spend.JUDGE_CALL_USD["z-ai/glm-5.3-flash"] == pytest.approx(0.098 / 103)


# -- the rules before any spending --------------------------------------------------------------


def decide(est=None, **kw):
    est = est or spend.estimate_generation(EVAL_MODEL, 30)
    args = {"go": True, "max_spend": 4.0, "allow_expensive": False, "declared": True, "kind": "dev"}
    return spend.decide(est, **{**args, **kw})


def test_without_go_nothing_proceeds_and_the_words_say_what_to_add():
    result = decide(go=False)
    assert (
        not result.proceed and "nothing was spent" in result.lines[0] and "--go" in result.lines[0]
    )


def test_a_cap_below_the_estimate_refuses_and_says_how_much_is_needed():
    est = spend.estimate_generation(EVAL_MODEL, 1000)  # $5.60
    result = decide(est, max_spend=4.0)
    assert not result.proceed and "does not cover" in result.lines[0]
    assert "--max-spend 5.6" in result.lines[0]
    assert decide(est, max_spend=6.0).proceed


def test_sonnet_needs_allow_expensive_even_with_go_and_a_big_enough_cap():
    est = spend.estimate_generation("anthropic/claude-sonnet-5.5", 30)
    refused = decide(est, max_spend=50.0)
    assert not refused.proceed and "--allow-expensive" in refused.lines[0]
    assert decide(est, max_spend=50.0, allow_expensive=True).proceed


def test_held_out_is_refused_without_a_declared_reason():
    assert not decide(kind="heldout", declared=False).proceed
    assert decide(kind="heldout", declared=True).proceed


def test_the_default_caps_are_four_dollars_for_dev_and_two_for_held_out():
    assert (spend.DEV_CAP_USD, spend.HELDOUT_CAP_USD) == (4.0, 2.0)


# -- a bare eval-run spends nothing -------------------------------------------------------------


def test_a_bare_generate_prints_the_estimate_and_starts_nothing(where, capsys):
    code = pipeline.main(where.argv("generate"), make_runner=forbidden_runner)
    shown = capsys.readouterr().out
    assert code == 0 and "estimate: 9 runs" in shown and "nothing was spent" in shown
    assert "add --go" in shown and 'make eval-run ARGS="--go"' in shown
    assert not where.runsets.exists() and spend.read_ledger(where.runsets) == []


def test_go_with_a_cap_below_the_estimate_is_refused_and_starts_nothing(where, capsys):
    code = pipeline.main(
        where.argv("generate", "--go", "--max-spend", "0.01"), make_runner=forbidden_runner
    )
    assert code == 2 and "does not cover the estimate" in capsys.readouterr().out
    assert not where.runsets.exists()


def test_sonnet_with_go_and_a_cap_but_no_allow_expensive_starts_nothing(where, capsys):
    code = pipeline.main(
        where.argv("generate", "--model", "sonnet", "--go", "--max-spend", "20"),
        make_runner=forbidden_runner,
    )
    assert code == 2 and "--allow-expensive" in capsys.readouterr().out
    assert not where.runsets.exists()


def test_a_judge_is_never_started_unless_it_is_named(where):
    assert generate_run_set(where) == 0
    assert list(where.cache.glob("*")) == []  # forbidden_judge would have raised


def test_the_help_names_the_two_judge_modes_and_says_there_is_no_default():
    text = pipeline.build_parser()._subparsers._group_actions[0].choices["generate"].format_help()
    flat = " ".join(text.split())
    assert "quick" in flat and "milestone" in flat and "z-ai/glm-5.3-flash" in flat
    assert "qwen/qwen3.7-flash" in flat and "no default" in flat and "not approved" in flat


def test_a_judge_that_is_not_listed_is_refused_before_anything_runs(where, capsys):
    code = pipeline.main(
        where.argv(
            "generate",
            "--go",
            "--max-spend",
            "1",
            "--judge",
            "vendor/judge",
            "--judge-call-usd",
            "0.001",
        ),
        make_runner=forbidden_runner,
    )
    assert code == 2 and "not in the judge policy" in capsys.readouterr().out


# -- a run-set is made, within the cap ----------------------------------------------------------


def test_go_makes_a_run_set_with_a_manifest_traces_and_a_ledger_entry(where, capsys):
    assert generate_run_set(where, "--repeats", "2") == 0
    [runset] = list_runsets(where.runsets)
    assert len(runset.trace_files()) == 6
    m = runset.manifest
    assert (m["kind"], m["repeats"], m["planned_runs"], m["completed_runs"]) == ("dev", 2, 6, 6)
    assert m["status"] == "complete" and m["model"] == {"tier": "eval", "slug": EVAL_MODEL}
    assert m["judges"] == {"slugs": [], "rubric_sha256": None}
    assert m["dataset"]["block_pins"] == {"c1": 100, "c2": 100, "c3": 100}
    assert (
        m["hashes"]["dataset_sha256"]
        == describe_dataset(
            where.cards,
            None or __import__("evals.cards", fromlist=["load_cards"]).load_cards(where.cards),
            "dev",
        )["dataset_sha256"]
    )
    assert "git_commit" in m and "git_dirty" in m and "environment" in m
    assert m["spend"]["spent_usd"] == pytest.approx(0.024)
    [row] = spend.read_ledger(where.runsets)
    assert row["runs_done"] == 6 and row["spent_usd"] == pytest.approx(0.024)
    assert row["estimated_usd"] == pytest.approx(6 * 0.0056)
    assert "next: python -m evals.run all --runset" in capsys.readouterr().out


def test_runs_go_repeat_by_repeat_and_each_gets_the_per_run_cap_and_tags(where):
    make = scripted_runner()
    assert generate_run_set(where, "--repeats", "2", make=make) == 0
    order = [(cid, tags["repeat"]) for cid, _, tags in make.calls]
    assert order == [("c1", 1), ("c2", 1), ("c3", 1), ("c1", 2), ("c2", 2), ("c3", 2)]
    assert all(ceiling == 0.05 for _, ceiling, _ in make.calls)
    assert all(tags["concurrent"] is False for *_, tags in make.calls)


def test_the_cap_stops_the_run_set_before_it_is_passed(where, capsys):
    make = scripted_runner(cost=0.05)  # every run costs its whole ceiling
    code = pipeline.main(where.argv("generate", "--go", "--max-spend", "0.12"), make_runner=make)
    shown = capsys.readouterr().out
    assert code == 3 and "STOPPED: spend_cap" in shown
    assert len(make.calls) == 2
    [runset] = list_runsets(where.runsets)
    assert runset.manifest["status"] == "stopped" and runset.manifest["completed_runs"] == 2
    [row] = spend.read_ledger(where.runsets)
    assert row["spent_usd"] == pytest.approx(0.10) and row["spent_usd"] <= 0.12


def test_runs_that_keep_failing_end_the_run_set_early_and_cost_nothing(where, capsys):
    def make(_traces):
        return lambda card, ceiling, tags: RunResult(None, 0.0, error="rpc_unreachable")

    code = pipeline.main(where.argv("generate", "--go", "--max-spend", "1"), make_runner=make)
    shown = capsys.readouterr().out
    assert code == 3 and "STOPPED: failures" in shown and "rpc_unreachable" in shown
    assert spend.read_ledger(where.runsets)[0]["spent_usd"] == 0.0


def test_workers_mark_the_run_set_and_the_traces_concurrent(where):
    make = scripted_runner()
    assert generate_run_set(where, "--workers", "3", make=make) == 0
    assert all(tags["concurrent"] is True for *_, tags in make.calls)
    assert list_runsets(where.runsets)[0].manifest["concurrent"] is True


def test_a_named_judge_runs_after_the_agent_inside_the_same_cap(where):
    judge = FakeJudge(oracle(), cost=0.0004)
    code = pipeline.main(
        where.argv("generate", "--go", "--max-spend", "1", "--judge", "qwen/qwen3.7-flash"),
        make_runner=scripted_runner(),
        make_judge=lambda spec: judge,
    )
    # identical claims in the repeats are one judge call each, so there are fewer calls than reports
    assert code == 0 and 0 < len(judge.calls) <= 18
    [runset] = list_runsets(where.runsets)
    assert runset.manifest["judges"] == {
        "slugs": ["qwen/qwen3.7-flash"],
        "rubric_sha256": load_rubric().sha256,
        "mode": "custom",
        "label": "single judge, not for quoting",
        "quotable": False,
        "rubric_approved": False,
        "rubric_notice": "rubric draft, not approved by Aryaan",
    }
    assert runset.manifest["spend"]["spent_usd"] == pytest.approx(
        9 * 0.004 + len(judge.calls) * 0.0004
    )
    assert spend.read_ledger(where.runsets)[0]["judges"] == ["qwen/qwen3.7-flash"]


def test_the_ledger_sums_what_was_spent_against_the_envelope(where, capsys):
    generate_run_set(where)
    generate_run_set(where)
    capsys.readouterr()
    assert pipeline.main(where.argv("ledger")) == 0
    shown = capsys.readouterr().out
    assert "total through evals.run: $0.0720" in shown and "roughly $25 envelope" in shown


# -- the real runner, with the chain and the model replaced -------------------------------------


def test_the_real_runner_pins_the_block_caps_the_run_and_writes_the_trace(tmp_path, monkeypatch):
    """The one path that spends money, run with a scripted chain and a scripted model."""
    from agent.config import load_config
    from evals.cards import Card
    from evals.generate import live_agent_runner
    from tests.agent_support import (
        TOKEN,
        ScriptedModel,
        call,
        erc20_world,
        offline_services,
        report_body,
        submit,
    )

    world = erc20_world()
    asked = {}
    seen_limits = []

    def connect(chain, env, block=None):
        asked["block"] = block
        return world

    def scripted_model(spec, timeout):
        model = ScriptedModel(
            [[call("identify_contract", address=TOKEN)], [submit(**report_body())]], cost=0.007
        )
        asked["model"] = spec.name
        seen_limits.append(timeout)
        return model

    monkeypatch.setattr("chain.rpc.RpcClient.connect", staticmethod(connect))
    monkeypatch.setattr("agent.llm.OpenRouterModel", scripted_model)
    monkeypatch.setattr(
        "chain.profile.Services.from_env", staticmethod(lambda env: offline_services())
    )
    runner = live_agent_runner("eval", tmp_path, {})
    card = Card.model_validate(
        {"id": "x", "address": TOKEN, "class": "benign", "block": world.block, "split": "dev"}
    )
    result = runner(card, 0.05, {"runset": "t", "repeat": 2, "concurrent": False})
    assert asked == {"block": world.block, "model": load_config().resolve_model("eval").name}
    assert result.accepted and result.error is None and result.cost_usd == pytest.approx(0.014)
    saved = json.loads(result.trace.read_text())
    assert saved["tags"] == {"runset": "t", "repeat": 2, "concurrent": False}
    assert saved["limits"]["max_cost_usd"] == 0.05  # the ceiling reached the agent loop


# -- the layer table, with and without a run-set ------------------------------------------------


def test_make_eval_with_no_run_set_says_so_and_still_runs_retrieval(where, capsys):
    spy = retrieval_spy()
    code = pipeline.main(where.argv("all"), retrieval=spy)
    shown = capsys.readouterr().out
    assert code == 0 and len(spy.seen) == 1 and spy.seen[0].held_out is False
    assert shown.count(NO_RUNSET) == 5  # tool use, detection, answer quality, time, cost
    assert "retrieval[dense-small]" in shown and "0.84 [0.76-0.92] / 0.80 [0.72-0.88]" in shown
    assert not where.results.exists()  # nothing was scored, so no results file


def test_make_eval_with_a_run_set_prints_every_layer_and_saves_a_versioned_results_file(
    where, capsys
):
    generate_run_set(where, "--repeats", "2")
    capsys.readouterr()
    assert pipeline.main(where.argv("all"), retrieval=retrieval_spy()) == 0
    shown = capsys.readouterr().out
    for layer in (
        "retrieval[dense-small]",
        "tool use",
        "detection",
        "answer quality",
        "time[",
        "cost[",
    ):
        assert layer in shown
    assert NO_RUNSET not in shown and "faithfulness" in shown and "not judged" in shown
    [written] = list(where.results.glob("*.json"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-abc12345-dev-eval-r2\.json", written.name)
    saved = json.loads(written.read_text())
    assert saved["schema_version"] == 1 and saved["manifest"]["runset"]["name"] == "dev-eval-r2"
    assert saved["manifest"]["runset"]["hashes"]["dataset_sha256"]
    assert saved["manifest"]["split"] == "dev" and len(saved["per_contract"]) == 3
    assert saved["detection"]["precision"] is None or "value" in saved["detection"]["precision"]
    assert "unreviewed" in shown  # the counter is still printed


def test_scoring_stops_if_the_cards_changed_after_the_run_set_was_made(where, capsys):
    generate_run_set(where)
    where.cards.write_text(where.cards.read_text() + "\n# edited\n")
    capsys.readouterr()
    assert pipeline.main(where.argv("all", "--no-retrieval")) == 1
    assert "the cards or the label rules changed" in capsys.readouterr().out
    assert pipeline.main(where.argv("all", "--no-retrieval", "--ignore-dataset-change")) == 0


def test_a_run_set_can_be_named_and_the_newest_is_the_default(where, capsys):
    generate_run_set(where, "--name", "first")
    generate_run_set(where, "--name", "second")
    capsys.readouterr()
    assert pipeline.main(where.argv("all", "--no-retrieval")) == 0
    assert any("second" in p.name for p in where.results.glob("*.json"))
    assert (
        pipeline.main(where.argv("all", "--no-retrieval", "--runset", str(where.runsets / "first")))
        == 0
    )
    assert any("first" in p.name for p in where.results.glob("*.json"))


# -- held-out: declared, totals only ------------------------------------------------------------


def test_held_out_is_refused_without_declare_and_with_a_dev_cards_file(where, capsys):
    assert pipeline.main(where.argv("heldout", cards=where.held), retrieval=retrieval_spy()) == 2
    assert "--declare" in capsys.readouterr().out
    code = pipeline.main(
        where.argv("heldout", "--declare", "a milestone", cards=where.cards),
        retrieval=retrieval_spy(),
    )
    assert code == 2 and "not in the heldout split" in capsys.readouterr().out
    assert pipeline.main(["heldout", "--declare", "x"]) == 2  # no --cards at all


def test_held_out_with_no_run_set_runs_retrieval_totals_and_says_so(where, capsys):
    spy = retrieval_spy()
    code = pipeline.main(
        where.argv("heldout", "--declare", "milestone 1", cards=where.held), retrieval=spy
    )
    shown = capsys.readouterr().out
    assert code == 0 and spy.seen[0].held_out is True
    assert "no heldout run-set yet" in shown


def test_a_held_out_run_set_is_scored_in_totals_with_no_contract_named_anywhere(where, capsys):
    spy = retrieval_spy()
    code = pipeline.main(
        where.argv(
            "heldout",
            "--declare",
            "milestone 1",
            "--generate",
            "--go",
            "--max-spend",
            "1",
            cards=where.held,
        ),
        make_runner=scripted_runner(),
        retrieval=spy,
    )
    shown = capsys.readouterr().out
    assert code == 0 and spy.seen[0].held_out is True
    [written] = list(where.results.glob("*.json"))
    saved = json.loads(written.read_text())
    assert "per_contract" not in saved and saved["manifest"]["held_out_declared"] == "milestone 1"
    [runset] = list_runsets(where.runsets)
    files = [runset.path / "manifest.json", written, spend.ledger_path(where.runsets)]
    for text in [f.read_text() for f in files] + [shown]:
        for cid, address in ADDRESSES.items():
            assert address not in text and f'"{cid}"' not in text
    manifest = json.loads((runset.path / "manifest.json").read_text())
    assert "block_pins" not in manifest["dataset"] and "cards_file" not in manifest["dataset"]
    assert manifest["dataset"]["contracts"] == 3 and manifest["kind"] == "heldout"
    assert manifest["declared"] == "milestone 1"
    assert not where.summaries.exists()  # a held-out run-set never gets a summary


def test_no_summary_is_written_for_a_held_out_run_set_even_if_asked(where):
    pipeline.main(
        where.argv(
            "heldout",
            "--declare",
            "x",
            "--generate",
            "--go",
            "--max-spend",
            "1",
            "--write-summary",
            cards=where.held,
        ),
        make_runner=scripted_runner(),
        retrieval=retrieval_spy(),
    )
    assert not where.summaries.exists()
    [runset] = list_runsets(where.runsets)
    runs = [parse_trace(json.loads(p.read_text())) for p in runset.trace_files()]
    with pytest.raises(ValueError, match="only a dev run-set"):
        build_summary(runset.manifest, runs, None, None)


def test_held_out_generation_defaults_to_the_two_dollar_cap(where, capsys):
    pipeline.main(
        where.argv(
            "heldout", "--declare", "x", "--generate", "--model", "sonnet", cards=where.held
        ),
        make_runner=forbidden_runner,
        retrieval=retrieval_spy(),
    )
    assert "spend cap: $2.00" in capsys.readouterr().out


# -- the summary, and scoring it again ----------------------------------------------------------


def make_milestone(where, *extra):
    generate_run_set(where, "--repeats", "2", *extra)
    assert pipeline.main(where.argv("all", "--no-retrieval", "--write-summary")) == 0


def test_a_summary_scored_again_gives_exactly_the_committed_results(where, capsys):
    make_milestone(where)
    [summary] = list(where.summaries.glob("*.json"))
    capsys.readouterr()
    assert pipeline.main(where.argv("check-summaries")) == 0
    shown = capsys.readouterr().out
    assert "ok" in shown and "exactly the committed results file" in shown
    assert "tool use" in shown and "time[" in shown  # the table, timing reported
    data = json.loads(summary.read_text())
    assert "messages" not in json.dumps(data) and data["manifest"]["kind"] == "dev"


def test_a_summary_with_a_judge_keeps_the_labels_and_still_reproduces_the_results(where):
    judge = FakeJudge(oracle({"The contract has a mint function.": "partly"}), cost=0.0004)
    generate_run_set(where, "--judge", "qwen/qwen3.7-flash", make_judge=lambda s: judge)
    assert pipeline.main(where.argv("all", "--no-retrieval", "--write-summary")) == 0
    [written] = list(where.results.glob("*.json"))
    block = json.loads(written.read_text())["answer_quality"]["faithfulness"]
    assert (
        block["judges"] == ["qwen/qwen3.7-flash"] and block["rubric_sha256"] == load_rubric().sha256
    )
    [summary] = list(where.summaries.glob("*.json"))
    assert json.loads(summary.read_text())["judge_labels"]["judges"]["qwen/qwen3.7-flash"]
    assert pipeline.main(where.argv("check-summaries")) == 0


def test_a_scoring_change_that_moves_a_number_fails_the_check(where, capsys):
    make_milestone(where)
    [written] = list(where.results.glob("*.json"))
    saved = json.loads(written.read_text())
    saved["cost"][next(iter(saved["cost"]))]["total_usd"] += 0.01
    written.write_text(json.dumps(saved))
    capsys.readouterr()
    assert pipeline.main(where.argv("check-summaries")) == 1
    shown = capsys.readouterr().out
    assert "FAIL" in shown and "total_usd" in shown


def test_a_summary_that_was_edited_fails_the_check(where):
    make_milestone(where)
    [summary] = list(where.summaries.glob("*.json"))
    data = json.loads(summary.read_text())
    data["runs"][0]["verdict"] = "insufficient_evidence"
    summary.write_text(json.dumps(data))
    assert pipeline.main(where.argv("check-summaries")) == 1


def test_a_summary_without_its_committed_results_file_fails(where, capsys):
    make_milestone(where)
    for f in where.results.glob("*.json"):
        f.unlink()
    assert pipeline.main(where.argv("check-summaries")) == 1
    assert "is not committed" in capsys.readouterr().out


def test_with_no_committed_summary_the_check_says_so_and_passes(where, capsys):
    assert pipeline.main(where.argv("check-summaries")) == 0
    assert "no committed summaries yet" in capsys.readouterr().out


def test_a_run_survives_the_trip_through_a_summary_unchanged_for_the_scorers(where):
    generate_run_set(where)
    [runset] = list_runsets(where.runsets)
    original = parse_trace(
        json.loads(runset.trace_files()[0].read_text()), runset.trace_files()[0].name
    )
    again = run_from_dict(json.loads(json.dumps(run_to_dict(original))))
    assert (again.verdict, again.cost_usd, again.tools, again.attempts, again.findings) == (
        original.verdict,
        original.cost_usd,
        original.tools,
        original.attempts,
        original.findings,
    )


def test_the_comparison_ignores_the_time_of_writing_and_nothing_else():
    a = {"created_at": "x", "n": 1, "rows": [{"v": 0.5}], "only": 1}
    b = {"created_at": "y", "n": 1, "rows": [{"v": 0.5000001}]}
    assert differences(a, b) == ["/only: present in only one", "/rows[0]/v: 0.5 against 0.5000001"]
    assert differences({"created_at": "x"}, {"created_at": "y"}) == []


# -- make ---------------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_shows_each_target_and_what_it_runs():
    out = subprocess.run(
        ["make", "-n", "eval", "eval-run", "eval-heldout", "DECLARE=a milestone"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "python -m evals.run all" in out
    assert "python -m evals.run generate" in out and "--go" not in out
    assert "python -m evals.run heldout --cards evals/datasets/contracts_heldout.toml" in out
    assert '--declare "a milestone"' in out


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_a_bare_make_eval_run_has_no_go_in_it_and_make_eval_has_no_paid_command():
    for target in ("eval", "eval-run"):
        out = subprocess.run(
            ["make", "-n", target], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
        assert "--go" not in out and "--max-spend" not in out
    makefile = (ROOT / "Makefile").read_text()
    assert "eval-run:\n\t$(UV) python -m evals.run generate $(ARGS)" in makefile
