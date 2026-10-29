"""The head-to-head harness (arm P, arm A, budget, report), with scripted clients only: no model,
no network, no explorer."""

import json
from pathlib import Path

import pytest

from evals.source_review import compare as cmp
from evals.source_review import compare_cli, plain
from evals.source_review.cards import load_cards
from evals.source_review.scoring import Flag, flags_of, run_counts
from review.client import ModelUnavailable, ReviewerCall, ScriptedReviewer
from review.lexer import lex

CARDS = load_cards()
PLANTED = [i for i in cmp.dev_items(CARDS) if i.card.origin == "planted"]


def _fixture(item_id: str) -> tuple[cmp.Item, cmp.Source]:
    item = next(i for i in PLANTED if i.id == item_id)
    return item, cmp.fixture_source(item.card)


def _reply(findings: list[dict]) -> str:
    return json.dumps({"findings": findings})


def _finding(**over) -> dict:
    base = {
        "file": "contracts/Contract.sol",
        "contract": "C",
        "function": "f",
        "start_line": 3,
        "end_line": 4,
        "severity": "medium",
        "description": "d",
    }
    return base | over


# ---- the items ---------------------------------------------------------------------------------


def test_dev_items_are_dev_only_and_in_family_order():
    items = cmp.dev_items(CARDS)
    assert all(i.card.split == "dev" for i in items)
    groups = [i.group for i in items]
    assert groups.count("planted_vulnerable") == 12
    assert groups.count("planted_safe") == 12
    assert groups.count("planted_renamed") == 12
    assert groups.count("incident") == 5  # the labelled dev incidents
    assert [i.id for i in items[:3]] == ["pl01", "pl01-safe", "pl01-renamed"]
    held_out = {c.id for c in CARDS if c.split == "held_out"}
    assert not held_out & {i.id for i in items}


def test_fixture_sources_share_one_neutral_path_so_a_file_name_never_says_vulnerable():
    for item_id in ("pl01", "pl01-safe", "pl01-renamed"):
        _, source = _fixture(item_id)
        assert [path for path, _ in source.files] == [cmp.FIXTURE_PATH]
        assert "vulnerable" not in cmp.FIXTURE_PATH.lower()


# ---- arm P: the prompt ---------------------------------------------------------------------------


def test_plain_prompt_is_raw_numbered_and_carries_no_pipeline():
    text = (
        "// SPDX-License-Identifier: MIT\n// note: owner only\ncontract C {\n"
        "  function f() external {}\n}\n"
    )
    (prompt,) = plain.plain_prompts([("contracts/C.sol", text)])
    assert prompt.system == plain.PLAIN_SYSTEM
    assert "// note: owner only" in prompt.user  # comments are NOT stripped
    assert "=== file: contracts/C.sol ===" in prompt.user
    assert "    4|   function f() external {}" in prompt.user
    for word in ("checklist", "taxonomy", "<<<", "untrusted"):
        assert word not in prompt.user.lower()


def test_plain_prompt_splits_only_when_the_raw_source_is_too_large():
    files = [(f"a{i}.sol", "contract A {}\n" * 50) for i in range(4)]
    assert len(plain.plain_prompts(files)) == 1
    parts = plain.plain_prompts(files, max_input_tokens=400)
    assert len(parts) > 1
    for i in range(4):
        assert sum(f"=== file: a{i}.sol" in p.user for p in parts) >= 1
    assert all("part " in p.user for p in parts)


def test_a_single_file_over_the_limit_is_cut_by_lines_and_keeps_its_line_numbers():
    big = ("uint256 x;\n" * 300).rstrip("\n")
    parts = plain.plain_prompts([("big.sol", big)], max_input_tokens=300)
    assert len(parts) > 1
    assert "(lines 1 to " in parts[0].user
    assert any(f"{300:>5}| uint256 x;" in p.user for p in parts)


# ---- arm P: reading a reply ----------------------------------------------------------------------


def test_parse_reply_reads_good_json_and_a_fenced_block():
    items, failures, dropped = plain.parse_reply(_reply([_finding()]), "stop")
    assert [(i.contract, i.function, i.severity) for i in items] == [("C", "f", "medium")]
    assert failures == [] and not dropped
    fenced = "```json\n" + _reply([_finding()]) + "\n```"
    assert len(plain.parse_reply(fenced, "stop")[0]) == 1


@pytest.mark.parametrize(
    ("text", "finish", "why"),
    [
        ("not json at all", "stop", "reply is not JSON"),
        ('{"findings": 3}', "stop", "reply has no findings list"),
        ("[]", "stop", "reply has no findings list"),
        (None, "stop", "no text in the reply"),
        ("   ", "stop", "no text in the reply"),
        ('{"findings": [', "length", "reply cut off at the output limit"),
    ],
)
def test_an_unusable_reply_is_no_findings_and_one_named_failure(text, finish, why):
    items, failures, _ = plain.parse_reply(text, finish)
    assert items == [] and failures == [why]


def test_bad_items_are_dropped_and_counted_and_the_list_is_bounded():
    bad = [
        "text",
        _finding(severity="critical"),
        _finding(function=None),
        _finding(start_line="3"),
        _finding(contract=5),
    ]
    items, failures, dropped = plain.parse_reply(_reply([*bad, _finding()]), "stop")
    assert len(items) == 1 and failures == []
    assert dropped == {"not_an_object": 1, "bad_severity": 1, "bad_field": 3}
    many = _reply([_finding()] * (plain.MAX_FINDINGS + 7))
    items, _, dropped = plain.parse_reply(many, "stop")
    assert len(items) == plain.MAX_FINDINGS and dropped["over_limit"] == 7


# ---- arm P: placing a finding --------------------------------------------------------------------


def _lexed(item_id: str):
    item, source = _fixture(item_id)
    path, text = source.files[0]
    return item.card, {path: lex("F1", text)}, text


def test_a_finding_is_placed_by_its_names_then_by_its_lines_and_else_not_at_all():
    card, lexed, _ = _lexed("pl01")
    label = card.functions[0]
    path = cmp.FIXTURE_PATH
    named = plain.Item(path, label.contract, label.function, 1, 1, "high")
    assert plain.place(named, lexed).key == (label.contract, label.function)
    no_contract = plain.Item("Other.sol", None, label.function, 1, 1, "low")
    assert plain.place(no_contract, lexed).key == (label.contract, label.function)  # one file
    member = next(m for d in lexed[path].definitions for m in d.members if m.name == label.function)
    by_lines = plain.Item(path, "Wrong", "nope", member.start_line, member.start_line, "low")
    assert plain.place(by_lines, lexed).key == (label.contract, label.function)
    nowhere = plain.Item(path, "Wrong", "nope", 1, 1, "low")
    assert plain.place(nowhere, lexed).function is None


def test_counts_from_flags_agrees_with_the_scorers_own_counts_on_arm_a():
    item, source = _fixture("pl01")
    for flaws_on in (True, False):
        client = (
            cmp.scripted_ours(item, source) if flaws_on else ScriptedReviewer(['{"items": []}'])
        )
        result = cmp.run_review(source.bundle, client, known=source.known, second=None)
        for floor in ("low", "medium"):
            ours = cmp.counts_from_flags(item.card, flags_of(result), min_severity=floor)
            theirs = run_counts(item.card, result, min_severity=floor)
            assert (ours.tp, ours.fp, ours.fn) == (theirs.tp, theirs.fp, theirs.fn)


# ---- the money -----------------------------------------------------------------------------------


def _call(cost: float) -> ReviewerCall:
    return ReviewerCall(text='{"items": []}', cost_usd=cost)


def test_budget_adds_the_reported_cost_and_refuses_before_a_request_that_could_pass_the_cap():
    inner = ScriptedReviewer([_call(0.30)])
    budget = cmp.Budget(inner, 0.50)
    prompt = plain.plain_prompts([("a.sol", "contract A {}")])[0]
    budget.review(prompt, {}, max_tokens=100)
    assert budget.spent == pytest.approx(0.30)
    with pytest.raises(ModelUnavailable):
        budget.review(prompt, {}, max_tokens=50_000)  # worst case 1.0 dollar
    assert budget.refused == 1 and len(inner.prompts) == 1  # the model was not asked
    budget.review(prompt, {}, max_tokens=100)  # a small one still fits
    assert len(inner.prompts) == 2


# ---- running both arms ---------------------------------------------------------------------------


def _sources(items):
    return {i.id: cmp.fixture_source(i.card) for i in items}


def test_scripted_run_scores_both_arms_perfectly_and_never_stores_source_text():
    items = PLANTED[:6]
    records, spent = cmp.scripted_run(items, _sources(items), out=lambda _: None)
    assert [r["item"] for r in records] == [i.id for i in items]
    assert spent == pytest.approx(0.02 * 6)
    meta = {
        "model": "scripted",
        "date": "2026-10-29",
        "items_total": 6,
        "spent": spent,
        "max_spend": 1,
    }
    report = cmp.build_report(records, CARDS, meta)
    vulnerable = report["groups"]["planted_vulnerable"]
    assert vulnerable["P"]["found_items"] == vulnerable["A"]["found_items"] == 2
    assert report["groups"]["planted_safe"]["P"]["with_medium_plus_flag"] == 0
    assert report["paired"]["planted_vulnerable"]["neither"] == 0
    blob = json.dumps(report)
    for item in items:
        for line in item.card.fixture_path.read_text().splitlines():
            if len(line.strip()) > 30:
                assert line.strip() not in blob


def test_the_cap_stops_the_run_and_the_rest_is_reported_as_not_run():
    items = PLANTED[:6]
    paid = []

    def make(item, source):
        paid.append(item.id)  # each item "costs" 0.10 through `spent`
        return cmp.scripted_plain(item, source)

    records = cmp.run_compare(
        items,
        _sources(items),
        make,
        cmp.scripted_ours,
        spent=lambda: 0.1 * len(paid),
        max_spend=0.35,
        out=lambda _: None,
    )
    ran = [r for r in records if "P" in r]
    skipped = [r for r in records if "skipped" in r]
    assert len(ran) == 2 and len(skipped) == 4
    assert all(r["skipped"] == "spend cap" for r in skipped)
    assert [r["item"] for r in ran] == [i.id for i in items[:2]]  # a whole prefix, in order
    meta = {"model": "m", "date": "d", "items_total": 6, "spent": 0.2, "max_spend": 0.35}
    report = cmp.build_report(records, CARDS, meta)
    assert {n["item"] for n in report["not_scored"]} == {r["item"] for r in skipped}


def test_a_refused_request_leaves_the_item_unscored_not_counted_as_nothing_found():
    item, source = _fixture("pl01")
    dead = cmp.Budget(cmp.scripted_plain(item, source), 0.0)  # every request refused
    arm = cmp.run_arm_p(source, dead, plain.DEFAULT_MAX_TOKENS)
    assert not arm.complete and arm.calls == 0
    arm_a = cmp.run_arm_a(source, cmp.Budget(cmp.scripted_ours(item, source), 0.0), 8192)
    assert not arm_a.complete


def test_resume_keeps_earlier_records_and_the_checkpoint_has_one_line_per_new_item(tmp_path):
    items = PLANTED[:3]
    sources = _sources(items)
    first, _ = cmp.scripted_run(items[:1], sources, out=lambda _: None)
    done = {first[0]["item"]: first[0]}
    checkpoint = tmp_path / "run.jsonl"
    seen: list[str] = []

    def make(item, source):
        seen.append(item.id)
        return cmp.scripted_plain(item, source)

    records = cmp.run_compare(
        items,
        sources,
        make,
        cmp.scripted_ours,
        spent=lambda: 0.0,
        max_spend=1.0,
        done=done,
        checkpoint=checkpoint,
        out=lambda _: None,
    )
    assert seen == [items[1].id, items[2].id]  # the done item was not run again
    assert len(records) == 3
    assert set(cmp.read_checkpoint(checkpoint)) == {items[1].id, items[2].id}


def test_a_dead_model_stops_the_run_after_a_few_silent_items():
    items = PLANTED[:8]
    dead = lambda item, source: ScriptedReviewer([ModelUnavailable("down")])  # noqa: E731
    lines: list[str] = []
    records = cmp.run_compare(
        items, _sources(items), dead, dead, spent=lambda: 0.0, max_spend=9.0, out=lines.append
    )
    ran = [r for r in records if "P" in r]
    assert len(ran) == cmp.MAX_SILENT
    assert all(r["skipped"] == "model not answering" for r in records if "skipped" in r)
    assert len(records) == 8 and any("stopping" in line for line in lines)


def test_an_unfinished_item_in_a_checkpoint_is_run_again_on_resume():
    item = PLANTED[0]
    dead = lambda i, s: ScriptedReviewer([ModelUnavailable("down")])  # noqa: E731
    first = cmp.run_compare(
        [item], _sources([item]), dead, dead, spent=lambda: 0.0, max_spend=9.0, out=lambda _: None
    )
    assert not first[0]["P"]["complete"]
    again = cmp.run_compare(
        [item],
        _sources([item]),
        cmp.scripted_plain,
        cmp.scripted_ours,
        spent=lambda: 0.0,
        max_spend=9.0,
        done={item.id: first[0]},
        out=lambda _: None,
    )
    assert again[0]["P"]["complete"] and again[0]["A"]["complete"]


def test_the_real_client_asks_for_json_in_the_prompt_because_opus_has_no_schema_endpoint(
    monkeypatch,
):
    seen = {}

    class Fake(ScriptedReviewer):
        def __init__(self, spec, **kwargs):
            seen.update(kwargs, name=spec.name)
            super().__init__([_call(0.0)], name=spec.name)

    monkeypatch.setattr(cmp, "OpenRouterReviewer", Fake)
    cmp.real_budget(1.0)
    assert seen == {
        "structured": "none",
        "retries": cmp.RETRIES,
        "name": "anthropic/claude-opus-5.5",
    }


def test_with_no_response_format_the_schema_reaches_the_model_inside_the_prompt_for_both_arms():
    inner = ScriptedReviewer([_call(0.0)])
    budget = cmp.Budget(inner, 5.0, schema_in_prompt=True)
    prompt = plain.plain_prompts([("a.sol", "contract A {}")])[0]
    schema = {"type": "object", "properties": {"findings": {}}}
    budget.review(prompt, schema, max_tokens=100)
    sent = inner.prompts[0]
    assert '"properties":{"findings":{}}' in sent.system and sent.user == prompt.user
    off = ScriptedReviewer([_call(0.0)])
    cmp.Budget(off, 5.0).review(prompt, schema, max_tokens=100)
    assert off.prompts[0] == prompt  # the default leaves the prompt alone


# ---- statistics and report -----------------------------------------------------------------------


def test_sign_test_and_the_smallest_significant_split():
    assert cmp.sign_test_p(6, 0) == pytest.approx(0.03125)
    assert cmp.sign_test_p(5, 0) == pytest.approx(0.0625)
    assert cmp.sign_test_p(0, 0) == 1.0
    assert cmp.sign_test_p(3, 3) == 1.0
    assert cmp.smallest_split(6) == "6:0"
    assert cmp.smallest_split(5).startswith("none")
    assert cmp.smallest_split(10) == "9:1"


def _arm(flags: list[Flag], cost: float = 0.1) -> dict:
    return cmp.asdict(
        cmp.ArmRecord("X", True, [cmp.asdict(f) for f in flags], 1, 10, 10, cost, 1.0, "ran")
    )


def _flag(card, severity="medium") -> Flag:
    label = card.functions[0]
    return Flag(label.contract, label.function, "e", severity, "medium", False, "F1", 1, 2)


def test_the_paired_view_counts_who_found_what_and_the_report_says_so():
    items = [i for i in PLANTED if i.group == "planted_vulnerable"][:4]
    plan = {
        items[0].id: (True, True),
        items[1].id: (True, False),
        items[2].id: (True, False),
        items[3].id: (False, True),
    }  # (A found, P found)
    records = []
    for item in items:
        a_found, p_found = plan[item.id]
        records.append(
            {
                "item": item.id,
                "group": item.group,
                "P": _arm([_flag(item.card)] if p_found else []) | {"arm": "P"},
                "A": _arm([_flag(item.card)] if a_found else []) | {"arm": "A"},
            }
        )
    meta = {"model": "m", "date": "2026-10-29", "items_total": 4, "spent": 0.8, "max_spend": 1.0}
    report = cmp.build_report(records, CARDS, meta)
    paired = report["paired"]["planted_vulnerable"]
    assert (paired["both_found"], paired["a_found_p_missed"], paired["p_found_a_missed"]) == (
        1,
        2,
        1,
    )
    assert report["groups"]["planted_vulnerable"]["A"]["found_items"] == 3
    text = "\n".join(cmp.render(report))
    assert "DRAFT LABELS" in text and "may be memorised" not in text.split("## Finding")[0]
    assert "MEMORISED" in "\n".join(cmp.read_limits(report)) or "memorised" in text


def test_the_results_row_is_one_line_with_the_counts():
    records, spent = cmp.scripted_run(PLANTED[:3], _sources(PLANTED[:3]), out=lambda _: None)
    meta = {
        "model": cmp.OPUS.name,
        "date": "2026-10-29",
        "items_total": 3,
        "spent": spent,
        "max_spend": 1.0,
    }
    row = cmp.results_row(cmp.build_report(records, CARDS, meta))
    assert "\n" not in row and row.startswith("| 2026-10-29 |") and "1/1 vs 1/1" in row


# ---- the command line ----------------------------------------------------------------------------


def test_the_run_command_refuses_without_confirmation_and_never_builds_a_real_client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a real model client was built")

    monkeypatch.setattr(cmp, "OpenRouterReviewer", forbidden)
    lines: list[str] = []
    code = compare_cli.main(["run", "--planted-only"], out=lines.append)
    assert code == 2
    text = "\n".join(lines)
    assert "--confirm-spend was not given" in text and "--max-spend was not given" in text
    code = compare_cli.main(["run", "--planted-only", "--confirm-spend"], out=lines.append)
    assert code == 2


def test_the_dry_run_command_is_free_and_says_it_is_scripted():
    lines: list[str] = []
    assert compare_cli.main(["dry-run"], out=lines.append) == 0
    assert "SCRIPTED" in lines[-1]


def test_log_appends_one_row_once(tmp_path, monkeypatch):
    records, spent = cmp.scripted_run(PLANTED[:3], _sources(PLANTED[:3]), out=lambda _: None)
    meta = {
        "model": cmp.OPUS.name,
        "date": "2026-10-29",
        "items_total": 3,
        "spent": spent,
        "max_spend": 1.0,
    }
    report = tmp_path / "r.json"
    report.write_text(json.dumps(cmp.build_report(records, CARDS, meta)))
    log = tmp_path / "results.md"
    log.write_text("# Experiment log\n")
    monkeypatch.setattr(compare_cli, "RESULTS_MD", log)
    assert compare_cli.main(["log", "--report", str(report)], out=lambda _: None) == 0
    assert compare_cli.main(["log", "--report", str(report)], out=lambda _: None) == 1
    assert log.read_text().count("plain Opus 5.5") == 1
    assert Path(log).read_text().startswith("# Experiment log")


def test_budget_keeps_the_service_message_of_a_failed_request():
    boom = ScriptedReviewer([ModelUnavailable("OpenRouter error 429: rate limited")])
    budget = cmp.Budget(boom, 5.0)
    prompt = plain.plain_prompts([("a.sol", "contract A {}")])[0]
    with pytest.raises(ModelUnavailable):
        budget.review(prompt, {}, max_tokens=100)
    assert budget.errors == ["OpenRouter error 429: rate limited"]


def test_credits_remaining_reads_the_balance_and_survives_a_failure(monkeypatch):
    import io

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-value")

    def served(request, timeout=0):
        assert request.get_header("Authorization") == "Bearer test-key-value"
        return io.BytesIO(json.dumps({"data": {"total_credits": 10, "total_usage": 9.7}}).encode())

    assert cmp.credits_remaining(served) == pytest.approx(0.3)

    def down(request, timeout=0):
        raise OSError("no route")

    assert cmp.credits_remaining(down) is None
