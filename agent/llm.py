"""The model the agent talks to, behind a small interface.

`ModelClient.complete` takes the conversation so far and the tools on offer and returns one reply:
some text, some tool calls, and what the call cost. The loop never sees HTTP, so the tests use a
scripted fake model, and moving to the Anthropic API directly later is one new class.

`OpenRouterModel` is the real one: Claude through OpenRouter's chat-completions endpoint
(OpenAI-style `tools` in, `tool_calls` out), over plain `urllib` the way
`retrieval/embedders.py` calls the embedding endpoint: the key comes from the environment or a
`.env` file when the first request is made, goes only into the Authorization header and is never
put in a message.

What was measured against OpenRouter on 2026-10-26 (anthropic/claude-sonnet-5.5), and what differs
from the Anthropic API itself:
- Tool calls come back with `arguments` as a JSON string, several in one turn work, and a
  `role: "tool"` message answering each `tool_call_id` round-trips.
- A model cannot be forced to call a tool: `tool_choice` set to "required" or to one named function
  is refused (HTTP 400, "type tool and any are not supported for this model"). Only "auto" and
  "none" work, so the loop has to deal with a reply that is plain text instead of `submit_report`.
- Prompt caching works through `cache_control` breakpoints on message content: a 10k-token prefix
  cost $0.026 to write and $0.0011 to read (1.25x and 0.1x the input price).
- Every reply carries `usage.cost` in dollars, which is what the cost cap counts.
- Extended thinking is not used: it would mean sending the model's reasoning blocks back with each
  turn.
"""

import http.client
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from dotenv import load_dotenv
from pydantic import BaseModel

from agent.config import ModelSpec
from retrieval.embedders import KEY_VARIABLE
from retrieval.paths import env_files

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})
_CACHED_READ_SHARE = 0.1  # of the input price, used only when a reply carries no cost


class ModelUnavailable(Exception):
    """The model cannot be used (no key, service refused, no answer). The message says what to do
    and never contains the key."""


class ToolCall(BaseModel, frozen=True):
    id: str
    name: str
    arguments: str  # the JSON text the model wrote; the loop parses and checks it


class Usage(BaseModel, frozen=True):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0  # of the prompt, read from the cache
    reasoning_tokens: int = 0  # hidden thinking: billed as output and counted in completion_tokens
    cost_usd: float = 0.0


class ModelReply(BaseModel, frozen=True):
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = Usage()
    finish_reason: str | None = None
    model: str = ""
    # What a thinking model hands back with its reply and expects to get back unchanged with its
    # tool results (OpenRouter's `reasoning_details`). None for a model that does not think.
    reasoning_details: tuple[dict[str, Any], ...] | None = None

    def as_message(self) -> dict[str, Any]:
        """The assistant turn as it goes back into the conversation."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.reasoning_details:
            message["reasoning_details"] = list(self.reasoning_details)
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class ModelClient(Protocol):
    name: str

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        *,
        max_tokens: int,
    ) -> ModelReply:
        """One model call. `messages` are OpenAI-style dicts; `tools` are `{"type": "function",
        "function": {name, description, parameters}}`. Raises ModelUnavailable if it cannot
        answer."""
        ...


def _within(seconds: float, call: Callable):
    """Run `call()` and give up after `seconds` of wall-clock time. urllib's own timeout restarts
    whenever a byte arrives, and OpenRouter sends keep-alive bytes while a provider stalls, so a
    request can otherwise hang for ever (the same reason `retrieval/embedders.py` does this)."""
    done: queue.Queue = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            done.put((True, call()))
        except BaseException as err:  # handed to the caller, which decides what it means
            done.put((False, err))

    threading.Thread(target=run, daemon=True).start()
    try:
        ok, value = done.get(timeout=seconds)
    except queue.Empty:
        raise TimeoutError(f"no complete reply within {seconds:g}s") from None
    if ok:
        return value
    raise value


def _safe(text: str, limit: int = 200) -> str:
    """Text from a server, made safe to print: no control characters, bounded."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()[:limit]


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [dict(block) for block in content]
    return [{"type": "text", "text": content or ""}]


def with_cache_breakpoints(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of the conversation with two cache breakpoints: the system prompt (everything before
    it, the tools, is cached with it) and the last message. Each turn the second one moves
    forward, so the part of the conversation already sent is read from the cache at a tenth of the
    price. Anthropic allows four breakpoints; the provider-specific field stays in this class."""
    out = [dict(m) for m in messages]
    marked = {0, len(out) - 1} if out and out[0].get("role") == "system" else {len(out) - 1}
    for index in marked:
        blocks = _blocks(out[index].get("content"))
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        out[index]["content"] = blocks
    return out


class OpenRouterModel:
    def __init__(
        self,
        spec: ModelSpec,
        *,
        api_key: str | None = None,
        url: str = OPENROUTER_CHAT_URL,
        retries: int = 3,
        timeout: float = 90.0,
        temperature: float = 0.0,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = spec.name
        self._spec = spec
        self._api_key, self._url = api_key, url
        self._retries, self._timeout, self._temperature = retries, timeout, temperature
        self._opener, self._sleep = opener, sleep

    def _key(self) -> str:
        if self._api_key:
            return self._api_key
        for path in env_files():
            load_dotenv(path)  # variables already set in the shell win
        key = os.environ.get(KEY_VARIABLE, "").strip()
        if not key:
            raise ModelUnavailable(
                f"{KEY_VARIABLE} is not set. Add it to .env (see .env.example) or export it."
            )
        return key

    def _describe(self, status: int, detail: str) -> str:
        reasons = {
            400: "the request was refused as invalid",
            401: f"OpenRouter rejected the key: check {KEY_VARIABLE}",
            402: "the OpenRouter account has no credits",
            404: f"model {self.name!r} does not exist or does not support tools",
        }
        return (
            f"OpenRouter error {status}: {reasons.get(status, 'request failed')}. {detail}".strip()
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": "application/json",
            },
        )
        for attempt in range(self._retries + 1):
            try:

                def fetch(request=request):
                    with self._opener(request, timeout=self._timeout) as response:
                        return json.load(response)

                payload = _within(self._timeout, fetch)
            except urllib.error.HTTPError as err:
                try:
                    detail = _safe(json.loads(err.read()).get("error", {}).get("message", ""))
                except (ValueError, AttributeError, OSError):
                    detail = ""
                if err.code in _RETRY_STATUSES and attempt < self._retries:
                    self._sleep(2.0**attempt)
                    continue
                raise ModelUnavailable(self._describe(err.code, detail)) from None
            except (OSError, http.client.HTTPException) as err:
                # a dropped connection, a timeout, no route: all retried
                if attempt < self._retries:
                    self._sleep(2.0**attempt)
                    continue
                raise ModelUnavailable(
                    f"could not reach OpenRouter ({type(err).__name__})"
                ) from None
            if isinstance(payload, dict) and payload.get("choices"):
                return payload
            # OpenRouter can answer 200 with an error body or no choices when a provider fails.
            message = (
                payload.get("error", {}).get("message", "") if isinstance(payload, dict) else ""
            )
            if attempt < self._retries:
                self._sleep(2.0**attempt)
                continue
            raise ModelUnavailable(f"OpenRouter returned no reply. {_safe(str(message))}".strip())
        raise ModelUnavailable("OpenRouter returned no reply.")  # pragma: no cover

    def _cost(self, usage: dict[str, Any]) -> float:
        """OpenRouter's own figure; the list price only if it is missing."""
        if usage.get("cost") is not None:
            return float(usage["cost"])
        prompt = int(usage.get("prompt_tokens") or 0)
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        fresh = (prompt - cached) * self._spec.input_per_mtok
        read = cached * self._spec.input_per_mtok * _CACHED_READ_SHARE
        return (fresh + read + completion * self._spec.output_per_mtok) / 1e6

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        *,
        max_tokens: int,
    ) -> ModelReply:
        body = {
            "model": self.name,
            "messages": with_cache_breakpoints(messages),
            "tools": list(tools),
            "max_tokens": max_tokens,
            "temperature": self._temperature,
        }
        payload = self._post(body)
        choice = payload["choices"][0]
        message = choice.get("message") or {}
        calls = tuple(
            ToolCall(
                id=str(call["id"]),
                name=str(call["function"]["name"]),
                arguments=call["function"].get("arguments") or "{}",
            )
            for call in message.get("tool_calls") or []
        )
        usage = payload.get("usage") or {}
        details = message.get("reasoning_details")
        return ModelReply(
            content=message.get("content") or None,
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                cached_tokens=int(
                    (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                ),
                reasoning_tokens=int(
                    (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
                ),
                cost_usd=self._cost(usage),
            ),
            finish_reason=choice.get("finish_reason"),
            model=str(payload.get("model") or self.name),
            reasoning_details=tuple(d for d in details if isinstance(d, dict))
            if isinstance(details, list) and details
            else None,
        )
