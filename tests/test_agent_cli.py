"""`python -m agent`: argument handling, and one whole run with the model and the chain scripted."""

import json

import agent.cli as cli
from tests.agent_support import TOKEN, ScriptedModel, call, erc20_world, report_body, submit


def scripted(monkeypatch, steps):
    world = erc20_world()
    model = ScriptedModel(steps)
    monkeypatch.setattr(cli.RpcClient, "connect", lambda chain, env, block=None: world)
    monkeypatch.setattr(cli, "OpenRouterModel", lambda spec, timeout: model)
    return model


def test_an_address_or_a_contract_id_is_required(capsys):
    assert cli.main(["analyze"], env={}) == 2
    assert "Give an address" in capsys.readouterr().err
    assert cli.main(["analyze", "--contract", "nope"], env={}) == 2
    assert "No contract 'nope'" in capsys.readouterr().err


def test_a_whole_run_prints_a_readable_report_and_exits_zero(monkeypatch, capsys):
    scripted(monkeypatch, [[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    assert cli.main(["analyze", TOKEN, "--no-trace"], env={}) == 0
    out = capsys.readouterr().out
    assert "Verdict: NO RED FLAGS FOUND" in out and "T1: identify_contract" in out


def test_json_output_is_the_report_and_a_trace_is_written_unless_declined(
    monkeypatch, capsys, tmp_path
):
    scripted(monkeypatch, [[call("identify_contract", address=TOKEN)], [submit(**report_body())]])
    monkeypatch.setattr("agent.trace.RUNS_DIR", tmp_path)
    monkeypatch.setattr(cli, "write_trace", lambda run, limits: tmp_path / "t.json")
    assert cli.main(["analyze", TOKEN, "--json", "--max-cost", "0.2"], env={}) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["verdict"] == "no_red_flags_found"
    assert "trace:" in captured.err


def test_a_report_that_failed_validation_exits_nonzero_and_a_missing_key_is_explained(
    monkeypatch, capsys
):
    from agent.llm import ModelUnavailable

    scripted(monkeypatch, [ModelUnavailable("OPENROUTER_API_KEY is not set. Add it to .env.")])
    assert cli.main(["analyze", TOKEN, "--no-trace"], env={}) == 1
    captured = capsys.readouterr()
    assert (
        "OPENROUTER_API_KEY is not set" in captured.err and "INSUFFICIENT EVIDENCE" in captured.out
    )


def test_the_model_option_takes_a_tier_or_any_openrouter_slug_and_leaves_the_default_alone(
    monkeypatch,
):
    seen = []
    world = erc20_world()
    monkeypatch.setattr(cli.RpcClient, "connect", lambda chain, env, block=None: world)

    def fake_model(spec, timeout):
        seen.append(spec.name)
        return ScriptedModel(
            [[call("identify_contract", address=TOKEN)], [submit(**report_body())]]
        )

    monkeypatch.setattr(cli, "OpenRouterModel", fake_model)
    for flag in ([], ["--model", "haiku"], ["--model", "vendor/some-model-1"]):
        steps = ["analyze", TOKEN, "--no-trace", *flag]
        assert cli.main(steps, env={}) == 0
    assert seen == [
        "anthropic/claude-sonnet-5.5",
        "anthropic/claude-haiku-5.5",
        "vendor/some-model-1",
    ]
