"""The judge's model client: one request, one reply, no tools.

`JudgeClient` is the interface the runner uses, so the tests use a scripted fake. `OpenRouterJudge`
is the real one. It reuses the agent's OpenRouter client (retries, wall-clock timeout, the cost
figure OpenRouter reports, the key read from the environment and never put in a message) and only
changes the request: temperature 0, the rubric as the system message, no tools, and the reply
asked for as JSON that matches a schema.

NOT VERIFIED against the live API (nothing has been sent): that each candidate model accepts
`response_format` with a JSON schema, and that `provider.require_parameters` is understood. If a
model refuses, the error is a normal `ModelUnavailable` and the judge's entry in `judges.toml` can
say `structured = "none"`; the reply is parsed strictly either way. `reasoning = "off"` sends
`reasoning: {"enabled": false}`, also untested; a model that thinks first bills its hidden tokens as
output, inside `max_tokens`.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent.config import ModelSpec
from agent.llm import ModelUnavailable, OpenRouterModel, _safe
from evals.judge.rubric import JUDGE_DIR, Prompt

JUDGES_PATH = JUDGE_DIR / "judges.toml"

__all__ = [
    "JudgeClient",
    "JudgeReply",
    "JudgeSpec",
    "ModelUnavailable",
    "OpenRouterJudge",
    "load_judges",
]


@dataclass(frozen=True)
class JudgeReply:
    text: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None
    provider: str | None = None
    model: str = ""


class JudgeClient(Protocol):
    name: str

    def judge(self, prompt: Prompt, schema: dict[str, Any], *, max_tokens: int) -> JudgeReply:
        """One call. Raises ModelUnavailable when the model cannot answer (no key, refused, no
        credits, no reply); the message never contains the key."""
        ...


@dataclass(frozen=True)
class JudgeSpec:
    """A candidate judge as listed in judges.toml: the slug and what OpenRouter charged when the
    file was written. The prices bound a run's worst case; the cost that is counted is the one
    OpenRouter reports on each reply."""

    slug: str
    input_per_mtok: float
    output_per_mtok: float
    max_tokens: int = 800
    structured: str = "json_schema"  # "json_schema" | "none"
    reasoning: str = ""  # "off" asks the model not to think first; "" leaves its default
    note: str = ""


def load_judges(path: Path = JUDGES_PATH) -> dict[str, JudgeSpec]:
    if not path.exists():
        return {}
    out = {}
    for row in tomllib.loads(path.read_text()).get("judge", []):
        spec = JudgeSpec(**row)
        out[spec.slug] = spec
    return out


class OpenRouterJudge(OpenRouterModel):
    def __init__(self, spec: JudgeSpec, **kwargs: Any) -> None:
        kwargs.setdefault("timeout", 120.0)
        super().__init__(
            ModelSpec(
                name=spec.slug,
                input_per_mtok=spec.input_per_mtok,
                output_per_mtok=spec.output_per_mtok,
            ),
            temperature=0.0,
            **kwargs,
        )
        self._structured = spec.structured
        self._reasoning = spec.reasoning

    def judge(self, prompt: Prompt, schema: dict[str, Any], *, max_tokens: int) -> JudgeReply:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": prompt.messages(),
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        if self._structured == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "judgement", "strict": True, "schema": schema},
            }
            body["provider"] = {"require_parameters": True}
        if self._reasoning == "off":
            body["reasoning"] = {"enabled": False}
        payload, _ = self._post(body)
        choice = payload["choices"][0]
        message = choice.get("message") or {}
        usage = payload.get("usage") or {}
        content = message.get("content")
        return JudgeReply(
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
