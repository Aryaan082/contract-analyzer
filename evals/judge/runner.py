"""Running the judge over a list of jobs, with a spend cap that is a hard stop.

Before every call the runner works out the most that call could cost (the prompt's size at
3 characters a token, which over-counts, at the input price, plus `max_tokens` at the output price)
and does not make the call if the money spent so far plus that would pass `max_spend_usd`. The
cost it counts afterwards is the one OpenRouter reports on the reply (the list price only when a
reply carries none). So a run cannot exceed the cap unless a provider charges more than its listed
price; a run that hits the cap stops cleanly, says how many jobs were left, and everything it did
finish is in the cache.

A cached answer costs nothing and is not asked for again. A reply that could not be parsed is
cached too (so it is not paid for twice); `retry_unparsed` asks again.
"""

import math
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from evals.judge.cache import KEEP_RAW, JudgeCache, claim_key, summary_key
from evals.judge.client import JudgeClient, JudgeReply, ModelUnavailable
from evals.judge.inputs import ClaimInput, SummaryInput
from evals.judge.parse import Parsed, parse_claim, parse_summary, quotes_found
from evals.judge.rubric import CLAIM_SCHEMA, SUMMARY_SCHEMA, Prompt, Rubric

CHARS_PER_TOKEN = 3  # real text is nearer 3.5 to 4, so the estimate is on the high side


@dataclass(frozen=True)
class Job:
    task: str  # "claim" | "summary"
    key: str
    prompt: Prompt
    schema: dict[str, Any]
    model: str
    rubric: str
    repeat: int
    sentences: int  # a summary job: how many the reply must answer
    texts: tuple[str, ...]  # what the reply may quote
    meta: dict[str, Any]


def claim_job(inp: ClaimInput, rubric: Rubric, model: str, repeat: int = 0) -> Job:
    return Job(
        task="claim",
        key=claim_key(
            inp.claim_sha, inp.evidence_sha, inp.kind, inp.severity, model, rubric.sha256, repeat
        ),
        prompt=inp.prompt(rubric),
        schema=CLAIM_SCHEMA,
        model=model,
        rubric=rubric.sha256,
        repeat=repeat,
        sentences=0,
        texts=tuple(inp.untrusted_texts),
        meta={
            "claim_sha": inp.claim_sha,
            "evidence_sha": inp.evidence_sha,
            "evidence_cut": inp.cut,
        },
    )


def summary_job(inp: SummaryInput, rubric: Rubric, model: str, repeat: int = 0) -> Job:
    return Job(
        task="summary",
        key=summary_key(inp.summary_sha, inp.context_sha, model, rubric.sha256, repeat),
        prompt=inp.prompt(rubric),
        schema=SUMMARY_SCHEMA,
        model=model,
        rubric=rubric.sha256,
        repeat=repeat,
        sentences=len(inp.sentences),
        texts=tuple(inp.untrusted_texts),
        meta={"summary_sha": inp.summary_sha, "context_sha": inp.context_sha},
    )


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.input_per_mtok + completion_tokens * self.output_per_mtok
        ) / 1e6


def worst_case(job: Job, price: Price, max_tokens: int) -> float:
    estimated_in = math.ceil(job.prompt.chars / CHARS_PER_TOKEN) + 50
    return price.cost(estimated_in, max_tokens)


class SpendCap:
    """Money reserved or spent in this run; `reserve` says no rather than pass the cap."""

    def __init__(self, max_usd: float) -> None:
        if max_usd <= 0:
            raise ValueError("a spend cap above zero is required")
        self.max_usd = max_usd
        self.spent = 0.0
        self._reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount: float) -> bool:
        with self._lock:
            if self.spent + self._reserved + amount > self.max_usd + 1e-12:
                return False
            self._reserved += amount
            return True

    def settle(self, reserved: float, actual: float) -> None:
        with self._lock:
            self._reserved -= reserved
            self.spent += actual


@dataclass
class RunSummary:
    jobs: int = 0
    cached: int = 0
    called: int = 0
    parsed: int = 0
    unparsed: int = 0
    left: int = 0  # jobs not done because the run stopped
    stopped: str | None = None  # "spend_cap" | "unavailable: <why>"
    spent_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    refused: dict[str, int] = field(default_factory=dict)


def _record(job: Job, reply: JudgeReply, parsed: Parsed, cost: float) -> dict[str, Any]:
    reasons = [v.reason for v in parsed.verdicts]
    return {
        "key": job.key,
        "task": job.task,
        "model": job.model,
        "rubric": job.rubric,
        "repeat": job.repeat,
        "input_sha": job.prompt.sha256,
        **job.meta,
        "parsed": parsed.ok,
        "error": parsed.error,
        "verdicts": [{"label": v.label, "reason": v.reason} for v in parsed.verdicts],
        "quotes_found": [quotes_found(r, list(job.texts)) for r in reasons] if parsed.ok else [],
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "reasoning_tokens": reply.reasoning_tokens,
        "cost_usd": round(cost, 8),
        "finish_reason": reply.finish_reason,
        "provider": reply.provider,
        "raw": (reply.text or "")[:KEEP_RAW],
    }


def run_jobs(
    jobs: list[Job],
    client: JudgeClient,
    cache: JudgeCache,
    cap: SpendCap,
    *,
    price: Price,
    max_tokens: int = 800,
    workers: int = 1,
    retry_unparsed: bool = False,
    progress: Callable[[RunSummary], None] | None = None,
) -> RunSummary:
    summary = RunSummary(jobs=len(jobs))
    lock = threading.Lock()
    stop = threading.Event()
    unique: dict[str, Job] = {}
    for job in jobs:
        unique.setdefault(job.key, job)

    todo: list[Job] = []
    for job in unique.values():
        row = cache.get(job.key)
        if row is not None and (row["parsed"] or not retry_unparsed):
            summary.cached += 1
        else:
            todo.append(job)

    def one(job: Job) -> None:
        if stop.is_set():
            with lock:
                summary.left += 1
            return
        reserved = worst_case(job, price, max_tokens)
        if not cap.reserve(reserved):
            stop.set()
            with lock:
                summary.stopped = summary.stopped or "spend_cap"
                summary.left += 1
            return
        try:
            reply = client.judge(job.prompt, job.schema, max_tokens=max_tokens)
        except ModelUnavailable as err:
            cap.settle(reserved, 0.0)
            stop.set()
            with lock:
                summary.stopped = summary.stopped or f"unavailable: {err}"[:240]
                summary.left += 1
            return
        cost = reply.cost_usd or price.cost(reply.prompt_tokens, reply.completion_tokens)
        cap.settle(reserved, cost)
        parsed = (
            parse_claim(reply.text, reply.finish_reason)
            if job.task == "claim"
            else parse_summary(reply.text, job.sentences, reply.finish_reason)
        )
        cache.put(_record(job, reply, parsed, cost))
        with lock:
            summary.called += 1
            summary.prompt_tokens += reply.prompt_tokens
            summary.completion_tokens += reply.completion_tokens
            if parsed.ok:
                summary.parsed += 1
            else:
                summary.unparsed += 1
                summary.refused[parsed.error or "?"] = (
                    summary.refused.get(parsed.error or "?", 0) + 1
                )
            if progress is not None:
                summary.spent_usd = cap.spent
                progress(summary)

    if workers <= 1:
        for job in todo:
            one(job)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, todo))
    summary.spent_usd = cap.spent
    return summary


def estimate(
    jobs: list[Job],
    cache: JudgeCache,
    price: Price,
    max_tokens: int,
    *,
    typical_out: int = 150,
    retry_unparsed: bool = False,
) -> dict[str, Any]:
    """What a run of these jobs would cost, before anything is sent. `typical` assumes real text
    at 3.7 characters a token and `typical_out` answer tokens per call (a model that thinks first
    will use more, and the cost it reports includes that); `worst` is the bound the spend cap
    uses."""
    unique: dict[str, Job] = {}
    for job in jobs:
        unique.setdefault(job.key, job)
    todo = [
        j
        for j in unique.values()
        if (row := cache.get(j.key)) is None or (retry_unparsed and not row["parsed"])
    ]
    typical_in = sum(math.ceil(j.prompt.chars / 3.7) + 30 for j in todo)
    return {
        "jobs": len(jobs),
        "unique": len(unique),
        "cached": len(unique) - len(todo),
        "to_call": len(todo),
        "input_tokens": typical_in,
        "typical_cost_usd": round(price.cost(typical_in, typical_out * len(todo)), 4),
        "worst_cost_usd": round(sum(worst_case(j, price, max_tokens) for j in todo), 4),
    }
