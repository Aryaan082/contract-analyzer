"""The verifier evaluation: what a system counts as shown and found, the pre-registered rule, the
cached and capped reviewer. No model, no judge and no cache folder is needed."""

import json

import pytest

from evals.source_review import compare as cmp
from evals.source_review import verifier_eval as ve
from review.client import ModelUnavailable, ReviewerCall, ScriptedReviewer
from review.pack import Prompt
from verify import stage

R = ve.FlagRow


def row(place, claim, labels=(), plain=True):
    return R((f"{place}.sol", "C", place), claim, frozenset(labels), plain)


def test_counts_group_flags_by_place_and_count_a_finding_once():
    rows = [
        row("a", "1", {"F1"}),
        row("a", "2", {"F1", "F2"}),
        row("b", "3"),
        row("c", "4", {"F1"}),
    ]
    assert ve.counts(rows) == (3, 2, 2)  # places a, b, c; a and c match; F1 and F2 found
    assert ve.counts([]) == (0, 0, 0)


def test_a_system_shows_a_place_if_a_flag_passes_and_finds_only_through_a_flag_that_passed():
    rows = [
        row("a", "good", {"F1"}, plain=True),
        row(
            "a", "bad", {"F2"}, plain=True
        ),  # same place, a different flag that the verifier rejects
        row("b", "good2", set(), plain=False),
        row("c", "unchecked", {"F3"}, plain=False),  # no verdict at all
    ]
    verdicts = {
        "good": {"verdict": "valid", "severity": "high", "difficulty": "low"},
        "bad": {"verdict": "invalid", "severity": "", "difficulty": ""},
        "good2": {"verdict": "valid", "severity": "informational", "difficulty": "high"},
    }
    means = ve.evaluate([{"r": rows}], ["r"], verdicts)
    assert means["A plain alone"]["r"] == (1, 1, 2)  # only place a: both of its flags are plain
    assert means["U every flag shown"]["r"] == (3, 2, 3)
    assert means["valid"]["r"] == (2, 1, 1)  # places a and b; F2 and F3 are not found
    assert means["valid, severity low+"]["r"] == (1, 1, 1)  # b is informational: below the gate
    assert means["valid, severity medium+"]["r"] == (1, 1, 1)
    assert means["valid, difficulty not high"]["r"] == (1, 1, 1)  # b needs a privileged role


def test_the_means_are_over_the_runs_and_a_report_with_no_flags_counts_zero():
    run1 = {"r": [row("a", "x", {"F1"})], "s": []}
    run2 = {"r": [row("a", "x", {"F1"}), row("b", "y")]}
    means = ve.evaluate([run1, run2], ["r", "s"], {})
    assert means["U every flag shown"]["r"] == (1.5, 1.0, 1.0)
    assert means["U every flag shown"]["s"] == (0.0, 0.0, 0.0)
    assert means["valid"]["r"] == (0.0, 0.0, 0.0)  # nothing has a verdict


def test_the_gate_that_is_scored_is_the_gate_the_review_list_applies():
    assert ve.SEVERITY_UP["low+"] == stage.SHOWN_SEVERITIES


def means_of(shown, credited, found):
    return {
        "A plain alone": {"r": (10, 1, 1), "s": (10, 1, 1)},
        "valid": {"r": (shown, credited, found), "s": (shown, credited, found)},
    }


def test_the_rule_needs_a_higher_share_no_fewer_findings_and_fewer_places():
    slugs = ["r", "s"]
    better = ve.decide(
        means_of(4, 1, 1), "valid", slugs
    )  # 25% against 10%, same finds, fewer places
    assert better.met and better.found == 0 and better.shown == -12
    assert better.share_interval[0] > 0
    assert not ve.decide(means_of(4, 1, 0), "valid", slugs).met  # finds fewer than plain
    assert not ve.decide(means_of(12, 3, 1), "valid", slugs).met  # shows more places
    lower = ve.decide(means_of(8, 0.5, 1), "valid", slugs)  # 6.25% against 10%, fewer places
    assert not lower.met and lower.share < 0


def test_the_share_must_exceed_plains_with_an_interval_that_excludes_zero():
    slugs = ["r", "s"]
    same_share = ve.decide(means_of(5, 0.5, 1), "valid", slugs)  # 10% against 10%
    assert same_share.share == pytest.approx(0) and not same_share.met


def test_the_bootstrap_interval_is_repeatable_and_collapses_on_a_constant():
    assert ve.interval(lambda reps: 3.0, ["a", "b", "c"]) == (3.0, 3.0)
    f = lambda reps: float(sum(ord(s) for s in reps))  # noqa: E731
    assert ve.interval(f, ["a", "b", "c"]) == ve.interval(f, ["a", "b", "c"])


def test_a_pair_is_a_plain_run_and_a_pipeline_run():
    assert ve.parse_pair("ds:screen+ds3:pipeline") == (("ds", "screen"), ("ds3", "pipeline"))
    with pytest.raises(SystemExit):
        ve.parse_pair("ds:screen")
    for side in ("dev", "held_out"):
        for text in ve.DEFAULT_PAIRS[side]:
            ve.parse_pair(text)


def test_the_verdict_and_cache_files_live_in_a_folder_per_side():
    assert ve.default_out("dev").name == "reasoning-eval"
    assert ve.default_out("held_out").name == "reasoning-eval-heldout"


def prompt(text="p"):
    return Prompt(system="s", user=text)


def client(replies, tmp_path, cap=1.0):
    inner = ScriptedReviewer(replies, name="deepseek/x", cost_usd=0.001)
    return inner, ve.CachedClient(inner, tmp_path / "cache.json", cap, cmp.DEEPSEEK)


def test_an_answered_prompt_is_never_asked_again_and_the_cache_survives_a_restart(tmp_path):
    inner, c = client(['{"a": 1}', '{"a": 2}'], tmp_path)
    assert c.review(prompt(), {}, max_tokens=100).text == '{"a": 1}'
    assert c.review(prompt(), {}, max_tokens=100).text == '{"a": 1}'  # from the cache
    assert (c.calls, c.hits, len(inner.prompts)) == (1, 1, 1)
    assert c.spent == pytest.approx(0.001)
    c.flush()
    assert json.loads((tmp_path / "cache.json").read_text())
    _, again = client(["unused"], tmp_path)
    assert again.review(prompt(), {}, max_tokens=100).text == '{"a": 1}' and again.calls == 0


def test_a_reply_cut_off_at_the_limit_is_not_cached(tmp_path):
    cut = ReviewerCall(text="{", finish_reason="length", model="m")
    inner, c = client([cut], tmp_path)
    c.review(prompt(), {}, max_tokens=100)
    c.review(prompt(), {}, max_tokens=100)
    assert len(inner.prompts) == 2 and not c.cache


def test_a_request_the_cap_would_not_cover_is_refused_before_it_is_sent(tmp_path):
    inner, c = client(["{}"], tmp_path, cap=0.0)
    with pytest.raises(ModelUnavailable, match="spend cap"):
        c.review(prompt(), {}, max_tokens=100)
    assert c.refused == 1 and not inner.prompts


def test_a_failed_request_keeps_its_message(tmp_path):
    _, c = client([ModelUnavailable("OpenRouter error 402: no credits")], tmp_path)
    with pytest.raises(ModelUnavailable):
        c.review(prompt(), {}, max_tokens=100)
    assert c.errors == ["OpenRouter error 402: no credits"] and c.reserved == 0


def test_the_report_names_the_rule_and_says_whether_it_was_met(tmp_path):
    lines: list[str] = []
    means = {
        name: {"r": (10.0, 1.0, 1.0), "s": (10.0, 1.0, 1.0)}
        for name in (*ve.BASE_SYSTEMS, *ve.RULES)
    }
    means["valid, severity low+"] = {"r": (4.0, 1.0, 1.0), "s": (4.0, 1.0, 1.0)}
    ve.report("KEY", means, ["r", "s"], "held_out", "v3flash", 1, out=lines.append)
    text = "\n".join(lines)
    assert "A plain alone" in text and "per report" in text
    assert "valid, severity low+" in text and "RULE MET" in text
    assert text.count("rule not met") == len(ve.RULES) - 1


def test_the_command_is_registered_with_the_options_that_gate_spending():
    p = ve.build_parser()
    ns = p.parse_args(["claims", "--side", "held_out", "--cache", "c", "--max-spend", "1",
                       "--confirm-spend", "--declare", "why"])  # fmt: skip
    assert ns.confirm_spend and ns.max_spend == 1 and ns.declare == "why" and ns.workers == 20
    score = p.parse_args(["score", "--side", "held_out", "--cache", "c", "--exploratory-held-out"])
    assert score.exploratory_held_out
    with pytest.raises(SystemExit):  # the claims command has no such option
        p.parse_args(["claims", "--side", "dev", "--cache", "c", "--exploratory-held-out"])
