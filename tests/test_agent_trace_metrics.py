"""What the trace records for Component 6: seconds per model call and per tool call, fingerprints,
every submit attempt, store hits, and the machine. None of it may change what a run does, which the
older agent tests (unchanged) check; these check that the new numbers are right."""

import json

import pytest

from agent.config import Limits
from agent.llm import ToolCall
from agent.loop import _call_record, run_agent
from agent.runmeta import digest, environment
from agent.tools import CountedStore, RunContext
from agent.trace import write_trace
from tests.agent_support import (
    TOKEN,
    ScriptedModel,
    call,
    erc20_world,
    offline_services,
    report_body,
    submit,
)
from tests.test_agent_llm import MESSAGES, TOOLS, http_error, model, payload


class Clocks:
    """A monotonic clock and a time-of-day clock that only move when a test moves them. `slept`
    moves the time of day alone, as a laptop asleep would."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def mono(self) -> float:
        return self.now

    def wall(self) -> float:
        return 1_700_000_000.0 + self.now + self.slept


def go(steps, clocks=None, *, limits=None, execute=None, monkeypatch=None):
    clocks = clocks or Clocks()
    world = erc20_world()
    scripted = ScriptedModel(steps)
    if execute is not None:
        monkeypatch.setattr("agent.loop.execute", execute)
    run = run_agent(
        scripted,
        RunContext(world, world.chain, services=offline_services()),
        TOKEN,
        limits=limits or Limits(),
        clock=clocks.mono,
        wall=clocks.wall,
    )
    return run, scripted


def test_seconds_are_split_between_the_model_and_the_tools(monkeypatch):
    clocks = Clocks()

    def thinking(seconds, then):
        def step(messages):
            clocks.now += seconds
            return then

        return step

    from agent.tools import execute as real_execute

    def slow_execute(ctx, tool_call, tools):
        clocks.now += 3.0  # every tool call takes 3 s
        return real_execute(ctx, tool_call, tools)

    run, _ = go(
        [
            thinking(2.0, [call("identify_contract", address=TOKEN)]),
            thinking(2.0, [submit(**report_body())]),
        ],
        clocks,
        execute=slow_execute,
        monkeypatch=monkeypatch,
    )
    metrics = run.metrics
    assert metrics["model_s"] == 4.0 and metrics["tools_s"] == 3.0
    assert metrics["loop_s"] == 7.0 and metrics["total_s"] == 7.0 and metrics["setup_s"] == 0.0
    assert metrics["overhead_s"] == 0.0 and metrics["skew_s"] == 0.0
    assert metrics["model_calls"] == 2 and metrics["tools_run"] == 1
    assert [t["seconds"] for t in run.turns] == [2.0, 2.0]
    (only,) = run.calls
    assert only["tool"] == "identify_contract" and only["seconds"] == 3.0 and only["ran"]
    assert metrics["started_at"] == "2023-11-14T22:13:20+00:00"
    assert run.report.run.seconds == 7.0  # the number that was always there is unchanged


def test_a_run_during_which_the_machine_slept_shows_a_gap_between_the_two_clocks():
    clocks = Clocks()

    def sleepy(messages):
        clocks.now += 1.0
        clocks.slept += 100.0  # the laptop slept: time of day moved, the monotonic clock did not
        return [call("identify_contract", address=TOKEN)]

    run, _ = go([sleepy, [submit(**report_body())]], clocks)
    assert run.metrics["skew_s"] == 100.0
    assert run.metrics["wall_s"] == 101.0 and run.metrics["loop_s"] == 1.0
    assert run.turns[0]["wall_seconds"] == 101.0 and run.turns[0]["seconds"] == 1.0


def test_setup_time_before_the_loop_starts_is_counted_in_the_total_only():
    clocks = Clocks()
    world = erc20_world()
    pin, pins = world.pin, []

    def slow_pin():
        if not pins:  # only the run's own first pin is slow; the tools pin again, quickly
            clocks.now += 4.0
        pins.append(1)
        return pin()

    world.pin = slow_pin
    run = run_agent(
        ScriptedModel([[call("identify_contract", address=TOKEN)], [submit(**report_body())]]),
        RunContext(world, world.chain, services=offline_services()),
        TOKEN,
        clock=clocks.mono,
        wall=clocks.wall,
    )
    assert run.metrics["setup_s"] == 4.0 and run.metrics["loop_s"] == 0.0
    assert run.metrics["total_s"] == 4.0 and run.report.run.seconds == 0.0


def test_a_repeated_call_is_marked_and_a_refused_one_did_not_run():
    run, _ = go(
        [
            [
                call("identify_contract", address=TOKEN),
                call("identify_contract", address=TOKEN),
                call("list_selectors", address=TOKEN),
            ],
            [submit(**report_body())],
        ],
        limits=Limits(max_tool_calls=2),
    )
    first, second, refused = run.calls
    assert first["evidence_id"] == "T1" and not first["duplicate"] and first["ok"] is True
    assert second["duplicate"] and second["ran"]
    assert refused["ran"] is False and refused["tool"] == "list_selectors"
    assert "seconds" not in refused


def test_the_result_fingerprint_ignores_the_evidence_id_but_not_the_content():
    one = ToolCall(id="a", name="risk_scan", arguments='{"address": "0x1"}')

    def record(result):
        return _call_record(1, one, json.dumps(result), 0.5, 0.5)

    same_content = {"tool": "risk_scan", "ok": True, "data": {"signals": [1, 2]}}
    a = record({"evidence_id": "T3", **same_content})
    b = record({"evidence_id": "T7", **same_content})
    c = record({"evidence_id": "T3", **same_content, "data": {"signals": [1]}})
    assert a["result_sha"] == b["result_sha"] != c["result_sha"]
    assert a["arguments_sha"] == digest('{"address": "0x1"}')
    cut_off = _call_record(1, one, '{"evidence_id": "T1", "data": "truncat', 0.1, 0.1)
    assert cut_off["result_sha"] and cut_off["evidence_id"] is None and cut_off["error"] is False


def test_every_submit_attempt_is_recorded_with_its_problems():
    invented = report_body(
        findings=[
            {
                "claim": "A real claim.",
                "kind": "observation",
                "severity": "info",
                "evidence_ids": ["T1"],
            },
            {
                "claim": "An invented one.",
                "kind": "observation",
                "severity": "info",
                "evidence_ids": ["T99"],
            },
        ]
    )
    broken = ToolCall(id="broken_1", name="submit_report", arguments="{not json")
    run, _ = go(
        [
            [call("identify_contract", address=TOKEN)],
            [broken],
            [submit(**invented)],
            [submit(**report_body())],
        ]
    )
    schema, rejected, accepted = run.attempts
    assert schema["parsed"] is False and schema["claims"] is None and not schema["accepted"]
    assert schema["problems"] == [{"where": "report", "code": "schema_error"}]
    assert rejected["parsed"] and rejected["claims"] == 2 and rejected["claims_failing"] == 1
    assert {"where": "findings[1]", "code": "unknown_evidence"} in rejected["problems"]
    assert accepted["accepted"] and accepted["problems"] == [] and accepted["claims_failing"] == 0
    assert [a["attempt"] for a in run.attempts] == [1, 2, 3]
    assert run.report.validation.attempts == 3


def test_counters_count_code_reads_and_store_lookups():
    run, _ = go(
        [
            [call("identify_contract", address=TOKEN), call("list_selectors", address=TOKEN)],
            [submit(**report_body())],
        ]
    )
    assert run.metrics["counters"] == {"code_reads": 1}  # no code store in this test


def test_the_counting_store_changes_nothing_and_counts_lookups_and_hits():
    seen = {}
    counted = []

    class Inner:
        def find(self, chain_id, address, block):
            return seen.get((chain_id, address, block))

        def save(self, chain_id, address, block, code_hash, code):
            seen[(chain_id, address, block)] = (code_hash, code)

    store = CountedStore(Inner(), counted.append)
    assert store.find(1, "0xa", 5) is None
    store.save(1, "0xa", 5, "0xh", b"\x01")
    assert store.find(1, "0xa", 5) == ("0xh", b"\x01")
    assert counted == ["code_store_lookups", "code_store_lookups", "code_store_hits"]


def test_the_prompt_and_tools_are_fingerprinted():
    one, _ = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    two, _ = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    assert one.metrics["system_prompt_sha"] == two.metrics["system_prompt_sha"]
    assert one.metrics["tools_sha"] == two.metrics["tools_sha"]
    assert len(one.metrics["tools_sha"]) == 16


def test_the_trace_file_carries_the_new_keys_and_nothing_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SECRET-0123456789")
    monkeypatch.setenv("RPC_URL_ETHEREUM", "https://node.example/v2/SECRET-RPC-KEY")
    run, _ = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    path = write_trace(run, Limits(), tmp_path, tags={"concurrent": True})
    saved = json.loads(path.read_text())
    assert [c["tool"] for c in saved["calls"]] == ["identify_contract"]
    assert len(saved["attempts"]) == 1 and saved["metrics"]["schema"] == 2
    assert saved["tags"] == {"concurrent": True}
    assert {"git_commit", "git_dirty", "models_toml_sha", "forge", "cpu_count", "python"} <= set(
        saved["environment"]
    )
    assert "SECRET" not in path.read_text()


def test_a_trace_without_tags_has_empty_tags(tmp_path):
    run, _ = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    saved = json.loads(write_trace(run, Limits(), tmp_path, env={"x": 1}).read_text())
    assert saved["tags"] == {} and saved["environment"] == {"x": 1}


def test_the_environment_is_looked_up_once_and_never_raises(monkeypatch):
    monkeypatch.setenv("AGENT_NETWORK_LABEL", "test lab")
    environment.cache_clear()
    try:
        info = environment()
        assert info["network_label"] == "test lab" and info["cpu_count"]
        assert environment() is info
    finally:
        environment.cache_clear()


def test_the_model_client_reports_the_provider_and_how_many_requests_failed_first():
    made, _ = model(http_error(503), http_error(503), payload(provider="Some Host"))
    reply = made.complete(MESSAGES, TOOLS, max_tokens=10)
    assert reply.retries == 2 and reply.provider == "Some Host"
    plain, _ = model(payload())
    reply = plain.complete(MESSAGES, TOOLS, max_tokens=10)
    assert reply.retries == 0 and reply.provider is None


@pytest.mark.parametrize("junk", [None, 5, ["x"]])
def test_a_provider_that_is_not_text_is_ignored(junk):
    reply = model(payload(provider=junk))[0].complete(MESSAGES, TOOLS, max_tokens=10)
    assert reply.provider is None


def test_two_traces_written_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    first, _ = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    second, _ = go([[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    paths = {write_trace(r, Limits(), tmp_path, env={}) for r in (first, second, first)}
    assert len(paths) == 3 and len(list(tmp_path.glob("*.json"))) == 3
    assert all(p.name.endswith("22222222.json") or "22222222-" in p.name for p in paths)
