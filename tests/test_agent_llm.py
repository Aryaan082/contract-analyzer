"""OpenRouterModel against a fake HTTP opener: parsing, cost, retries, and what never leaks."""

import io
import json
import urllib.error

import pytest

from agent.config import ModelSpec
from agent.llm import (
    ModelReply,
    ModelUnavailable,
    OpenRouterModel,
    ToolCall,
    with_cache_breakpoints,
)

KEY = "sk-or-test-SECRET-1234"
SPEC = ModelSpec(name="anthropic/claude-sonnet-5.5", input_per_mtok=2.0, output_per_mtok=10.0)
TOOLS = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}]
MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]


def payload(**overrides):
    body = {
        "model": "anthropic/claude-sonnet-5.5",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "toolu_1",
                            "type": "function",
                            "function": {
                                "name": "identify_contract",
                                "arguments": '{"address": "0x1"}',
                            },
                        },
                        {
                            "id": "toolu_2",
                            "type": "function",
                            "function": {
                                "name": "list_selectors",
                                "arguments": '{"address": "0x1"}',
                            },
                        },
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 500,
            "completion_tokens": 100,
            "cost": 0.0021,
            "prompt_tokens_details": {"cached_tokens": 200},
        },
    }
    body.update(overrides)
    return body


class Opener:
    """Answers each request with the next item: a payload, or an exception to raise."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def http_error(code, message="boom"):
    body = io.BytesIO(json.dumps({"error": {"message": message}}).encode())
    return urllib.error.HTTPError("https://x", code, "err", {}, body)


def model(*answers, retries=2):
    opener = Opener(*answers)
    made = OpenRouterModel(
        SPEC, api_key=KEY, retries=retries, opener=opener, sleep=lambda s: None, timeout=5
    )
    return made, opener


def test_several_tool_calls_in_one_reply_are_parsed_with_their_arguments_and_cost():
    made, opener = model(payload())
    reply = made.complete(MESSAGES, TOOLS, max_tokens=100)
    assert [(c.id, c.name) for c in reply.tool_calls] == [
        ("toolu_1", "identify_contract"),
        ("toolu_2", "list_selectors"),
    ]
    assert json.loads(reply.tool_calls[0].arguments) == {"address": "0x1"}
    assert (reply.usage.prompt_tokens, reply.usage.cached_tokens) == (500, 200)
    assert reply.usage.cost_usd == 0.0021 and reply.finish_reason == "tool_calls"
    request = opener.requests[0]
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    body = json.loads(request.data)
    assert body["model"] == SPEC.name and body["tools"] == TOOLS and body["max_tokens"] == 100


def test_the_key_goes_only_into_the_authorization_header():
    made, opener = model(payload())
    made.complete(MESSAGES, TOOLS, max_tokens=10)
    assert KEY.encode() not in opener.requests[0].data
    assert KEY not in opener.requests[0].full_url


def test_cost_falls_back_to_the_list_price_when_the_reply_has_none():
    body = payload()
    del body["usage"]["cost"]
    reply = model(body)[0].complete(MESSAGES, TOOLS, max_tokens=10)
    expected = (300 * 2.0 + 200 * 2.0 * 0.1 + 100 * 10.0) / 1e6
    assert reply.usage.cost_usd == pytest.approx(expected)


def test_a_plain_text_reply_has_no_tool_calls():
    body = payload()
    body["choices"][0]["message"] = {"content": "I think it is fine."}
    reply = model(body)[0].complete(MESSAGES, TOOLS, max_tokens=10)
    assert reply.content == "I think it is fine." and reply.tool_calls == ()


def test_busy_responses_are_retried_then_succeed():
    made, opener = model(http_error(429), http_error(503), payload())
    assert made.complete(MESSAGES, TOOLS, max_tokens=10).tool_calls
    assert len(opener.requests) == 3


def test_an_empty_choices_reply_is_retried():
    made, _ = model({"choices": [], "error": {"message": "provider failed"}}, payload())
    assert made.complete(MESSAGES, TOOLS, max_tokens=10).tool_calls


@pytest.mark.parametrize(
    ("code", "fragment"), [(401, "check OPENROUTER_API_KEY"), (402, "no credits")]
)
def test_refusals_are_not_retried_and_say_what_to_do_without_the_key(code, fragment):
    made, opener = model(http_error(code, "details"))
    with pytest.raises(ModelUnavailable, match=fragment) as err:
        made.complete(MESSAGES, TOOLS, max_tokens=10)
    assert KEY not in str(err.value) and len(opener.requests) == 1


def test_an_unreachable_service_gives_up_after_the_retries():
    made, opener = model(OSError("no route"), OSError("no route"), OSError("no route"))
    with pytest.raises(ModelUnavailable, match="could not reach OpenRouter"):
        made.complete(MESSAGES, TOOLS, max_tokens=10)
    assert len(opener.requests) == 3


def test_a_missing_key_says_where_to_put_it(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("agent.llm.env_files", lambda: [])
    made = OpenRouterModel(SPEC, opener=Opener(payload()))
    with pytest.raises(ModelUnavailable, match="OPENROUTER_API_KEY is not set"):
        made.complete(MESSAGES, TOOLS, max_tokens=10)


def test_cache_breakpoints_mark_the_system_prompt_and_the_last_message_without_editing_the_input():
    messages = [*MESSAGES, {"role": "tool", "tool_call_id": "a", "content": "result"}]
    marked = with_cache_breakpoints(messages)
    assert marked[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert marked[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps(marked[1])
    assert messages[0]["content"] == "sys" and messages[-1]["content"] == "result"


def test_an_assistant_turn_goes_back_with_its_tool_calls():
    reply = ModelReply(content=None, tool_calls=(ToolCall(id="c1", name="n", arguments="{}"),))
    assert reply.as_message() == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "n", "arguments": "{}"}}
        ],
    }


def test_a_thinking_models_reasoning_is_counted_and_sent_back_with_its_tool_calls():
    body = payload()
    body["usage"]["completion_tokens_details"] = {"reasoning_tokens": 70}
    body["choices"][0]["message"]["reasoning_details"] = [
        {"type": "reasoning.text", "text": "think", "index": 0}
    ]
    reply = model(body)[0].complete(MESSAGES, TOOLS, max_tokens=10)
    assert reply.usage.reasoning_tokens == 70
    assert reply.as_message()["reasoning_details"] == [
        {"type": "reasoning.text", "text": "think", "index": 0}
    ]


def test_a_model_that_does_not_think_sends_no_reasoning_field_back():
    reply = model(payload())[0].complete(MESSAGES, TOOLS, max_tokens=10)
    assert reply.usage.reasoning_tokens == 0 and "reasoning_details" not in reply.as_message()
