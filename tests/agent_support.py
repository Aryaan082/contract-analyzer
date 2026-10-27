"""Test doubles for the agent: a scripted model, a scripted chain, and small builders.

ScriptedModel stands in for the real model: each step is the next thing it "says" (tool calls,
plain text, or an exception to raise), so the loop, the caps, the validator, the retries and the
injection handling are tested offline for free. It records what it was shown on every call.
"""

import copy
import json
from collections.abc import Callable
from typing import Any

from web3 import Web3

from agent.llm import ModelReply, ToolCall, Usage
from chain.explorer import ExplorerClient
from chain.profile import Services
from chain.rpc import BlockInfo
from chain.signatures import Candidate, SignatureServiceError
from tests.references_support import load_fixture
from tests.world import World, codec

TOKEN = Web3.to_checksum_address("0x" + "22" * 20)
HOLDER = Web3.to_checksum_address("0x" + "33" * 20)

Step = list[ToolCall] | str | Exception | Callable[[list[dict[str, Any]]], Any]


class ScriptedModel:
    name = "scripted"

    def __init__(self, steps: list[Step], *, cost: float = 0.01) -> None:
        self.steps = list(steps)
        self.cost = cost
        self.seen: list[dict[str, Any]] = []  # per call: the messages and the tool names offered

    def complete(self, messages, tools, *, max_tokens):
        self.seen.append(
            {
                "messages": copy.deepcopy(list(messages)),
                "tools": [t["function"]["name"] for t in tools],
            }
        )
        assert self.steps, "the script ran out: the loop made more model calls than planned"
        step = self.steps.pop(0)
        if callable(step):
            step = step(self.seen[-1]["messages"])
        if isinstance(step, Exception):
            raise step
        usage = Usage(prompt_tokens=1000, completion_tokens=100, cost_usd=self.cost)
        if isinstance(step, str):
            return ModelReply(content=step, usage=usage, finish_reason="stop", model=self.name)
        return ModelReply(
            content=None, tool_calls=tuple(step), usage=usage, finish_reason="tool_calls"
        )


_counter = iter(range(1, 10_000))


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call_{next(_counter)}", name=name, arguments=json.dumps(arguments))


def submit(**report: Any) -> ToolCall:
    return call("submit_report", **report)


def report_body(**overrides: Any) -> dict[str, Any]:
    """A report that passes the validator once `identify_contract` (T1) has run."""
    body: dict[str, Any] = {
        "verdict": "no_red_flags_found",
        "summary": "Nothing found in what was checked; several checks were not run.",
        "findings": [
            {
                "claim": "The contract has the six ERC-20 functions.",
                "kind": "observation",
                "severity": "info",
                "evidence_ids": ["T1"],
            }
        ],
        "checked": [{"what": "Contract kind", "evidence_ids": ["T1"]}],
        "not_checked": [{"what": "Who controls the contract", "why": "No owner read was made."}],
    }
    body.update(overrides)
    return body


class AgentWorld(World):
    """World plus the two things the agent's tools ask a real client for."""

    fork_url = "http://127.0.0.1:1"

    def block_info(self) -> BlockInfo:
        return BlockInfo(number=self.block, timestamp=1, base_fee=0)


def erc20_world(name: str | None = "Test Token") -> AgentWorld:
    """A chain with one contract at TOKEN: the committed mint-and-blacklist ERC-20 fixture."""
    world = AgentWorld()
    world.codes[TOKEN.lower()] = load_fixture("FixtureERC20MintBlacklist").code
    if name is not None:
        world.on(TOKEN, "name()", codec.encode(["string"], [name]))
    world.on(TOKEN, "owner()", codec.encode(["address"], [HOLDER]))
    return world


# --- the services of Component 1, with nothing behind them ------------------------------------


class FakeExplorer(ExplorerClient):
    """Etherscan stand-in: answers by action from a dict (a value that is an exception is raised),
    and records what was asked."""

    def __init__(self, **answers):
        super().__init__("FAKE-KEY-NEVER-USED")
        self.answers = answers
        self.asked: list[tuple[str, dict]] = []

    def get(self, chain_id, module, action, **params):
        self.asked.append((action, params))
        answer = self.answers[action]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def source_row(**overrides) -> dict:
    row = {
        "SourceCode": "contract Test {}",
        "ABI": "[]",
        "ContractName": "TestToken",
        "CompilerVersion": "v0.8.24+commit.e11b9ed9",
        "CompilerType": "solc",
        "OptimizationUsed": "1",
        "Runs": "200",
        "ConstructorArguments": "",
        "EVMVersion": "default",
        "LicenseType": "MIT",
        "Proxy": "0",
        "Implementation": "",
    }
    return {**row, **overrides}


UNVERIFIED_ROW = source_row(SourceCode="", ABI="Contract source code not verified", ContractName="")


def creation_row(**overrides) -> dict:
    row = {
        "contractAddress": TOKEN,
        "contractCreator": HOLDER,
        "txHash": "0x" + "ab" * 32,
        "blockNumber": "42",
        "timestamp": "1700000000",
        "contractFactory": "",
        "creationBytecode": "0x6080",
    }
    return {**row, **overrides}


def scripted_names(known: dict[str, list[Candidate]] | None = None, *, down: bool = False):
    """A selector database that knows `known` (selector to candidates) and nothing else, or is
    down. Records every batch it was asked."""
    asked: list[list[str]] = []

    def fetch(selectors):
        asked.append(list(selectors))
        if down:
            raise SignatureServiceError("the database is down")
        return {s: tuple((known or {}).get(s, ())) for s in selectors}

    fetch.asked = asked  # type: ignore[attr-defined]
    return fetch


def candidate(name: str, *, filtered: bool = False, verified: bool = True) -> Candidate:
    return Candidate(name=name, filtered=filtered, has_verified_contract=verified)


def offline_services(*, explorer: ExplorerClient | None = None, names=None) -> Services:
    """Services for a test: no Postgres, the explorer you pass (or none), and a selector database
    that knows nothing unless `names` (from scripted_names) says so. Nothing reaches the network."""
    return Services(explorer=explorer, fetch_names=names or scripted_names())
