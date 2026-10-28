"""The judge policy (evals/judge/policy.toml): one judge for quick runs, two independent judges for
milestone runs and any number that will be quoted, and a judge never grades an agent from its own
company. Scripted agent runs and judges; no network, no money."""

import json
import math

import pytest

from evals import pipeline, spend
from evals.generate import judge_runset
from evals.judge import cli as judge_cli
from evals.judge.cache import JudgeCache
from evals.judge.policy import (
    company,
    label,
    load_policy,
    policy_block,
    resolve_judges,
    self_grading,
)
from evals.judge.rubric import load_rubric
from evals.judge.runner import SpendCap
from evals.runset import list_runsets, load_runset
from tests.evals_pipeline_support import (
    EVAL_MODEL,
    forbidden_judge,
    forbidden_runner,
    generate_run_set,
    make_where,
    scripted_runner,
)
from tests.evals_support import make_trace
from tests.judge_support import FakeJudge, oracle

GLM, QWEN = "z-ai/glm-5.3-flash", "qwen/qwen3.7-flash"
DEEPSEEK = "deepseek/deepseek-v4.1-flash-1017"
SONNET = "anthropic/claude-sonnet-5.5"
POLICY = load_policy()


@pytest.fixture
def where(tmp_path):
    return make_where(tmp_path)


def two_judges(glm_says=None, qwen_says=None):
    """A make_judge whose two judges answer by their own dictionaries."""
    made = {
        GLM: FakeJudge(oracle(glm_says or {}), name=GLM, cost=0.0004),
        QWEN: FakeJudge(oracle(qwen_says or {}), name=QWEN, cost=0.0004),
        DEEPSEEK: FakeJudge(oracle(), name=DEEPSEEK, cost=0.0004),
    }
    return (lambda spec: made[spec.slug]), made


def sonnet_run(where, *judge_flags, make_judge=forbidden_judge):
    return pipeline.main(
        where.argv(
            "generate", "--go", "--max-spend", "5", "--model", "sonnet", "--allow-expensive",
            *judge_flags,
        ),
        make_runner=scripted_runner(),
        make_judge=make_judge,
    )  # fmt: skip


# -- the policy as decided ----------------------------------------------------------------------


def test_the_modes_are_the_ones_aryaan_decided():
    assert POLICY.modes == {"quick": (GLM,), "milestone": (GLM, QWEN)}
    assert POLICY.extra_allowed == (DEEPSEEK,)


def test_the_rubric_is_not_approved_and_the_policy_says_so():
    assert POLICY.rubric_approved is False
    assert POLICY.rubric_notice == "rubric draft, not approved by Aryaan"


def test_every_judge_the_policy_names_is_priced_and_listed():
    from evals.judge.client import load_judges

    listed = set(load_judges())
    for judge in POLICY.known_judges:
        assert judge in spend.JUDGE_PASS, f"{judge} has no measured cost"
    assert {GLM, QWEN} <= listed  # the two that make the modes are in judges.toml


# -- who is whose company -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (DEEPSEEK, "deepseek/deepseek-v4-pro-0813", True),
        (QWEN, "alibaba/anything", True),
        ("z-ai/glm-5.3-flash", "zhipu/glm", True),
        (GLM, QWEN, False),
        (DEEPSEEK, SONNET, False),
        (SONNET, "anthropic/claude-haiku-5.5", True),
        ("somebody-new/model", "somebody-new/other", True),
        ("somebody-new/model", "somebody-else/model", False),
    ],
)
def test_a_company_is_the_prefix_of_the_slug_folded_into_its_group(a, b, same):
    assert (company(a, POLICY) == company(b, POLICY)) is same


# -- resolving the judges a command asked for ---------------------------------------------------


def test_no_mode_and_no_extra_means_no_judge():
    assert resolve_judges(None, [], POLICY) == ((), None)


def test_quick_is_one_judge_and_milestone_is_two():
    assert resolve_judges("quick", [], POLICY) == ((GLM,), "quick")
    assert resolve_judges("milestone", [], POLICY) == ((GLM, QWEN), "milestone")


def test_an_extra_is_added_once_and_only_if_the_policy_lists_it():
    assert resolve_judges("quick", [DEEPSEEK, GLM], POLICY)[0] == (GLM, DEEPSEEK)
    assert resolve_judges(None, [DEEPSEEK], POLICY)[0] == (DEEPSEEK,)
    with pytest.raises(ValueError, match="not in the judge policy"):
        resolve_judges("quick", ["xiaomi/mimo-v2.6-pro"], POLICY)
    with pytest.raises(ValueError, match="unknown judge mode"):
        resolve_judges("thorough", [], POLICY)


# -- a judge never grades its own company, in both directions -----------------------------------


def test_deepseek_cannot_judge_the_eval_tier_agent_which_is_deepseek():
    [line] = self_grading([DEEPSEEK], EVAL_MODEL, POLICY)
    assert "refused" in line and "same company" in line and DEEPSEEK in line


def test_an_anthropic_judge_could_never_grade_sonnet():
    assert self_grading(["anthropic/claude-haiku-5.5"], SONNET, POLICY)


def test_the_other_way_round_is_fine():
    assert self_grading([DEEPSEEK], SONNET, POLICY) == []
    assert self_grading([GLM, QWEN], EVAL_MODEL, POLICY) == []
    assert self_grading([GLM, QWEN], SONNET, POLICY) == []


def test_only_the_judge_from_the_agents_company_is_named_in_the_refusal():
    refused = self_grading([GLM, DEEPSEEK], EVAL_MODEL, POLICY)
    assert len(refused) == 1 and DEEPSEEK in refused[0] and GLM not in refused[0]


# -- the label a result carries -----------------------------------------------------------------


def test_one_judge_is_not_for_quoting_and_two_companies_may_be_quoted():
    assert label([GLM], POLICY) == ("single judge, not for quoting", False)
    assert label([GLM, QWEN], POLICY)[1] is True
    assert label([GLM, DEEPSEEK], POLICY)[1] is True
    assert label([DEEPSEEK, "deepseek/other"], POLICY)[1] is False  # two slugs, one company


def test_the_block_uses_the_rubric_flag_it_was_given_not_the_one_in_force_now():
    approved_now = policy_block([GLM], mode="quick", rubric_approved=True)
    assert approved_now["rubric_approved"] is True and approved_now["rubric_notice"] is None
    then = policy_block([GLM], mode="quick", rubric_approved=False)
    assert then["rubric_notice"] == "rubric draft, not approved by Aryaan"
    assert policy_block([GLM])["mode"] == "custom"


# -- in the commands ----------------------------------------------------------------------------


def test_deepseek_on_the_eval_tier_agent_is_refused_before_anything_starts(where, capsys):
    code = pipeline.main(
        where.argv("generate", "--go", "--max-spend", "1", "--judge", DEEPSEEK),
        make_runner=forbidden_runner,
        make_judge=forbidden_judge,
    )
    shown = capsys.readouterr().out
    assert code == 2 and "same company as the agent" in shown
    assert not where.runsets.exists() and spend.read_ledger(where.runsets) == []


def test_deepseek_may_judge_sonnet_as_an_extra_and_the_run_set_records_it(where, monkeypatch):
    from evals.judge.client import JudgeSpec, load_judges

    # judges.toml gets its deepseek entry on another branch; the test supplies one so that it does
    # not depend on which branch it runs on
    specs = {**load_judges(), DEEPSEEK: JudgeSpec(DEEPSEEK, 0.15, 0.60)}
    monkeypatch.setattr("evals.pipeline.load_judges", lambda: specs)
    monkeypatch.setattr("evals.generate.load_judges", lambda: specs)
    make, made = two_judges()
    assert sonnet_run(where, "--judge", DEEPSEEK, make_judge=make) == 0
    [runset] = list_runsets(where.runsets)
    assert runset.manifest["judges"]["slugs"] == [DEEPSEEK] and made[DEEPSEEK].calls
    assert runset.manifest["model"]["slug"] == SONNET


def test_a_judge_the_policy_does_not_list_is_refused(where, capsys):
    code = pipeline.main(
        where.argv("generate", "--go", "--max-spend", "1", "--judge", "xiaomi/mimo-v2.6-pro"),
        make_runner=forbidden_runner,
    )
    assert code == 2 and "not in the judge policy" in capsys.readouterr().out


def test_with_no_judge_mode_nothing_is_judged_and_the_estimate_says_so(where, capsys):
    pipeline.main(where.argv("generate"), make_runner=forbidden_runner)
    shown = capsys.readouterr().out
    assert "no judge (none was named)" in shown and "rubric draft" not in shown
    assert "--judge-mode quick|milestone" in shown


def test_the_estimate_prices_one_judge_or_two_and_says_how(where, capsys):
    pipeline.main(where.argv("generate", "--judge-mode", "quick"), make_runner=forbidden_runner)
    quick = capsys.readouterr().out
    pipeline.main(where.argv("generate", "--judge-mode", "milestone"), make_runner=forbidden_runner)
    both = capsys.readouterr().out
    calls = math.ceil(9 * (12.1 + 1))  # 118 calls a judge: claims + 1 a report, 9 reports
    one = calls * 0.098 / 103
    two = one + calls * 0.046 / 103
    assert f"plus {calls} judge calls (z-ai/glm-5.3-flash) = ${one:.3f}" in quick
    assert (
        f"plus {2 * calls} judge calls (z-ai/glm-5.3-flash, qwen/qwen3.7-flash) = ${two:.3f}"
        in both
    )
    for shown in (quick, both):
        assert "judge pricing:" in shown and "(claims in a report + 1) calls" in shown
        assert "$0.098 / 103 calls" in shown
        assert "note: rubric draft, not approved by Aryaan" in shown
    assert "judging: quick" in quick and "judging: milestone" in both
    assert "single judge, not for quoting" in quick
    assert "two or more independent judges" in both


def test_the_quick_estimate_is_cheaper_than_the_milestone_estimate():
    one = spend.estimate_generation(SONNET, 100, (GLM,))
    two = spend.estimate_generation(SONNET, 100, (GLM, QWEN))
    assert (
        one.judge_usd
        < two.judge_usd
        == pytest.approx(one.judge_usd + 100 * (6.1 + 1) * 0.046 / 103, rel=0.02)
    )


def test_a_quick_run_is_labelled_single_judge_not_for_quoting_in_the_results(where, capsys):
    make, made = two_judges()
    assert sonnet_run(where, "--judge-mode", "quick", make_judge=make) == 0
    [runset] = list_runsets(where.runsets)
    assert runset.manifest["judges"] == {
        "slugs": [GLM],
        "rubric_sha256": load_rubric().sha256,
        "mode": "quick",
        "label": "single judge, not for quoting",
        "quotable": False,
        "rubric_approved": False,
        "rubric_notice": "rubric draft, not approved by Aryaan",
    }
    assert made[GLM].calls and not made[QWEN].calls
    capsys.readouterr()
    assert pipeline.main(where.argv("all", "--no-retrieval")) == 0
    shown = capsys.readouterr().out
    assert "judged as" in shown and "single judge, not for quoting" in shown
    assert "rubric draft, not approved by Aryaan" in shown
    [written] = list(where.results.glob("*.json"))
    saved = json.loads(written.read_text())
    block = saved["answer_quality"]["faithfulness"]
    assert block["policy"] == {
        "mode": "quick",
        "judges": [GLM],
        "label": "single judge, not for quoting",
        "quotable": False,
        "rubric_approved": False,
        "rubric_notice": "rubric draft, not approved by Aryaan",
    }
    assert any("single judge, not for quoting" in n for n in saved["manifest"]["notes"])
    assert any("rubric draft, not approved by Aryaan" in n for n in saved["manifest"]["notes"])
    assert saved["manifest"]["runset"]["judges"]["mode"] == "quick"


def test_a_milestone_run_has_two_judges_may_be_quoted_and_lists_every_disagreement(
    where, capsys, tmp_path
):
    make, made = two_judges(qwen_says={"The contract has a mint function.": "partly"})
    assert sonnet_run(where, "--judge-mode", "milestone", make_judge=make) == 0
    out = capsys.readouterr().out
    assert "the two judges disagree on" in out and "evals.run disagreements --runset" in out
    [runset] = list_runsets(where.runsets)
    assert runset.manifest["judges"]["slugs"] == [GLM, QWEN]
    assert (
        runset.manifest["judges"]["quotable"] is True
        and runset.manifest["judges"]["mode"] == "milestone"
    )
    assert made[GLM].calls and made[QWEN].calls
    assert pipeline.main(where.argv("all", "--no-retrieval")) == 0
    [written] = list(where.results.glob("*.json"))
    block = json.loads(written.read_text())["answer_quality"]["faithfulness"]
    assert block["policy"]["quotable"] is True and block["judges"] == [GLM, QWEN]
    assert block["agreement"]["disagreements"], "every disagreement is listed in the results file"
    listing = tmp_path / "disagreements.md"
    capsys.readouterr()
    assert pipeline.main(where.argv("disagreements", "--out", str(listing))) == 0
    text = listing.read_text()
    assert "The contract has a mint function." in text and "partly" in text and "supported" in text
    assert (
        f"**{GLM}**" in text
        and f"**{QWEN}**" in text
        and "`item` says so" in text
        or "says so" in text
    )


def test_disagreements_are_not_shown_for_a_run_set_with_one_judge_or_for_held_out(where, capsys):
    make, _ = two_judges()
    sonnet_run(where, "--judge-mode", "quick", make_judge=make)
    capsys.readouterr()
    assert pipeline.main(where.argv("disagreements")) == 0
    assert "need two" in capsys.readouterr().out
    pipeline.main(
        where.argv("heldout", "--declare", "x", "--generate", "--go", "--max-spend", "1",
                   cards=where.held),
        make_runner=scripted_runner(),
        retrieval=lambda ns, rows: 0,
    )  # fmt: skip
    held = [r for r in list_runsets(where.runsets) if r.kind == "heldout"][0]
    capsys.readouterr()
    assert pipeline.main(where.argv("disagreements", "--runset", str(held.path))) == 2
    assert "totals only" in capsys.readouterr().out


def test_a_held_out_milestone_judgement_is_labelled_and_has_no_per_claim_list(where):
    make, made = two_judges(qwen_says={"The contract has a mint function.": "partly"})
    pipeline.main(
        where.argv("heldout", "--declare", "milestone 1", "--generate", "--go", "--max-spend", "2",
                   "--model", "sonnet", "--allow-expensive", "--judge-mode", "milestone",
                   cards=where.held),
        make_runner=scripted_runner(),
        make_judge=make,
        retrieval=lambda ns, rows: 0,
    )  # fmt: skip
    [written] = list(where.results.glob("*.json"))
    block = json.loads(written.read_text())["answer_quality"]["faithfulness"]
    assert block["policy"]["quotable"] is True and block["policy"]["mode"] == "milestone"
    assert "disagreements" not in block["agreement"] and block["agreement"]["items"] > 0


def test_approving_the_rubric_later_does_not_change_a_committed_result(where, monkeypatch):
    make, _ = two_judges()
    sonnet_run(where, "--judge-mode", "milestone", make_judge=make)
    assert pipeline.main(where.argv("all", "--no-retrieval", "--write-summary")) == 0
    assert pipeline.main(where.argv("check-summaries")) == 0
    approved = POLICY.__class__(**{**POLICY.__dict__, "rubric_approved": True, "rubric_notice": ""})
    monkeypatch.setattr("evals.judge.policy.load_policy", lambda *a, **k: approved)
    assert pipeline.main(where.argv("check-summaries")) == 0  # still the value recorded then
    [written] = list(where.results.glob("*.json"))
    notes = json.loads(written.read_text())["manifest"]["notes"]
    assert any("rubric draft, not approved by Aryaan" in n for n in notes)


def test_a_self_graded_judge_in_a_manifest_is_not_counted_when_scoring(where, capsys):
    make, _ = two_judges()
    generate_run_set(where, "--judge-mode", "quick", make_judge=make)  # the eval-tier agent
    [runset] = list_runsets(where.runsets)
    manifest = json.loads((runset.path / "manifest.json").read_text())
    manifest["judges"]["slugs"].append(DEEPSEEK)  # as if it had got in by another route
    (runset.path / "manifest.json").write_text(json.dumps(manifest))
    capsys.readouterr()
    assert pipeline.main(where.argv("all", "--no-retrieval")) == 0
    shown = capsys.readouterr().out
    assert "not counted: refused" in shown and "same company" in shown
    [written] = list(where.results.glob("*.json"))
    assert json.loads(written.read_text())["answer_quality"]["faithfulness"]["judges"] == [GLM]


def test_the_judge_step_itself_refuses_a_judge_from_the_agents_company(where):
    generate_run_set(where)
    runset = load_runset(list_runsets(where.runsets)[0].path)
    with pytest.raises(ValueError, match="same company"):
        judge_runset(runset, [DEEPSEEK], JudgeCache(), SpendCap(1.0), lambda spec: FakeJudge())


def test_judging_traces_directly_refuses_a_judge_from_the_agents_company(
    tmp_path, capsys, monkeypatch
):
    class Forbidden:
        def __init__(self, *args, **kwargs):
            raise AssertionError("the judge client must not be built")

    monkeypatch.setattr(judge_cli, "OpenRouterJudge", Forbidden)
    trace = tmp_path / "t.json"
    trace.write_text(json.dumps(make_trace(model=EVAL_MODEL)))
    argv = ["run", str(trace), "--judge", DEEPSEEK, "--price-in", "1", "--price-out", "1"]
    assert judge_cli.main([*argv, "--go", "--max-spend", "1", "--cache", str(tmp_path / "c")]) == 2
    assert "same company as the agent" in capsys.readouterr().out
    other = tmp_path / "s.json"
    other.write_text(json.dumps(make_trace(model=SONNET)))
    argv = ["run", str(other), "--judge", DEEPSEEK, "--price-in", "1", "--price-out", "1"]
    assert judge_cli.main([*argv, "--cache", str(tmp_path / "c")]) == 0  # an estimate only
