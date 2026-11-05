"""The reviewer's model client: one request, one reply, no tools (R3). A reply that is not the JSON
asked for gets one more request (`call_once`).

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
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from agent.config import ModelSpec
from agent.llm import ModelUnavailable, OpenRouterModel, _safe
from review.pack import Checklist, Prompt
from review.spans import CUT_OFF, RETRYABLE, CallContext, Checked, check_reply
from review.types import REVIEWER_SCHEMA

__all__ = [
    "COMPANY_GROUPS",
    "RETRY_REMINDER",
    "UNAVAILABLE",
    "CallRecord",
    "ModelUnavailable",
    "OpenRouterReviewer",
    "ReviewerCall",
    "ReviewerClient",
    "ScriptedReviewer",
    "call_once",
    "company_of",
    "retry_prompt",
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
# The one sentence added to a request that is asked again after an unreadable reply. Fixed text of
# ours: nothing the model wrote is put into the second request.
RETRY_REMINDER = "Reply with the JSON object only: no text before or after it and no code fence."


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
    # 1 when the first reply was unreadable and the request was sent once more. Tokens and cost
    # then add up both requests; the rest of the record is the last request's.
    retries: int = 0


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


def retry_prompt(prompt: Prompt) -> Prompt:
    """The same request with `RETRY_REMINDER` after it. The instructions (and their hash) are the
    same; only the end of the user message differs."""
    return replace(prompt, user=f"{prompt.user}\n\n{RETRY_REMINDER}")


Checker = Callable[[str | None, CallContext, Checklist], Checked]


def _ask(
    client: ReviewerClient,
    prompt: Prompt,
    ctx: CallContext,
    checklist: Checklist,
    max_tokens: int,
    schema: dict[str, Any],
    check: Checker,
) -> tuple[Checked, ReviewerCall | None]:
    """One request and the check of its reply; the reply is None when the model was unavailable."""
    try:
        reply = client.review(prompt, schema, max_tokens=max_tokens)
    except ModelUnavailable:
        return Checked(unreadable=UNAVAILABLE), None
    if reply.finish_reason == "length":
        return Checked(unreadable=CUT_OFF), reply
    return check(reply.text, ctx, checklist), reply


def call_once(
    client: ReviewerClient,
    prompt: Prompt,
    ctx: CallContext,
    checklist: Checklist,
    *,
    purpose: str,
    max_tokens: int,
    schema: dict[str, Any] = REVIEWER_SCHEMA,
    check: Checker = check_reply,
) -> tuple[Checked, CallRecord]:
    """One request and the check of its reply, and ONE more request if the model answered but not
    in the shape asked for (no text, not JSON, no list of items; `RETRYABLE`). The second request
    is the first plus `RETRY_REMINDER`; its answer replaces the first, so a reply that is still
    unreadable is final. A reply that was cut off, or a model that cannot answer, is not asked
    again. A model that cannot answer is not an error here: the record says so and the caller counts
    the call as not done. The record adds up the tokens and cost of both requests."""
    checked, reply = _ask(client, prompt, ctx, checklist, max_tokens, schema, check)
    replies = [reply] if reply is not None else []
    retries = 0
    if reply is not None and checked.unreadable in RETRYABLE:
        retries = 1
        again, second = _ask(
            client, retry_prompt(prompt), ctx, checklist, max_tokens, schema, check
        )
        if second is not None:  # if the second request got no answer, the first one's reason stands
            checked = again
            replies.append(second)
    last = replies[-1] if replies else None
    return checked, CallRecord(
        purpose=purpose,
        index=ctx.call_index,
        model=client.name,
        prompt_tokens=sum(r.prompt_tokens for r in replies),
        completion_tokens=sum(r.completion_tokens for r in replies),
        cost_usd=sum(r.cost_usd for r in replies),
        finish_reason=last.finish_reason if last else None,
        accepted=len(checked.accepted) + len(checked.leads),
        dropped=dict(checked.dropped),
        adjusted=dict(checked.adjusted),
        unreadable=checked.unreadable,
        retries=retries,
    )
