"""The forge runner: the folder it builds, the fence round the process and how output is read.
forge is never called except by the one test that checks the sandbox profile with a plain shell."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.verify_common import FILES, GOOD_TEST
from verify import runner as rn


def project(**kw) -> rn.Project:
    base = {"files": FILES, "test_file": GOOD_TEST, "solc": "0.8.20"}
    return rn.Project(**{**base, **kw})


def suite(results: dict) -> str:
    return json.dumps({"test/PocTest.t.sol:PocTest": {"test_results": results}})


def test_the_config_turns_off_everything_a_test_could_use_to_leave_the_evm():
    text = rn.foundry_toml("0.8.20")
    for line in ("ffi = false", "fs_permissions = []", "offline = true", 'solc = "0.8.20"',
                 "auto_detect_solc = false", "no_storage_caching = true"):  # fmt: skip
        assert line in text.splitlines()
    assert "rpc_endpoints" not in text and "etherscan" not in text


def test_the_projects_evm_version_goes_into_the_config_when_it_has_one():
    assert 'evm_version = "london"' in rn.foundry_toml("0.8.20", "london").splitlines()
    assert "evm_version" not in rn.foundry_toml("0.8.20")


@pytest.mark.parametrize("bad", ["../x.sol", "/etc/passwd", "a/../../b", "", "a\0b"])
def test_a_path_that_could_leave_the_folder_is_refused(bad):
    with pytest.raises(ValueError, match="unsafe path"):
        rn.safe_relative(bad)


def test_the_folder_is_laid_out_with_files_test_config_and_copied_libraries(tmp_path):
    deps = tmp_path / "deps"
    (deps / "forge-std" / "src").mkdir(parents=True)
    (deps / "forge-std" / "src" / "Test.sol").write_text("// t")
    (deps / "forge-std" / ".git").mkdir()
    work = tmp_path / "work"
    rn.write_project(work, project(deps=deps, remappings=("forge-std/=lib/forge-std/src/",)))
    assert (work / "src/Vault.sol").read_text() == FILES["src/Vault.sol"]
    assert (work / rn.TEST_PATH).read_text() == GOOD_TEST
    assert (work / "foundry.toml").read_text() == rn.foundry_toml("0.8.20")
    rn.write_project(tmp_path / "w2", project(evm_version="london"))
    assert 'evm_version = "london"' in (tmp_path / "w2" / "foundry.toml").read_text()
    assert (work / "remappings.txt").read_text() == "forge-std/=lib/forge-std/src/\n"
    assert (work / "lib/forge-std/src/Test.sol").read_text() == "// t"
    assert not (work / "lib/forge-std/.git").exists() and (work / "tmp").is_dir()


def test_the_environment_of_the_process_holds_no_secret(monkeypatch, tmp_path):
    for name in ("OPENROUTER_API_KEY", "ALCHEMY_URL", "ETHERSCAN_API_KEY", "FOUNDRY_ETH_RPC_URL"):
        monkeypatch.setenv(name, "secret")
    env = rn.scrubbed_env(tmp_path, "forge")
    assert set(env) == {"PATH", "HOME", "TMPDIR", "NO_COLOR", "FOUNDRY_DISABLE_NIGHTLY_WARNING"}
    assert "secret" not in "".join(env.values()) and env["TMPDIR"] == str(tmp_path / "tmp")


def test_the_sandbox_profile_denies_the_network_and_writes_outside_the_folder(tmp_path):
    text = rn.sandbox_profile(tmp_path, Path("/Users/x"))
    assert "(deny network*)" in text and "(deny file-write*)" in text
    assert f'(subpath "{tmp_path}")' in text and '(subpath "/Users/x/.foundry")' in text
    assert text.index("(deny file-write*)") < text.index("(allow file-write*")  # later rules win


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS")
def test_the_real_profile_stops_a_write_outside_and_a_network_connection(tmp_path):
    work = (tmp_path / "work").resolve()
    work.mkdir()
    profile = tmp_path / "p.sb"
    profile.write_text(rn.sandbox_profile(work, Path.home().resolve()))

    def run(code: str) -> subprocess.CompletedProcess:
        return subprocess.run(["/usr/bin/sandbox-exec", "-f", str(profile), "/bin/sh", "-c", code],
                              capture_output=True, text=True)  # fmt: skip

    assert run(f"echo hi > {work}/a").returncode == 0 and (work / "a").exists()
    outside = tmp_path.resolve() / "outside"
    assert run(f"echo hi > {outside}").returncode != 0 and not outside.exists()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        code = f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=2)"
        denied = subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(profile), sys.executable, "-c", code],
            capture_output=True, text=True,
        )  # fmt: skip
        assert denied.returncode != 0 and "PermissionError" in denied.stderr
    finally:
        server.close()


def test_a_command_that_runs_past_the_limit_is_killed_with_its_children(tmp_path):
    pid_file = tmp_path / "child.pid"
    child = f"import os,time; open({str(pid_file)!r},'w').write(str(os.getpid())); time.sleep(30)"
    code = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
    )
    done = rn.run_group([sys.executable, "-c", code], tmp_path, {"PATH": "/usr/bin:/bin"}, 1.5)
    assert done.timed_out and done.returncode == -9
    pid = int(pid_file.read_text())
    for _ in range(40):  # the child is in the same group: it must be gone too
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the child process is still running")


def test_a_command_that_finishes_gives_its_output(tmp_path):
    done = rn.run_group([sys.executable, "-c", "print('hi')"], tmp_path, {"PATH": "/usr/bin"}, 5)
    assert done.stdout.strip() == "hi" and done.returncode == 0 and not done.timed_out


def completed(stdout="", stderr="", code=0, timed_out=False) -> rn.Completed:
    return rn.Completed(code, stdout, stderr, timed_out)


def test_a_passing_suite_is_passed_in_both_json_shapes():
    old = completed(suite({"test_exploit()": {"success": True, "reason": None}}))
    new = completed(suite({"test_exploit()": {"status": "Success", "reason": None}}))
    for done in (old, new):
        got = rn.parse_forge(done)
        assert (got.status, got.passed, got.failed) == ("passed", 1, 0)


def test_a_failing_test_is_failed_and_its_reason_is_kept_for_the_next_attempt():
    done = completed(suite({"test_a()": {"success": False, "reason": "assertion failed: 0 <= 0"},
                            "test_b()": {"success": True}}), code=1)  # fmt: skip
    got = rn.parse_forge(done)
    assert (got.status, got.passed, got.failed) == ("failed", 1, 1)
    assert "assertion failed" in got.tail


def test_one_failing_test_among_passes_is_not_a_pass_even_if_forge_exits_zero():
    done = completed(suite({"test_a()": {"success": True}, "test_b()": {"success": False}}))
    assert rn.parse_forge(done).status == "failed"


def test_other_outcomes_are_named():
    assert rn.parse_forge(completed(timed_out=True)).status == "timeout"
    assert (
        rn.parse_forge(completed("", "Error: Compiler run failed:\nsrc/A.sol:3: Error", 1)).status
        == "compile_error"
    )
    assert (
        rn.parse_forge(completed("No tests match the provided pattern", "", 0)).status == "no_tests"
    )
    assert (
        rn.parse_forge(completed("", "Error: solc 0.8.20 is not installed, offline", 1)).status
        == "tool_missing"
    )
    assert rn.parse_forge(completed("", "", 0)).status == "no_tests"
    assert "Undeclared" in rn.parse_forge(completed("", "Error: Undeclared identifier", 1)).tail


def test_the_command_is_forge_test_offline_on_one_contract_inside_the_sandbox(tmp_path):
    work = tmp_path.resolve() / "work"
    work.mkdir()
    cmd = rn.ForgeRunner("forge").command(work)
    assert (
        cmd[0] == rn.SANDBOX_EXEC
        and cmd[1] == "-f"
        and Path(cmd[2]).read_text().startswith("(version 1)")
    )
    assert cmd[3:6] == ["forge", "test", "--root"] and "--offline" in cmd and "--json" in cmd
    assert cmd[cmd.index("--match-contract") + 1] == "^PocTest$"
    assert rn.ForgeRunner("forge", sandbox=False).command(work)[0] == "forge"


def test_a_run_builds_the_folder_runs_the_command_with_a_clean_env_and_deletes_the_folder(
    monkeypatch,
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    seen = {}

    def fake(cmd, cwd, env, timeout):
        seen.update(
            cmd=cmd, cwd=cwd, env=dict(env), timeout=timeout, test=(cwd / rn.TEST_PATH).read_text()
        )
        return completed(suite({"test_exploit()": {"success": True}}))

    got = rn.ForgeRunner("forge", timeout=7, sandbox=False, command_runner=fake).run(project())
    assert got.status == "passed" and seen["test"] == GOOD_TEST and seen["timeout"] == 7
    assert "OPENROUTER_API_KEY" not in seen["env"] and "--offline" in seen["cmd"]
    assert not seen["cwd"].exists()  # the throwaway folder is gone


def test_a_missing_tool_is_reported_and_nothing_is_run(tmp_path):
    ran = []
    r = rn.ForgeRunner("/no/such/forge", sandbox=False, command_runner=lambda *a: ran.append(a))
    assert r.run(project()).status == "tool_missing" and not ran
