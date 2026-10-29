"""The reviewer's model client: one request, one reply, no tools (R3).

`ReviewerClient` is the interface the review loop uses, so the tests use `ScriptedReviewer`, which
needs no model and no network. `OpenRouterReviewer` is the real one. It reuses the agent's
OpenRouter client (retries, wall-clock timeout, the cost OpenRouter reports, the key read from the
environment and never put in a message) and changes only the request: temperature 0, the
instructions as the system message, no tools, and the reply asked for as JSON that matches
`review.types.REVIEWER_SCHEMA`.

NOT VERIFIED against the live API (nothing has been sent): that each candidate model accepts
`response_format` with a JSON schema (the schema has optional properties, so it is sent with
`strict: false`), and that `provider.require_parameters` is understood. If a model refuses, the
error is a normal `ModelUnavailable`, and `structured="json_object"` or `"none"` asks for plain
JSON instead; the reply is checked strictly either way (`review.spans.check_reply`).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.config import ModelSpec
from agent.llm import ModelUnavailable, OpenRouterModel, _safe
from review.pack import Checklist, Prompt
from review.spans import CallContext, Checked, check_reply
from review.types import REVIEWER_SCHEMA

__all__ = [
    "COMPANY_GROUPS",
    "UNAVAILABLE",
    "CallRecord",
    "ModelUnavailable",
    "OpenRouterReviewer",
    "ReviewerCall",
    "ReviewerClient",
    "ScriptedReviewer",
    "call_once",
    "company_of",
]

# Which model slugs are one company. The same groups as evals/judge/policy.toml, kept here so the
# product does not import the evals; a test checks the two agree.
COMPANY_GROUPS: tuple[tuple[str, ...], ...] = (
    ("qwen", "alibaba"),
    ("z-ai", "zhipu", "zai"),
    ("moonshotai", "moonshot"),
    ("bytedance-seed", "bytedance"),
    ("deepseek",),
    ("anthropic",),
    ("xiaomi",),
    ("minimax",),
    ("tencent",),
    ("openai",),
    ("google",),
)


UNAVAILABLE = "the reviewer model was unavailable"


def company_of(slug: str) -> str:
    """The company a model slug belongs to: its prefix, folded into its group's first name."""
    prefix = slug.split("/", 1)[0].strip().lower()
    for group in COMPANY_GROUPS:
        if prefix in group:
            return group[0]
    return prefix


@dataclass(frozen=True)
class ReviewerCall:
    """What one call returned. `text` is None when the model gave no text."""

    text: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None
    provider: str | None = None
    model: str = ""


@dataclass(frozen=True)
class CallRecord:
    """What one call did, for the trace and for the cost line (counts and words, no source text)."""

    purpose: str  # "review" or "confirm"
    index: int
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None
    accepted: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    adjusted: dict[str, int] = field(default_factory=dict)
    unreadable: str | None = None  # why the reply could not be used, in fixed words


class ReviewerClient(Protocol):
    name: str

    def review(self, prompt: Prompt, schema: dict[str, Any], *, max_tokens: int) -> ReviewerCall:
        """One call. Raises ModelUnavailable when the model cannot answer (no key, refused, no
        credits, no reply); the message never contains the key."""
        ...


class OpenRouterReviewer(OpenRouterModel):
    def __init__(
        self,
        spec: ModelSpec,
        *,
        structured: str = "json_schema",
        reasoning: str = "",
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("timeout", 180.0)
        super().__init__(spec, temperature=0.0, **kwargs)
        self._structured = structured
        self._reasoning = reasoning

    def review(self, prompt: Prompt, schema: dict[str, Any], *, max_tokens: int) -> ReviewerCall:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": prompt.messages(),
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        if self._structured == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "review", "strict": False, "schema": schema},
            }
            body["provider"] = {"require_parameters": True}
        elif self._structured == "json_object":
            body["response_format"] = {"type": "json_object"}
            body["provider"] = {"require_parameters": True}
        if self._reasoning == "off":
            body["reasoning"] = {"enabled": False}
        payload, _ = self._post(body)
        choice = payload["choices"][0]
        message = choice.get("message") or {}
        usage = payload.get("usage") or {}
        content = message.get("content")
        return ReviewerCall(
            text=content if isinstance(content, str) else None,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            reasoning_tokens=int(
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            ),
            cost_usd=self._cost(usage),
            finish_reason=choice.get("finish_reason"),
            provider=_safe(str(payload["provider"]), 60)
            if isinstance(payload.get("provider"), str)
            else None,
            model=str(payload.get("model") or self.name),
        )


Scripted = str | ReviewerCall | Exception | Callable[[Prompt], "str | ReviewerCall"]


@dataclass
class ScriptedReviewer:
    """A reviewer that answers from a script and calls no model. Each call takes the next reply (a
    JSON string, a `ReviewerCall`, an exception to raise, or a function of the prompt); the last
    reply repeats when the script runs out. It keeps every prompt it was sent."""

    replies: Sequence[Scripted]
    name: str = "scripted/reviewer"
    cost_usd: float = 0.0
    prompts: list[Prompt] = field(default_factory=list)

    def review(self, prompt: Prompt, schema: dict[str, Any], *, max_tokens: int) -> ReviewerCall:
        self.prompts.append(prompt)
        index = min(len(self.prompts), len(self.replies)) - 1
        reply = self.replies[index]
        if callable(reply) and not isinstance(reply, str | ReviewerCall | Exception):
            reply = reply(prompt)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            return ReviewerCall(text=reply, cost_usd=self.cost_usd, model=self.name)
        return reply


def call_once(
    client: ReviewerClient,
    prompt: Prompt,
    ctx: CallContext,
    checklist: Checklist,
    *,
    purpose: str,
    max_tokens: int,
) -> tuple[Checked, CallRecord]:
    """One request and the check of its reply. A model that cannot answer is not an error here: the
    record says so and the caller counts the call as not done."""
    try:
        reply = client.review(prompt, REVIEWER_SCHEMA, max_tokens=max_tokens)
    except ModelUnavailable:
        checked = Checked(unreadable=UNAVAILABLE)
        return checked, CallRecord(
            purpose=purpose,
            index=ctx.call_index,
            model=client.name,
            unreadable=checked.unreadable,
        )
    if reply.finish_reason == "length":
        checked = Checked(unreadable="the reviewer's reply was cut off")
    else:
        checked = check_reply(reply.text, ctx, checklist)
    return checked, CallRecord(
        purpose=purpose,
        index=ctx.call_index,
        model=client.name,
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
        cost_usd=reply.cost_usd,
        finish_reason=reply.finish_reason,
        accepted=len(checked.accepted),
        dropped=dict(checked.dropped),
        adjusted=dict(checked.adjusted),
        unreadable=checked.unreadable,
    )
