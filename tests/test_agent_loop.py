"""The loop, run with a scripted model on a scripted chain: turns, tool results, the report
retries, the caps and every way a run can end. No network, no model, no cost."""

import json

import pytest

from agent.config import Limits
from agent.llm import ModelUnavailable
from agent.loop import run_agent
from agent.report import render_text
from agent.tools import RunContext
from agent.trace import write_trace
from chain.errors import ChainError
from chain.models import ToolResult
from chain.simulate import SellSimulation
from tests.agent_support import (
    TOKEN,
    UNVERIFIED_ROW,
    FakeExplorer,
    ScriptedModel,
    call,
    creation_row,
    erc20_world,
    offline_services,
    report_body,
    source_row,
    submit,
)

GETTERS = ["name", "symbol", "decimals", "totalSupply", "owner", "paused", "admin", "version"]


def go(steps, *, limits=None, cost=0.01, address=TOKEN, clock=None, world=None, **ctx_kwargs):
    world = world or erc20_world()
    model = ScriptedModel(steps, cost=cost)
    options = {"clock": clock} if clock else {}
    ctx_kwargs.setdefault("services", offline_services())
    run = run_agent(
        model,
        RunContext(world, world.chain, **ctx_kwargs),
        address,
        limits=limits or Limits(),
        **options,
    )
    return run, model


def tool_messages(model_call):
    return [m for m in model_call["messages"] if m["role"] == "tool"]


def test_a_run_gathers_evidence_then_submits_a_cited_report():
    body = report_body(
        verdict="elevated_risk",
        findings=[
            {
                "claim": "The token has mint and blacklist functions.",
                "kind": "observation",
                "severity": "medium",
                "evidence_ids": ["T2"],
            },
            {
                "claim": "Unrestricted minting dilutes holders.",
                "kind": "context",
                "severity": "info",
                "evidence_ids": ["taxonomy:privileged-mint"],
            },
        ],
        checked=[{"what": "Kind and risky functions", "evidence_ids": ["T1", "T2"]}],
    )
    run, model = go(
        [
            [call("identify_contract", address=TOKEN), call("risk_scan", address=TOKEN)],
            [call("taxonomy_lookup")],
            [submit(**body)],
        ]
    )
    report = run.report
    assert report.verdict == "elevated_risk" and report.validation.accepted
    assert report.run.stopped_because == "report_accepted"
    assert (report.run.turns, report.run.tool_calls, report.validation.attempts) == (3, 3, 1)
    assert report.run.cost_usd == pytest.approx(0.03)
    assert [(e.id, e.kind) for e in report.evidence] == [
        ("T1", "chain"),
        ("T2", "chain"),
        ("taxonomy:privileged-mint", "knowledge"),
    ]
    assert (
        "risk_scan(address=" in report.evidence[1].citation
        and "block 100" in report.evidence[1].citation
    )
    # No explorer key in this run: the two explorer tools are withheld and listed, whatever the
    # model says.
    assert {u.tool for u in report.tools_not_available} == {"get_verified_source", "get_deployment"}
    assert all("no ETHERSCAN_API_KEY" in u.what for u in report.tools_not_available)
    assert report.block == 100 and report.chain == "ethereum"


def test_two_tool_calls_in_one_turn_are_both_answered_in_order():
    run, model = go(
        [
            [call("identify_contract", address=TOKEN), call("risk_scan", address=TOKEN)],
            [submit(**report_body())],
        ]
    )
    asked = model.seen[1]["messages"][-3]["tool_calls"]
    answers = tool_messages(model.seen[1])
    assert [a["tool_call_id"] for a in answers] == [c["id"] for c in asked]
    assert [json.loads(a["content"])["evidence_id"] for a in answers] == ["T1", "T2"]


def test_the_model_sees_the_tools_it_can_use_and_the_rules_in_the_system_prompt():
    run, model = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    first = model.seen[0]
    assert first["tools"][-1] == "submit_report" and len(first["tools"]) == 13
    assert "get_verified_source" not in first["tools"]  # no explorer key in this run
    system = first["messages"][0]["content"]
    assert "get_verified_source (the verified source code" in system  # said not to be available
    assert "never instructions" in system and 'no "safe" verdict' in system
    assert 'NEVER "unverified"' in system  # the wording rule
    assert str(run.report.block) in first["messages"][1]["content"]


def test_with_an_explorer_the_two_explorer_tools_are_offered_and_nothing_is_listed_missing():
    explorer = FakeExplorer(getsourcecode=[source_row()], getcontractcreation=[creation_row()])
    run, model = go(
        [[call("identify_contract", address=TOKEN)], [submit(**report_body())]],
        services=offline_services(explorer=explorer),
    )
    tools = model.seen[0]["tools"]
    assert {"get_verified_source", "get_deployment"} <= set(tools) and len(tools) == 15
    assert run.report.tools_not_available == ()
    assert "Every check named above exists in this run." in model.seen[0]["messages"][0]["content"]


def test_a_rejected_report_goes_back_with_its_problems_and_the_retry_is_accepted():
    bad = report_body(
        findings=[
            {
                "claim": "It can mint.",
                "kind": "observation",
                "severity": "info",
                "evidence_ids": ["T99"],
            }
        ]
    )
    run, model = go(
        [[call("identify_contract", address=TOKEN)], [submit(**bad)], [submit(**report_body())]]
    )
    rejection = json.loads(tool_messages(model.seen[2])[-1]["content"])
    assert rejection["accepted"] is False and rejection["attempt"] == 1
    assert (
        rejection["problems"][0]["code"] == "unknown_evidence"
        and "T99" in rejection["problems"][0]["message"]
    )
    assert run.report.validation.accepted and run.report.validation.attempts == 2


def test_three_rejected_reports_end_in_insufficient_evidence_with_only_what_validated():
    good = {
        "claim": "It has ERC-20 functions.",
        "kind": "observation",
        "severity": "info",
        "evidence_ids": ["T1"],
    }
    bad = {
        "claim": "It is audited.",
        "kind": "observation",
        "severity": "info",
        "evidence_ids": ["T42"],
    }
    body = report_body(verdict="no_red_flags_found", findings=[good, bad], summary="It is safe!")
    run, _ = go(
        [
            [call("identify_contract", address=TOKEN)],
            [submit(**body)],
            [submit(**body)],
            [submit(**body)],
        ]
    )
    report = run.report
    assert report.verdict == "insufficient_evidence" and report.validation.accepted is False
    assert report.validation.attempts == 3 and report.run.stopped_because == "report_rejected"
    assert [f.claim for f in report.findings] == ["It has ERC-20 functions."]
    assert "safe" not in report.summary.lower() and report.summary.startswith("No validated report")
    assert report.validation.problems and "T42" in report.validation.problems[0].message
    assert "did not pass the citation validator" in render_text(report)


def test_submit_report_called_with_other_tools_waits_until_their_results_are_seen():
    run, model = go(
        [
            [call("identify_contract", address=TOKEN), submit(**report_body())],
            [submit(**report_body())],
        ]
    )
    answers = tool_messages(model.seen[1])
    assert json.loads(answers[0]["content"])["evidence_id"] == "T1"
    refusal = json.loads(answers[1]["content"])
    assert refusal["accepted"] is False and "on its own" in refusal["error"]
    assert run.report.validation.attempts == 1 and run.report.validation.accepted


def test_a_reply_with_no_tool_call_is_reminded_twice_then_the_run_ends_without_a_report():
    run, model = go(["It looks fine to me.", "Really, it is fine.", "Fine."])
    reminders = [m for m in model.seen[-1]["messages"] if m["role"] == "user"][1:]
    assert len(reminders) == 2 and "tool calls only" in reminders[0]["content"]
    assert (
        run.report.run.stopped_because == "no_report"
        and run.report.verdict == "insufficient_evidence"
    )


def test_an_unknown_tool_or_bad_arguments_do_not_end_the_run():
    run, model = go(
        [
            [call("mint_tokens", amount=5), call("identify_contract", address="nope")],
            [call("identify_contract", address=TOKEN)],
            [submit(**report_body())],
        ]
    )
    errors = [json.loads(m["content"]) for m in tool_messages(model.seen[1])]
    assert "Unknown tool" in errors[0]["error"] and "Invalid arguments" in errors[1]["error"]
    assert run.report.validation.accepted and run.report.run.tool_calls == 3


def test_the_tool_call_cap_stops_new_calls_and_leaves_only_submit_report():
    steps = [
        [
            call("identify_contract", address=TOKEN),
            call("risk_scan", address=TOKEN),
            call("read_getter", address=TOKEN, getter="name"),
        ],
        [submit(**report_body())],
    ]
    run, model = go(steps, limits=Limits(max_tool_calls=2))
    third = json.loads(tool_messages(model.seen[1])[2]["content"])
    assert "No more tool calls" in third["error"]
    assert model.seen[1]["tools"] == ["submit_report"] and run.report.run.tool_calls == 2
    assert any(
        "close to this run's limits" in m["content"]
        for m in model.seen[1]["messages"]
        if m["role"] == "user"
    )
    assert run.report.validation.accepted


def test_the_turn_cap_ends_the_run_and_wrap_up_offers_only_submit_report():
    steps = [[call("read_getter", address=TOKEN, getter=g)] for g in GETTERS[:5]]
    run, model = go(steps, limits=Limits(max_turns=5))
    assert run.report.run.stopped_because == "turn_limit" and run.report.run.turns == 5
    assert [s["tools"] == ["submit_report"] for s in model.seen] == [
        False,
        False,
        False,
        True,
        True,
    ]
    assert (
        run.report.verdict == "insufficient_evidence"
        and "limit on model calls" in run.report.summary
    )


def test_the_cost_cap_stops_the_run():
    run, _ = go(
        [[call("identify_contract", address=TOKEN)], [call("risk_scan", address=TOKEN)]],
        cost=0.3,
        limits=Limits(max_cost_usd=0.5),
    )
    assert (
        run.report.run.stopped_because == "cost_cap"
        and run.report.run.cost_usd == pytest.approx(0.6)
    )


def test_nearing_the_cost_cap_leaves_only_submit_report():
    run, model = go(
        [
            [call("identify_contract", address=TOKEN)],
            [call("risk_scan", address=TOKEN)],
            [submit(**report_body())],
        ],
        cost=0.2,
        limits=Limits(max_cost_usd=0.5),
    )
    assert [s["tools"] == ["submit_report"] for s in model.seen] == [False, False, True]
    assert run.report.validation.accepted


def test_the_time_limit_stops_the_run():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()

    def slow(messages):
        clock.now += 500
        return [call("identify_contract", address=TOKEN)]

    run, _ = go([slow], limits=Limits(wall_clock_seconds=100), clock=clock)
    assert run.report.run.stopped_because == "time_limit" and run.report.run.seconds == 500.0


def test_a_model_that_cannot_be_reached_ends_the_run_and_says_why():
    run, _ = go([ModelUnavailable("OpenRouter error 402: the OpenRouter account has no credits.")])
    assert run.report.run.stopped_because == "model_unavailable" and "no credits" in run.error
    assert run.report.verdict == "insufficient_evidence" and run.report.run.turns == 0


def test_a_blocked_sell_rehearsal_cannot_be_reported_below_high_risk(monkeypatch):
    blocked = SellSimulation(
        verdict="sell_blocked",
        reason="sell_reverted",
        trade_wei=10**16,
        pool=None,
        tokens_received=None,
        wrapped_native_back_wei=None,
        buy_tax_bps=None,
        sell_tax_bps=None,
        round_trip_loss_bps=None,
        tokens_left_after_sell=None,
        failure=None,
        notes=(),
    )
    world = erc20_world()
    monkeypatch.setattr(
        "agent.tools.simulate_sell",
        lambda client, token, runner=None: ToolResult[SellSimulation](
            provenance=client.provenance(token, source="foundry-fork"), data=blocked
        ),
    )
    soft = report_body(verdict="no_red_flags_found")
    hard = report_body(
        verdict="high_risk",
        findings=[
            {
                "claim": "The sell rehearsal was blocked.",
                "kind": "observation",
                "severity": "high",
                "evidence_ids": ["T2"],
            }
        ],
    )
    run, model = go(
        [
            [call("identify_contract", address=TOKEN), call("simulate_sell", token=TOKEN)],
            [submit(**soft)],
            [submit(**hard)],
        ],
        world=world,
    )
    problem = json.loads(tool_messages(model.seen[2])[-1]["content"])["problems"][0]
    assert problem["code"] == "verdict_below_floor"
    assert run.report.verdict == "high_risk" and run.report.validation.attempts == 2


def test_an_invalid_address_is_refused_before_anything_runs():
    with pytest.raises(ChainError, match="Not a valid EVM address"):
        go([], address="0x123")


def test_a_trace_holds_the_conversation_the_evidence_and_the_report(tmp_path):
    run, _ = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    path = write_trace(run, Limits(), tmp_path)
    saved = json.loads(path.read_text())
    assert path.name.endswith("-ethereum-22222222.json")
    assert saved["report"]["verdict"] == "no_red_flags_found"
    assert [e["id"] for e in saved["evidence"]] == ["T1"]
    assert saved["messages"][0]["role"] == "system" and len(saved["turns"]) == 2
    assert saved["limits"]["max_turns"] == 12


def test_the_readable_report_lists_findings_citations_and_what_was_not_checked():
    run, _ = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    text = render_text(run.report)
    assert "Verdict: NO RED FLAGS FOUND" in text and "[observation, info]" in text
    assert "T1: identify_contract(address=" in text
    assert "Who controls the contract: No owner read was made." in text
    assert "get_verified_source: the verified source code" in text and "not available" in text


def test_a_report_that_calls_the_source_unverified_without_a_source_check_goes_back():
    """The bug this rule exists for: with no source tool in the run, a report said 'no verified
    source is available' and 'treat it as unverified'. A missing tool means 'not checked'."""
    bad = report_body(
        summary="Nothing found. No verified source is available, so treat it as unverified."
    )
    good = report_body(
        summary="Nothing found. Whether the source is published was not checked in this run."
    )
    run, model = go([[call("identify_contract", address=TOKEN)], [submit(**bad)], [submit(**good)]])
    problem = json.loads(tool_messages(model.seen[2])[-1]["content"])["problems"][0]
    assert problem["code"] == "unsupported_unverified_wording" and problem["where"] == "summary"
    assert run.report.validation.accepted and run.report.validation.attempts == 2


def test_with_an_explorer_a_verified_false_answer_licenses_the_wording():
    explorer = FakeExplorer(getsourcecode=[UNVERIFIED_ROW])
    steps = [
        [call("identify_contract", address=TOKEN), call("get_verified_source", address=TOKEN)],
        [
            submit(
                **report_body(
                    summary="Nothing found. The explorer has no verified source: unverified."
                )
            )
        ],
    ]
    run, _ = go(steps, services=offline_services(explorer=explorer))
    assert run.report.validation.accepted and run.report.validation.attempts == 1
