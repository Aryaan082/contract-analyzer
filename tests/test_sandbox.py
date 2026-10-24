"""chain/sandbox.py: how Foundry is started and its result read, with the child process faked."""

import json
import subprocess
from types import SimpleNamespace

import pytest

import chain.sandbox as sandbox
from chain.sandbox import (
    RehearsalParams,
    SandboxFailed,
    SandboxUnavailable,
    parse_result,
    run_rehearsal,
)

URL = "https://node.invalid/v2/SECRETKEY123"
PARAMS = RehearsalParams(
    token="0x" + "11" * 20,
    router="0x" + "22" * 20,
    factory="0x" + "33" * 20,
    wrapped_native="0x" + "44" * 20,
    mode="seed",
    trade_wei=10**16,
    seed_wei=10 * 10**18,
    gas_price=21 * 10**9,
)
GOOD = {
    "ok": True,
    "step": "",
    "revert": "0x",
    "pair": "0x" + "55" * 20,
    "mode": 1,
    "provider": "invented",
    "native_reserve": 10**19,
    "received": 990,
    "pool_gave": 1000,
    "arrived": 940,
    "native_back": 9 * 10**15,
    "left": 0,
}


@pytest.fixture
def fake_forge(monkeypatch, tmp_path):
    """Pretend forge is installed, run in a temp project, and let each test script the child."""
    (tmp_path / "results").mkdir()
    (tmp_path / "foundry.toml").write_text("")
    monkeypatch.setattr(sandbox, "SANDBOX_DIR", tmp_path)
    monkeypatch.setattr(sandbox, "forge_binary", lambda: "/usr/bin/forge")
    seen = SimpleNamespace(calls=[])

    def script(*behaviours):
        queue = list(behaviours)

        def run(command, **kwargs):
            seen.calls.append((command, kwargs))
            behaviour = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(behaviour, BaseException):
                raise behaviour
            if behaviour is not None:  # a result for the child to "write"
                (tmp_path / kwargs["env"]["SANDBOX_OUT"]).write_text(json.dumps(behaviour))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(sandbox.subprocess, "run", run)
        return seen

    seen.script = script
    seen.dir = tmp_path
    return seen


def test_the_child_gets_everything_in_its_environment_and_the_url_never_on_the_command_line(
    fake_forge,
):
    fake_forge.script(GOOD)
    result = run_rehearsal(URL, 12345, PARAMS)
    ((command, kwargs),) = fake_forge.calls
    env = kwargs["env"]
    assert env["FOUNDRY_ETH_RPC_URL"] == URL and env["FOUNDRY_FORK_BLOCK_NUMBER"] == "12345"
    assert (env["SANDBOX_TOKEN"], env["SANDBOX_MODE"]) == (PARAMS.token, "1")
    assert env["SANDBOX_TRADE_WEI"] == str(10**16) and env["SANDBOX_GAS_PRICE"] == str(21 * 10**9)
    assert "SECRETKEY123" not in " ".join(command)
    assert set(env) >= {"PATH", "HOME"} and "RPC_URL_BASE" not in env  # nothing else is passed on
    assert kwargs["cwd"] == fake_forge.dir and kwargs["timeout"] == sandbox.TIMEOUT_SECONDS
    assert result.ok and result.received == 990 and result.provider == "invented"


def test_the_result_file_is_removed_afterwards(fake_forge):
    fake_forge.script(GOOD)
    run_rehearsal(URL, 1, PARAMS)
    assert list((fake_forge.dir / "results").iterdir()) == []


def test_a_run_that_writes_nothing_is_retried_once_then_fails(fake_forge):
    fake_forge.script(None)
    with pytest.raises(SandboxFailed, match="no result"):
        run_rehearsal(URL, 1, PARAMS)
    assert len(fake_forge.calls) == 2


def test_a_retry_that_works_is_accepted(fake_forge):
    fake_forge.script(None, GOOD)
    assert run_rehearsal(URL, 1, PARAMS).ok
    assert len(fake_forge.calls) == 2


def test_a_timeout_is_a_failure_not_a_hang(fake_forge):
    fake_forge.script(subprocess.TimeoutExpired("forge", 180))
    with pytest.raises(SandboxFailed, match="timed out"):
        run_rehearsal(URL, 1, PARAMS)


def test_a_missing_binary_or_project_means_unavailable(monkeypatch, fake_forge):
    monkeypatch.setattr(sandbox, "forge_binary", lambda: None)
    with pytest.raises(SandboxUnavailable):
        run_rehearsal(URL, 1, PARAMS)
    monkeypatch.setattr(sandbox, "forge_binary", lambda: "/usr/bin/forge")
    (fake_forge.dir / "foundry.toml").unlink()
    with pytest.raises(SandboxUnavailable):
        run_rehearsal(URL, 1, PARAMS)


def test_failure_logs_and_messages_never_contain_the_url(fake_forge, monkeypatch, caplog):
    def noisy(command, **kwargs):
        return SimpleNamespace(
            returncode=1, stdout="", stderr=f"error: could not reach {URL} (key SECRETKEY123)"
        )

    monkeypatch.setattr(sandbox.subprocess, "run", noisy)
    with caplog.at_level("WARNING", logger="chain.sandbox"), pytest.raises(SandboxFailed) as caught:
        run_rehearsal(URL, 1, PARAMS)
    assert "https://node.invalid" not in caplog.text and "<url>" in caplog.text
    assert "SECRETKEY123" not in str(caught.value)


def test_a_result_that_is_not_json_is_a_failure(fake_forge, monkeypatch):
    def run(command, **kwargs):
        (fake_forge.dir / kwargs["env"]["SANDBOX_OUT"]).write_text("not json")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox.subprocess, "run", run)
    with pytest.raises(SandboxFailed, match="not JSON"):
        run_rehearsal(URL, 1, PARAMS)


# --- reading the result ----------------------------------------------------------------------


def test_a_good_result_is_parsed():
    result = parse_result(GOOD)
    assert result.revert == b"" and result.pair == "0x" + "55" * 20 and result.arrived == 940


def test_a_failed_step_carries_its_revert_data():
    failed = {**GOOD, "ok": False, "step": "sell", "revert": "0x08c379a0abcd"}
    result = parse_result(failed)
    assert (
        not result.ok and result.step == "sell" and result.revert == bytes.fromhex("08c379a0abcd")
    )


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {**GOOD, "received": -1},
        {**GOOD, "received": "990"},
        {**GOOD, "received": True},
        {**GOOD, "ok": "yes"},
        {**GOOD, "ok": False, "step": "mystery"},
        {**GOOD, "provider": "someone"},
        {**GOOD, "revert": "08c379a0"},
        {**GOOD, "revert": "0x" + "00" * 5000},
        {**GOOD, "revert": "0xzz"},
        {**GOOD, "pair": 5},
    ],
)
def test_anything_unexpected_in_the_result_is_a_failure(bad):
    with pytest.raises(SandboxFailed):
        parse_result(bad)
