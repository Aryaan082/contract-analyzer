"""Running one test on a local EVM, offline, in a throwaway folder (Aryaan, 2026-11-05).

`forge test` deploys the contract on an in-process EVM on this machine and runs the test against
it: no chain to set up, no network, no keys. The test is untrusted model output, so the run is
fenced in four ways:

* the folder: a fresh temporary directory outside the repo, deleted afterwards. The project's files
  and the dependencies are copied in; nothing the test does outlives the run;
* the config: `ffi = false`, no `fs_permissions`, no RPC endpoint, `offline = true`, so the test
  cannot read a file, run a program, reach a fork or make forge download a compiler;
* the process: `sandbox-exec` (macOS) with the network denied and writes allowed only in the
  folder (and forge's own cache); the environment holds PATH, HOME and TMPDIR and nothing else, so
  no API key reaches the process; a timeout kills the whole process group;
* the text: `verify.poc.check_test_text` refuses a test that uses a banned cheatcode first.

The compiler must already be installed (`~/.svm`); this module never downloads anything. If it is
not, or `forge` is missing, the status is `tool_missing` and the flag is unverifiable.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from verify.poc import TEST_CONTRACT

RUN_STATUSES = ("passed", "failed", "compile_error", "timeout", "tool_missing", "no_tests")
DEFAULT_TIMEOUT = 120.0
TAIL_CHARS = 3_000
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
TEST_PATH = "test/PocTest.t.sol"


@dataclass(frozen=True)
class Project:
    """What one run needs. `files` are the audited project's files by their path in the bundle;
    `deps` is a folder whose subfolders (forge-std, openzeppelin-contracts, ...) are copied to
    `lib/`; `remappings` map import prefixes to them (`forge-std/=lib/forge-std/src/`)."""

    files: Mapping[str, str]
    test_file: str
    solc: str
    remappings: tuple[str, ...] = ()
    deps: Path | None = None
    evm_version: str = (
        ""  # the project's own setting (Radius pins london); '' is the compiler default
    )


@dataclass(frozen=True)
class RunResult:
    status: str
    detail: str  # words of ours
    tail: str = ""  # the end of forge's output; shown to the test writer on a retry, never stored
    seconds: float = 0.0
    passed: int = 0
    failed: int = 0


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


CommandRunner = Callable[[list[str], Path, Mapping[str, str], float], Completed]


def foundry_toml(solc: str, evm_version: str = "") -> str:
    return "\n".join([
        "[profile.default]",
        'src = "src"', 'test = "test"', 'out = "out"', 'libs = ["lib"]', 'cache_path = "cache"',
        f'solc = "{solc}"', "auto_detect_solc = false", "offline = true",
        *([f'evm_version = "{evm_version}"'] if evm_version else []),
        "ffi = false", "fs_permissions = []", "no_storage_caching = true",
        "optimizer = false", 'bytecode_hash = "none"',
        "[fuzz]", "runs = 64",
        "",
    ])  # fmt: skip


def safe_relative(path: str) -> PurePosixPath:
    """A bundle path as a relative path that cannot leave the folder."""
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts or "\0" in path:
        raise ValueError(f"unsafe path {path[:60]!r}")
    return p


def write_project(work: Path, project: Project) -> None:
    """Lay the folder out: the project's files at their paths, the test, the config, the libs."""
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    for path, text in project.files.items():
        target = work / safe_relative(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (work / TEST_PATH).parent.mkdir(parents=True, exist_ok=True)
    (work / TEST_PATH).write_text(project.test_file)
    (work / "foundry.toml").write_text(foundry_toml(project.solc, project.evm_version))
    (work / "remappings.txt").write_text("\n".join(project.remappings) + "\n")
    if project.deps is not None:
        for child in sorted(project.deps.iterdir()):
            if child.is_dir():
                shutil.copytree(
                    child, work / "lib" / child.name,
                    ignore=shutil.ignore_patterns(".git", "node_modules"),
                )  # fmt: skip


def sandbox_profile(work: Path, home: Path) -> str:
    """macOS sandbox rules: everything allowed except the network, and writes only in `work` and
    forge's own folder. Paths must be real paths (a /var/... temp folder is /private/var/...)."""
    return "\n".join([
        "(version 1)", "(allow default)", "(deny network*)", "(deny file-write*)",
        f'(allow file-write* (subpath "{work}") (subpath "{home}/.foundry")'
        ' (literal "/dev/null") (literal "/dev/dtracehelper") (literal "/dev/tty"))',
        "",
    ])  # fmt: skip


def scrubbed_env(work: Path, forge: str) -> dict[str, str]:
    """The environment of the forge process: no API key, no RPC URL, no token."""
    forge_dir = str(Path(shutil.which(forge) or forge).parent)
    return {
        "PATH": f"{forge_dir}:/usr/bin:/bin", "HOME": str(Path.home()),
        "TMPDIR": str(work / "tmp"), "NO_COLOR": "1", "FOUNDRY_DISABLE_NIGHTLY_WARNING": "1",
    }  # fmt: skip


def run_group(cmd: list[str], cwd: Path, env: Mapping[str, str], timeout: float) -> Completed:
    """Run a command in its own process group and kill the whole group on timeout."""
    proc = subprocess.Popen(  # noqa: S603
        cmd, cwd=cwd, env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )  # fmt: skip
    try:
        out, err = proc.communicate(timeout=timeout)
        return Completed(proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        return Completed(-9, out or "", err or "", timed_out=True)


def parse_forge(done: Completed) -> RunResult:
    """forge's `--json` output (or its error text) as a RunResult."""
    text = (done.stdout or "") + (done.stderr or "")
    if done.timed_out:
        return RunResult("timeout", "the test ran past the time limit")
    try:
        data = json.loads(done.stdout)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        results = [
            r for suite in data.values() if isinstance(suite, dict)
            for r in (suite.get("test_results") or {}).values() if isinstance(r, dict)
        ]  # fmt: skip
        if results:
            ok = [r.get("success") is True or r.get("status") == "Success" for r in results]
            passed, failed = sum(ok), len(ok) - sum(ok)
            reasons = "\n".join(str(r.get("reason")) for r in results if r.get("reason"))
            status = "passed" if failed == 0 and done.returncode == 0 else "failed"
            detail = f"{passed} passed, {failed} failed"
            return RunResult(status, detail, reasons[-TAIL_CHARS:], passed=passed, failed=failed)
    lower = text.lower()
    if "no tests match" in lower or "no tests found" in lower:
        return RunResult("no_tests", "forge found no test to run", text[-TAIL_CHARS:])
    if any(w in lower for w in ("offline", "no such file or directory", "failed to install")) and (
        "solc" in lower
    ):
        return RunResult("tool_missing", "the compiler is not installed", text[-TAIL_CHARS:])
    if done.returncode != 0:
        return RunResult("compile_error", "forge could not build or run it", text[-TAIL_CHARS:])
    return RunResult("no_tests", "forge printed no test result", text[-TAIL_CHARS:])


class ForgeRunner:
    """Runs a Project's test. `command_runner` is replaced in tests."""

    def __init__(
        self,
        forge: str = "forge",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        sandbox: bool = True,
        command_runner: CommandRunner = run_group,
    ) -> None:
        self.forge, self.timeout, self.sandbox = forge, timeout, sandbox
        self.command_runner = command_runner

    def command(self, work: Path) -> list[str]:
        cmd = [
            self.forge, "test", "--root", str(work), "--offline", "--json",
            "--match-contract", f"^{TEST_CONTRACT}$",
        ]  # fmt: skip
        if not self.sandbox:
            return cmd
        profile = work.parent / "sandbox.sb"
        profile.write_text(sandbox_profile(work, Path.home().resolve()))
        return [SANDBOX_EXEC, "-f", str(profile), *cmd]

    def run(self, project: Project) -> RunResult:
        if self.sandbox and not Path(SANDBOX_EXEC).exists():
            return RunResult("tool_missing", "sandbox-exec is not available on this machine")
        if shutil.which(self.forge) is None and not Path(self.forge).exists():
            return RunResult("tool_missing", "forge is not installed")
        with tempfile.TemporaryDirectory(prefix="verify-") as tmp:
            work = Path(tmp).resolve() / "work"
            write_project(work, project)
            started = time.monotonic()
            done = self.command_runner(
                self.command(work), work, scrubbed_env(work, self.forge), self.timeout
            )
            result = parse_forge(done)
        return RunResult(
            result.status, result.detail, result.tail, round(time.monotonic() - started, 2),
            result.passed, result.failed,
        )  # fmt: skip
