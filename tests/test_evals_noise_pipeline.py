"""The noise study as a command: `python -m evals.run noise` and `noise-report`. Scripted agent
runs, no network, no money. The study is a small one built from the test cards, so its rule can be
recomputed here."""

import json

import pytest

from evals import pipeline, spend
from evals.cards import load_cards
from evals.noise_select import Decision, Registration, Study, choose, pool_of, population_sha256
from evals.runset import list_runsets
from tests.evals_pipeline_support import (
    ADDRESSES,
    CARD,
    forbidden_judge,
    forbidden_runner,
    generate_run_set,
    make_where,
    retrieval_spy,
    scripted_runner,
)

CLASSES = {"c1": "benign", "c2": "flagged", "c3": "grey"}
QUOTA = {"flagged": 1, "benign": 1, "grey": 1}
COMMITTED = Registration("abc1234567890abc1234567890abc1234567890", [])


@pytest.fixture
def where(tmp_path):
    dirs = make_where(tmp_path)
    dirs.noise_cards = tmp_path / "noise_cards.toml"
    dirs.noise_cards.write_text(
        "".join(
            CARD.format(id=i, address=a, klass=CLASSES[i], split="dev") + "tool_use = true\n"
            for i, a in ADDRESSES.items()
        )
    )
    return dirs


def study_of(where, ids=None):
    cards = load_cards(where.noise_cards)
    return Study(
        version=1,
        salt="s",
        repeats=2,
        named=(),
        quota=QUOTA,
        population_sha256=population_sha256(pool_of(cards, ())),
        ids=tuple(ids or choose(cards, salt="s", named=(), quota=QUOTA)),
        decision=Decision(0, 3, 3, 7, 5, 0.5),
    )


def noise(where, *rest, prereg=COMMITTED, make=forbidden_runner, judge=forbidden_judge, **kw):
    return pipeline.main(
        where.argv("noise", *rest, cards=where.noise_cards),
        study=kw.pop("study", None) or study_of(where),
        prereg=lambda: prereg,
        make_runner=make,
        make_judge=judge,
        **kw,
    )


def run_noise(where, *rest, **kw):
    return noise(where, "--go", "--max-spend", "1", *rest, make=scripted_runner(), **kw)


# -- the estimate, and what a run needs ---------------------------------------------------------


def test_the_estimate_is_printed_and_nothing_runs(where, capsys):
    assert noise(where) == 0
    shown = capsys.readouterr().out
    assert "registration: committed at abc123456789" in shown
    assert "noise run-set: 3 contracts x 2 repeats" in shown
    assert "estimate: 6 runs" in shown and "no judge (none was named)" in shown
    assert "nothing was spent. To run it, add --go and an explicit --max-spend." in shown
    assert 'make eval-noise ARGS="--go --max-spend <USD>"' in shown
    assert not where.runsets.exists()


def test_the_estimate_with_a_judge_adds_its_calls_and_names_the_label(where, capsys):
    assert noise(where, "--judge-mode", "quick") == 0
    shown = capsys.readouterr().out
    assert "plus 79 judge calls (z-ai/glm-5.3-flash)" in shown  # 6 runs x (12.1 claims + 1)
    assert "judging: quick (z-ai/glm-5.3-flash): single judge, not for quoting" in shown


def test_a_noise_run_needs_go_an_explicit_cap_and_a_committed_registration(where, capsys):
    assert noise(where, "--go") == 2
    assert "needs an explicit --max-spend" in capsys.readouterr().out
    dirty = Registration("abc", ["noise-study.md"])
    assert noise(where, "--go", "--max-spend", "1", prereg=dirty) == 2
    shown = capsys.readouterr().out
    assert "registration: NOT committed (noise-study.md)" in shown
    assert "commit the registration files first" in shown
    assert noise(where, "--go", "--max-spend", "0.001") == 2  # does not cover the estimate
    assert "does not cover the estimate" in capsys.readouterr().out
    assert not where.runsets.exists()


def test_an_uncommitted_registration_is_shown_even_without_go(where, capsys):
    assert noise(where, prereg=Registration(None, ["never committed"])) == 0
    assert "NOT committed (never committed)" in capsys.readouterr().out


def test_a_study_that_does_not_follow_its_rule_is_refused_before_anything_runs(where, capsys):
    wrong = study_of(where, ids=["c1", "c2", "c1"])
    assert noise(where, "--go", "--max-spend", "1", study=wrong) == 2
    assert "not what the rule gives" in capsys.readouterr().out
    assert not where.runsets.exists()


def test_the_contracts_and_repeats_cannot_be_changed_on_the_command_line(where):
    with pytest.raises(SystemExit):
        noise(where, "--repeats", "9")
    with pytest.raises(SystemExit):
        noise(where, "--workers", "4")
    with pytest.raises(SystemExit):
        noise(where, "--contracts", "c1")


# -- the run-set it makes ------------------------------------------------------------------------


def test_go_makes_a_run_set_of_its_own_kind_and_prints_the_analysis(where, capsys):
    assert run_noise(where) == 0
    shown = capsys.readouterr().out
    [runset] = list_runsets(where.runsets)
    m = runset.manifest
    assert (m["kind"], m["repeats"], m["planned_runs"], m["completed_runs"]) == ("noise", 2, 6, 6)
    assert m["dataset"]["contracts"] == 3 and set(m["dataset"]["block_pins"]) == set(ADDRESSES)
    assert m["concurrent"] is False and m["status"] == "complete"
    registered = m["noise_study"]
    assert registered["registered_commit"] == COMMITTED.commit
    assert registered["contracts"] == ["c1", "c2", "c3"] and len(registered["files_sha256"]) == 64
    assert registered["uncommitted"] == [] and registered["decision"]["abstain_max"] == 5
    assert runset.name.startswith("noise-")
    tags = [json.loads(p.read_text())["tags"] for p in runset.trace_files()]
    assert sorted((t["contract"], t["repeat"], t["concurrent"]) for t in tags)[0] == (
        "c1",
        1,
        False,
    )
    assert "NOISE STUDY: 6 runs of 6 planned, 3 of 3 contracts" in shown
    assert "Decision rule for R" in shown and "saved " in shown
    [saved] = list(where.results.glob("*.json"))
    document = json.loads(saved.read_text())
    assert document["manifest"]["kind"] == "noise" and document["noise"]["runs"] == 6
    ledger = spend.read_ledger(where.runsets)
    assert [(r["command"], r["kind"], r["runs_done"]) for r in ledger] == [("noise", "noise", 6)]


def test_the_runs_go_repeat_by_repeat_one_at_a_time(where):
    make = scripted_runner()
    assert noise(where, "--go", "--max-spend", "1", make=make) == 0
    assert [(c, t["repeat"], t["concurrent"]) for c, _, t in make.calls] == [
        ("c1", 1, False),
        ("c2", 1, False),
        ("c3", 1, False),
        ("c1", 2, False),
        ("c2", 2, False),
        ("c3", 2, False),
    ]


def test_the_cap_stops_a_noise_run_with_whole_repeats_and_says_so(where, capsys):
    code = noise(where, "--go", "--max-spend", "0.2", make=scripted_runner(cost=0.04))
    shown = capsys.readouterr().out
    [runset] = list_runsets(where.runsets)
    assert code == 3 and runset.manifest["status"] == "stopped"
    assert runset.manifest["stopped_because"] == "spend_cap" and "STOPPED: spend_cap" in shown
    assert runset.manifest["spend"]["spent_usd"] <= 0.2


# -- make eval never takes it for a dev run-set --------------------------------------------------


def test_make_eval_scores_the_dev_run_set_even_when_a_noise_run_set_is_newer(where, capsys):
    assert generate_run_set(where, "--repeats", "1") == 0
    assert run_noise(where) == 0
    assert [r.kind for r in list_runsets(where.runsets)] == ["dev", "noise"]
    capsys.readouterr()
    assert pipeline.main(where.argv("all", "--no-retrieval")) == 0
    shown = capsys.readouterr().out
    [saved] = [p for p in where.results.glob("*.json") if "dev-" in p.name]
    assert json.loads(saved.read_text())["manifest"]["runset"]["kind"] == "dev"
    assert "NOISE STUDY" not in shown


def test_make_eval_with_only_a_noise_run_set_says_there_is_no_dev_run_set(where, capsys):
    assert run_noise(where) == 0
    capsys.readouterr()
    code = pipeline.main(where.argv("all"), retrieval=retrieval_spy())
    shown = capsys.readouterr().out
    assert code == 0 and "no dev run-set yet" in shown


def test_a_noise_run_set_named_by_hand_is_refused_by_the_other_commands(where, capsys):
    assert run_noise(where) == 0
    [runset] = list_runsets(where.runsets)
    capsys.readouterr()
    assert pipeline.main(where.argv("all", "--no-retrieval", "--runset", str(runset.path))) == 2
    assert "is a noise run-set, not a dev one: python -m evals.run noise-report" in (
        capsys.readouterr().out
    )
    held = where.argv("heldout", "--declare", "why", "--no-retrieval", cards=where.held)
    assert pipeline.main([*held, "--runset", str(runset.path)]) == 2
    assert pipeline.main(where.argv("disagreements", "--runset", str(runset.path))) == 2
    assert "is a noise run-set, not a dev one" in capsys.readouterr().out


def test_a_noise_run_set_never_gets_a_committed_summary(where):
    assert run_noise(where) == 0
    assert not where.summaries.exists()
    assert pipeline.main(where.argv("check-summaries")) == 0


# -- the report ----------------------------------------------------------------------------------


def test_the_report_can_be_made_again_for_a_stored_run_set(where, capsys):
    assert run_noise(where) == 0
    capsys.readouterr()
    code = pipeline.main(where.argv("noise-report", cards=where.noise_cards), study=study_of(where))
    shown = capsys.readouterr().out
    assert code == 0 and "NOISE STUDY: 6 runs of 6 planned" in shown
    assert "Verdict" in shown and "Tool set changed" in shown and "Why runs differ" in shown


def test_the_report_with_no_noise_run_set_says_so(where, capsys):
    assert pipeline.main(where.argv("noise-report", cards=where.noise_cards)) == 0
    assert "no noise run-set yet" in capsys.readouterr().out


def test_the_report_refuses_cards_that_changed_since_the_run(where, capsys):
    assert run_noise(where) == 0
    capsys.readouterr()
    where.noise_cards.write_text(where.noise_cards.read_text() + "\n# edited later\n")
    argv = where.argv("noise-report", cards=where.noise_cards)
    assert pipeline.main(argv, study=study_of(where)) == 1
    assert "cards or the label rules changed" in capsys.readouterr().out
    assert pipeline.main([*argv, "--ignore-dataset-change"], study=study_of(where)) == 0


def test_the_report_says_when_the_registration_files_changed_after_the_run(where, capsys):
    assert run_noise(where) == 0
    [runset] = list_runsets(where.runsets)
    manifest = json.loads((runset.path / "manifest.json").read_text())
    manifest["noise_study"]["files_sha256"] = "0" * 64
    (runset.path / "manifest.json").write_text(json.dumps(manifest))
    capsys.readouterr()
    argv = where.argv("noise-report", cards=where.noise_cards)
    assert pipeline.main(argv, study=study_of(where)) == 0
    assert "differ from what was registered" in capsys.readouterr().out


# -- the subset fix: a run of some of the cards plans only those ---------------------------------


def test_a_run_of_some_contracts_plans_and_describes_only_those(where):
    assert generate_run_set(where, "--repeats", "2", "--contracts", "c1,c2") == 0
    [runset] = list_runsets(where.runsets)
    m = runset.manifest
    assert (m["planned_runs"], m["completed_runs"], m["dataset"]["contracts"]) == (4, 4, 2)
    assert set(m["dataset"]["block_pins"]) == {"c1", "c2"}
