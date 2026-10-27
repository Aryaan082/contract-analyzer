"""The agent against the real model and the real chains. Skipped unless you ask for `-m live`.

    uv run pytest -m live tests/test_agent_live.py

Needs OPENROUTER_API_KEY and RPC_URL_ETHEREUM in `.env` or the environment, and the retrieval index
(`make index`). These calls cost money: a few cents for the probe, about five cents per report.
"""

import json
import os

import pytest

from agent.config import load_config
from agent.contracts import load_candidates, load_contracts
from agent.llm import OpenRouterModel
from agent.loop import run_agent
from agent.tools import RunContext
from chain.config import load_env
from chain.rpc import RpcClient

pytestmark = pytest.mark.live

CONTRACTS = {**load_contracts(), **load_candidates()}  # the fixed five, then the extra honeypots

PROBE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {argument: {"type": "integer" if name == "add" else "string"}},
                "required": [argument],
            },
        },
    }
    for name, argument, description in (
        ("add", "a", "Add 25 to the integer a."),
        ("text_length", "text", "The length of a text."),
    )
]


@pytest.fixture(scope="module")
def env():
    load_env()
    if not os.environ.get("OPENROUTER_API_KEY") or not os.environ.get("RPC_URL_ETHEREUM"):
        pytest.skip("needs OPENROUTER_API_KEY and RPC_URL_ETHEREUM")
    return os.environ


def test_tool_calls_parallel_calls_and_a_tool_result_round_trip_work_through_openrouter(env):
    model = OpenRouterModel(load_config().resolve_model("haiku"))
    messages = [
        {"role": "user", "content": "Call add with a=17 and text_length with text='contract'."}
    ]
    first = model.complete(messages, PROBE_TOOLS, max_tokens=300)
    assert {c.name for c in first.tool_calls} == {"add", "text_length"}
    arguments = {c.name: json.loads(c.arguments) for c in first.tool_calls}
    assert arguments["add"] == {"a": 17} and arguments["text_length"] == {"text": "contract"}
    messages.append(first.as_message())
    for tool_call in first.tool_calls:
        answer = 42 if tool_call.name == "add" else 8
        messages.append(
            {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(answer)}
        )
    second = model.complete(messages, PROBE_TOOLS, max_tokens=300)
    assert "42" in (second.content or "") and not second.tool_calls
    assert first.usage.cost_usd > 0 and second.usage.cost_usd > 0


@pytest.mark.parametrize("contract_id", list(CONTRACTS))
def test_a_valid_cited_report_for_each_fixed_contract(env, contract_id):
    contract = CONTRACTS[contract_id]
    config = load_config()
    client = RpcClient.connect(contract.chain, env, contract.block)
    run = run_agent(
        OpenRouterModel(config.resolve_model(), timeout=config.limits.request_timeout_seconds),
        RunContext(client, contract.chain),
        contract.address,
        limits=config.limits,
    )
    report = run.report
    assert report.validation.accepted, report.validation.problems
    assert report.verdict != "insufficient_evidence"
    assert report.run.cost_usd <= config.limits.max_cost_usd
