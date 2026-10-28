"""The judge's check set, as committed (checkset/), and the command that runs it: offline, with a
scripted judge standing in for the model. The faults in it are ours; see evals/judge/README.md."""

import json
import re

import pytest

from evals.cards import load_cards
from evals.judge import checkset as cs
from evals.judge import cli
from evals.judge.cache import JudgeCache
from evals.judge.rubric import load_rubric
from evals.judge.runner import Price, SpendCap, run_jobs
from tests.evals_support import make_trace
from tests.judge_support import FakeJudge, oracle

RUBRIC = load_rubric()
PRICE = Price(1.0, 1.0)


# -- the committed check set --------------------------------------------------------------------


@pytest.fixture(scope="module")
def items():
    return cs.load_items()


def test_the_check_set_has_the_planted_real_and_sound_items_the_plan_calls_for(items):
    claims = [i for i in items if i.task == "claim"]
    planted = [i for i in claims if i.row["origin"] == "planted"]
    real = [i for i in claims if i.row["origin"] == "real"]
    sound = [i for i in claims if not i.row["fault"]]
    assert len(planted) >= 36 and len(real) >= 4
    assert len({i.row["operator"] for i in planted}) >= 6
    assert len(sound) >= len(planted) - 4  # nearly every planted fault has its unedited twin
    assert len([i for i in items if i.task == "summary"]) >= 12
    units = [u for i in items for u in i.scored()]
    assert sum(f for _, f in units) >= 40 and sum(not f for _, f in units) >= 40


def test_every_item_has_a_unique_id_and_every_planted_fault_differs_from_its_twin(items):
    ids = [i.id for i in items]
    assert len(set(ids)) == len(ids)
    by_id = {i.id: i for i in items}
    for item in items:
        twin = item.row.get("twin_of")
        if item.task == "claim" and item.row["origin"] == "planted" and twin:
            base = by_id[twin].claim
            assert (item.claim.claim, item.claim.records) != (base.claim, base.records)
            assert not by_id[twin].row["fault"]


def test_the_citation_validator_accepts_every_claim_in_the_check_set(items):
    """So no fault here is one the validator would already have refused."""
    assert all(i.row["validator_accepts"] for i in items if i.task == "claim")


def test_the_check_set_comes_only_from_dev_contracts():
    dev = load_cards(cs.JUDGE_DIR.parent / "datasets" / "contracts.toml")
    allowed = {(c.address, c.block) for c in dev if c.split == "dev"}
    sources = {(i.row["source"]["address"], i.row["source"]["block"]) for i in cs.load_items()}
    assert sources <= allowed


def test_every_record_a_claim_cites_is_in_the_evidence_file(items):
    for item in items:
        if item.claim is not None:
            assert item.claim.records and all(r.text for r in item.claim.records)


def test_the_check_set_files_hold_no_url_or_key():
    text = cs.ITEMS_PATH.read_text() + cs.EVIDENCE_PATH.read_text()
    assert not re.search(r"https?://|alchemy|api[_-]?key|sk-or-", text, re.IGNORECASE)


def test_the_readme_says_the_faults_are_ours():
    text = (cs.JUDGE_DIR / "README.md").read_text()
    assert "The faults are ours" in text and "not truth" in text
    assert "Aryaan's own labels" in text


# -- running the check set with a scripted judge ------------------------------------------------


def key_of(item):
    return (item.claim.claim, tuple(r.id for r in item.claim.records))


def answer_key(items, missed=(), false_alarms=()):
    claims, sentences = {}, {}
    for item in items:
        if item.claim is not None:
            if item.row["fault"]:
                claims[key_of(item)] = "supported" if item.id in missed else "contradicted"
            elif item.id in false_alarms:
                claims[key_of(item)] = "not_supported"
        else:
            for n, role in item.row["roles"].items():
                text = item.summary.sentences[int(n) - 1]
                sentences[text] = "not_supported" if role == "fault" else "supported"
    return claims, sentences


def run_check(items, model, **key):
    cache = key.pop("cache", JudgeCache())
    claims, sentences = answer_key(items, **key)
    run_jobs(
        cs.jobs_for(items, RUBRIC, model),
        FakeJudge(oracle(claims, sentences), name=model),
        cache,
        SpendCap(10),
        price=PRICE,
        max_tokens=100,
    )
    return cache


def test_a_perfect_scripted_judge_catches_everything_and_raises_no_false_alarm(items):
    cache = run_check(items, "vendor/a")
    out = cs.evaluate(items, cache, ["vendor/a"], RUBRIC.sha256)["per_judge"]["vendor/a"]
    assert out["claims"]["catch"]["rate"] == 1.0 and out["claims"]["false_alarm"]["rate"] == 0.0
    assert out["summary_sentences"]["catch"]["rate"] == 1.0
    assert out["summary_sentences"]["false_alarm"]["rate"] == 0.0
    assert out["unlabelled"] == 0 and out["cost"]["refused_replies"] == 0


def test_planted_misses_and_false_alarms_show_up_in_the_rates_and_the_lists(items):
    planted = [i.id for i in items if i.task == "claim" and i.row["origin"] == "planted"]
    sound = [i.id for i in items if i.task == "claim" and not i.row["fault"]]
    cache = run_check(items, "vendor/a", missed=planted[:3], false_alarms=sound[:2])
    claims = cs.evaluate(items, cache, ["vendor/a"], RUBRIC.sha256)["per_judge"]["vendor/a"][
        "claims"
    ]
    n_faults = claims["catch"]["n"]
    assert claims["catch"]["k"] == n_faults - 3 and claims["missed"] == sorted(planted[:3])
    assert claims["false_alarm"]["k"] == 2 and claims["false_alarms"] == sorted(sound[:2])


def test_two_scripted_judges_that_differ_on_three_claims_disagree_on_exactly_those(items):
    planted = [i.id for i in items if i.task == "claim" and i.row["origin"] == "planted"]
    cache = run_check(items, "vendor/a")
    run_check(items, "vendor/b", missed=planted[:3], cache=cache)
    out = cs.evaluate(items, cache, ["vendor/a", "vendor/b"], RUBRIC.sha256)
    assert [d["id"] for d in out["agreement"]["disagreements"]] == sorted(planted[:3])


def test_the_stability_pass_is_a_separate_question_and_counts_the_changed_labels(items):
    cache = run_check(items, "vendor/a")
    claims, sentences = answer_key(items)
    flip = next(i for i in items if i.task == "claim" and not i.row["fault"])
    claims[key_of(flip)] = "partly"
    run_jobs(
        cs.jobs_for(items, RUBRIC, "vendor/a", repeat=1),
        FakeJudge(oracle(claims, sentences), name="vendor/a"),
        cache,
        SpendCap(10),
        price=PRICE,
        max_tokens=100,
    )
    out = cs.evaluate(items, cache, ["vendor/a"], RUBRIC.sha256, stability_models=["vendor/a"])
    assert out["stability"]["vendor/a"]["changed"] == 1


def test_every_prompt_for_the_check_set_carries_its_claim_and_no_answer_key(items):
    for job in cs.jobs_for(items, RUBRIC, "vendor/a"):
        text = job.prompt.system + job.prompt.user
        assert "fault" not in text.lower().replace("default", "") or job.task == "summary"
        assert "planted" not in text.lower()


# -- the command --------------------------------------------------------------------------------


class Forbidden:
    def __init__(self, *args, **kwargs):
        raise AssertionError("the judge client must not be built without --go")


def test_estimate_and_a_run_without_go_send_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "OpenRouterJudge", Forbidden)
    argv = ["--judge", "vendor/judge", "--price-in", "0.3", "--price-out", "1.2"]
    assert cli.main(["checkset", "estimate", *argv, "--cache", str(tmp_path)]) == 0
    assert cli.main(["checkset", "run", *argv, "--max-spend", "1", "--cache", str(tmp_path)]) == 0
    shown = capsys.readouterr().out
    assert "nothing sent" in shown and "worst case" in shown


def test_go_without_a_spend_cap_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "OpenRouterJudge", Forbidden)
    argv = ["checkset", "run", "--judge", "vendor/judge", "--price-in", "1", "--price-out", "1"]
    assert cli.main([*argv, "--go", "--cache", str(tmp_path)]) == 2


def test_go_with_a_cap_stops_at_the_cap_and_says_so(monkeypatch, tmp_path, capsys):
    fake = FakeJudge(cost=0.001)  # a call really costs less than the most it could
    monkeypatch.setattr(cli, "OpenRouterJudge", lambda spec: fake)
    argv = ["checkset", "run", "--judge", "vendor/judge", "--price-in", "1", "--price-out", "1"]
    code = cli.main(
        [*argv, "--go", "--max-spend", "0.0045", "--workers", "1", "--cache", str(tmp_path)]
    )
    assert code == 3 and "STOPPED: spend_cap" in capsys.readouterr().out
    cache = JudgeCache(tmp_path)
    assert sum(r["cost_usd"] for r in cache.rows()) <= 0.0045
    assert len(fake.calls) == len(cache) <= 3


def test_judging_traces_needs_go_too(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "OpenRouterJudge", Forbidden)
    trace = tmp_path / "one.json"
    trace.write_text(json.dumps(make_trace()))
    argv = ["run", str(trace), "--judge", "vendor/judge", "--price-in", "1", "--price-out", "1"]
    assert cli.main([*argv, "--cache", str(tmp_path / "c")]) == 0
    assert "nothing sent" in capsys.readouterr().out
