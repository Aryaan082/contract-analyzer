"""Shared pieces of the verify tests: a small contract, a test that is fine, scripted replies."""

import json

from review.client import ReviewerCall, ScriptedReviewer
from verify.runner import RunResult
from verify.types import Candidate

CODE = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
contract Vault {
    mapping(address => uint256) public balance;
    function deposit() external payable { balance[msg.sender] += msg.value; }
    function withdraw(uint256 amount) external {
        (bool ok,) = msg.sender.call{value: amount}("");
        require(ok);
        balance[msg.sender] -= amount;
    }
}
"""
OTHER = """pragma solidity ^0.8.20;
contract Token {
    function mint(address to, uint256 amount) external {}
}
"""
FILES = {"src/Vault.sol": CODE, "src/Token.sol": OTHER}
GOOD_TEST = """// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.20;
import {Test} from "forge-std/Test.sol";
import {Vault} from "src/Vault.sol";
contract PocTest is Test {
    Vault vault;
    address attacker = makeAddr("attacker");
    function setUp() public {
        vault = new Vault();
        vm.deal(attacker, 1 ether);
    }
    function test_exploit() public {
        vm.prank(attacker);
        vault.deposit{value: 1 ether}();
        assertGt(address(vault).balance, 0);
    }
}
"""
WRITER = "deepseek/deepseek-v4.1-flash-1017"
READER = "z-ai/glm-5.3-flash"


def candidate(**kw) -> Candidate:
    base = dict(report="r", file="src/Vault.sol", contract="Vault", function="withdraw",
                start_line=6, end_line=10, note="state is updated after the external call",
                severity="high", layers=frozenset({"plain"}))  # fmt: skip
    return Candidate(**{**base, **kw})


def poc_reply(test: str = GOOD_TEST, **kw) -> str:
    return json.dumps({"feasible": True, "test_file": test, **kw})


def verdict_reply(verdict: str = "accept") -> str:
    return json.dumps({"verdict": verdict, "reason": "r"})


def triage_reply(kind: str = "logic", confidence: str = "high", reason: str = "r") -> str:
    return json.dumps({"kind": kind, "confidence": confidence, "reason": reason})


def writer(replies, **kw) -> ScriptedReviewer:
    return ScriptedReviewer(replies, name=WRITER, **kw)


def reader(replies, **kw) -> ScriptedReviewer:
    return ScriptedReviewer(replies, name=READER, **kw)


def call(text: str, **kw) -> ReviewerCall:
    return ReviewerCall(text=text, **kw)


class StubRunner:
    """Stands in for ForgeRunner: answers from a list of RunResults, keeps the projects it got."""

    def __init__(self, *results: RunResult) -> None:
        self.results, self.projects = list(results), []

    def run(self, project) -> RunResult:
        self.projects.append(project)
        return self.results[min(len(self.projects), len(self.results)) - 1]


PASSED = RunResult("passed", "1 passed, 0 failed", passed=1)
FAILED = RunResult("failed", "0 passed, 1 failed", "assertion failed: 0 > 0", failed=1)
COMPILE = RunResult(
    "compile_error", "forge could not build or run it", "Error: Undeclared identifier"
)
