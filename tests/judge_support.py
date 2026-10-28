"""Builders and a scripted judge for the faithfulness tests: no network, no money.

`FakeJudge` answers each call from a function of the prompt, records every call, and reports a cost,
so the tests can check parsing, caching, the spend cap and the arithmetic without a model.
`oracle` makes a judge that reads the claim (or the numbered summary sentences) out of the prompt it
is sent and answers from a dictionary, which also proves the prompt carries what it should.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from evals.judge.client import JudgeReply, ModelUnavailable
from evals.judge.rubric import Prompt
from evals.runs import RunRecord, parse_trace
from tests.evals_support import ADDR, make_trace

SENTINEL_VERDICT = "high_risk"


def verdict_json(label: str = "supported", reason: str = "ok") -> str:
    return json.dumps({"label": label, "reason": reason})


class FakeJudge:
    """name, a script `prompt -> reply text | JudgeReply | Exception`, and the calls it got."""

    def __init__(
        self,
        script: Callable[[Prompt], Any] | None = None,
        *,
        name: str = "vendor/judge",
        cost: float = 0.001,
    ) -> None:
        self.name = name
        self.script = script or (lambda prompt: verdict_json())
        self.cost = cost
        self.calls: list[Prompt] = []
        self.max_tokens: list[int] = []

    def judge(self, prompt: Prompt, schema: dict[str, Any], *, max_tokens: int) -> JudgeReply:
        self.calls.append(prompt)
        self.max_tokens.append(max_tokens)
        answer = self.script(prompt)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, JudgeReply):
            return answer
        return JudgeReply(
            text=answer,
            prompt_tokens=1000,
            completion_tokens=40,
            cost_usd=self.cost,
            finish_reason="stop",
            model=self.name,
        )


def unavailable(message: str = "OpenRouter error 402: the account has no credits") -> Exception:
    return ModelUnavailable(message)


_CLAIM = re.compile(r"<<<BEGIN claim (\w+)>>>\n(.*?)\n<<<END claim \1>>>", re.DOTALL)
_SENTENCES = re.compile(r"<<<BEGIN sentences (\w+)>>>\n(.*?)\n<<<END sentences \1>>>", re.DOTALL)


def claim_in(prompt: Prompt) -> str | None:
    found = _CLAIM.search(prompt.user)
    return found.group(2) if found else None


def ids_in(prompt: Prompt) -> tuple[str, ...]:
    return tuple(re.findall(r"^Record \d+ of \d+: id (\S+) \(", prompt.user, re.MULTILINE))


def sentences_in(prompt: Prompt) -> list[str]:
    found = _SENTENCES.search(prompt.user)
    if not found:
        return []
    return [re.sub(r"^\d+\. ", "", line) for line in found.group(2).split("\n")]


def oracle(
    claims: dict[Any, str] | None = None,
    sentences: dict[str, str] | None = None,
    default: str = "supported",
) -> Callable[[Prompt], str]:
    """A script that answers by looking up the claim (by its text, or by (text, evidence ids)) or
    each summary sentence in a dict of label by text; anything not listed gets `default`."""
    claims = claims or {}
    sentences = sentences or {}

    def script(prompt: Prompt) -> str:
        claim = claim_in(prompt)
        if claim is not None:
            # a key of (claim, evidence ids) beats a key of the claim alone
            label = claims.get((claim, ids_in(prompt)), claims.get(claim, default))
            return verdict_json(label, "`evidence` says so")
        listed = sentences_in(prompt)
        return json.dumps(
            {
                "sentences": [
                    {"n": n, "label": sentences.get(s, default), "reason": "`item` says so"}
                    for n, s in enumerate(listed, start=1)
                ]
            }
        )

    return script


def make_run(
    claims: list[tuple[str, tuple[str, ...]]] | None = None,
    *,
    address: str = ADDR,
    verdict: str = SENTINEL_VERDICT,
    summary: str = "A token with a mint function. Several checks were not run.",
    texts: dict[str, str] | None = None,
    tools: int = 2,
    **kwargs: Any,
) -> RunRecord:
    """A run record whose report has the given (claim, evidence ids) findings. `texts` replaces
    the tool message of an evidence id with a text of the test's choosing."""
    names = ["identify_contract", "risk_scan", "read_getter", "match_references"][:tools]
    trace = make_trace(
        address=address,
        verdict=verdict,
        summary=summary,
        tools=[(n, {"address": address}) for n in names],
        **kwargs,
    )
    claims = claims if claims is not None else [("The contract has a mint function.", ("T1",))]
    trace["report"]["findings"] = [
        {"claim": c, "kind": "observation", "severity": "info", "evidence_ids": list(ids)}
        for c, ids in claims
    ]
    if texts:
        for message in trace["messages"]:
            if message.get("role") != "tool":
                continue
            shown = json.loads(message["content"])
            if shown.get("evidence_id") in texts:
                message["content"] = texts[shown["evidence_id"]]
    return parse_trace(trace, path=f"run-{address[-6:]}-{len(claims)}.json")
