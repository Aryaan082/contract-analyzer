"""Shared pieces of the verify tests: a small contract and scripted replies."""

from review.client import ReviewerCall, ScriptedReviewer
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
WRITER = "deepseek/deepseek-v4.1-flash-1017"


def candidate(**kw) -> Candidate:
    base = dict(report="r", file="src/Vault.sol", contract="Vault", function="withdraw",
                start_line=6, end_line=10, note="state is updated after the external call",
                severity="high", layers=frozenset({"plain"}))  # fmt: skip
    return Candidate(**{**base, **kw})


def writer(replies, **kw) -> ScriptedReviewer:
    return ScriptedReviewer(replies, name=WRITER, **kw)


def call(text: str, **kw) -> ReviewerCall:
    return ReviewerCall(text=text, **kw)
