"""Making a run-set: the agent on the cards' contracts, R repeats, then (only if asked) a judge.

This is the paid part. It never decides to spend: `evals.pipeline` does that, through the rules in
`evals.spend`, and hands this module a `SpendCap`. Here every run reserves its ceiling first and
does not start if the cap would be passed. The thing that runs the agent is a function passed in,
so the tests use a scripted one; `live_agent_runner` is the real one (RPC, explorer, the model).

Runs go repeat by repeat (every contract once, then every contract again) so that a run-set stopped
early still has whole repeats. Runs are one at a time unless `workers` says otherwise; a run-set
made with workers is marked concurrent in its manifest and in every trace's tags, and the timing
statistics leave such runs out.
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.cards import Card
from evals.judge.cache import JudgeCache
from evals.judge.client import JudgeClient, JudgeSpec, load_judges
from evals.judge.inputs import claim_inputs, judgeable, summary_input
from evals.judge.policy import load_policy, self_grading
from evals.judge.rubric import load_rubric
from evals.judge.runner import Job, Price, SpendCap, claim_job, run_jobs, summary_job
from evals.runset import RunSet, read_runs

CONSECUTIVE_FAILURES = 3  # runs that fail in a row (no RPC, no key) end the run-set early


@dataclass(frozen=True)
class PlanItem:
    card: Card
    repeat: int  # from 1


def plan_runs(cards: Sequence[Card], repeats: int) -> list[PlanItem]:
    return [PlanItem(card, r) for r in range(1, repeats + 1) for card in cards]


@dataclass(frozen=True)
class RunResult:
    trace: Path | None
    cost_usd: float
    accepted: bool = False
    error: str | None = None  # why no trace was written, if none was


AgentRunner = Callable[[Card, float, dict[str, Any]], RunResult]
"""(card, per-run cost cap in USD, tags for the trace) -> the result of one agent run."""


@dataclass
class Generated:
    planned: int
    done: int = 0
    failed: int = 0
    skipped: int = 0  # not started because the cap or an early stop
    spent_usd: float = 0.0
    stopped: str | None = None  # "spend_cap" | "failures" | None
    errors: list[str] = field(default_factory=list)


def generate(
    runset: RunSet,
    plan: Sequence[PlanItem],
    runner: AgentRunner,
    cap: SpendCap,
    *,
    ceiling: float,
    workers: int = 1,
    progress: Callable[[Generated], None] | None = None,
) -> Generated:
    result = Generated(planned=len(plan))
    lock = threading.Lock()
    stop = threading.Event()
    streak = 0

    def one(item: PlanItem) -> None:
        nonlocal streak
        if stop.is_set():
            with lock:
                result.skipped += 1
            return
        if not cap.reserve(ceiling):
            stop.set()
            with lock:
                result.stopped = result.stopped or "spend_cap"
                result.skipped += 1
            return
        tags = {
            "runset": runset.name,
            "repeat": item.repeat,
            "contract": item.card.id,
            "concurrent": workers > 1,
        }
        try:
            outcome = runner(item.card, ceiling, tags)
        except Exception as err:  # a failure of one run must not lose the others
            outcome = RunResult(None, 0.0, error=type(err).__name__)
        cap.settle(ceiling, outcome.cost_usd)
        with lock:
            result.spent_usd = cap.spent
            if outcome.trace is None:
                result.failed += 1
                result.errors.append(f"{item.card.id} #{item.repeat}: {outcome.error}")
                streak += 1
                if streak >= CONSECUTIVE_FAILURES:
                    stop.set()
                    result.stopped = result.stopped or "failures"
            else:
                result.done += 1
                streak = 0
            if progress is not None:
                progress(result)

    if workers <= 1:
        for item in plan:
            one(item)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, plan))
    result.spent_usd = cap.spent
    return result


def _store_state(asked: int, answered: int) -> str | None:
    if not asked:
        return None
    return "cold" if not answered else "warm" if answered >= asked else "mixed"


def cache_state(counters: Mapping[str, int]) -> str:
    """Whether a run met warm stores, from what the run itself counted: `warm` if the code store and
    the selector-name store answered everything they were asked, `cold` if they answered nothing,
    `mixed` otherwise, `no_store` if no store was asked (none is configured). The stores live in
    Postgres and outlast a run, so the first run of a contract is not necessarily cold."""
    states = {
        s
        for s in (
            _store_state(counters.get("code_store_lookups", 0), counters.get("code_store_hits", 0)),
            _store_state(
                counters.get("selector_names_asked", 0),
                counters.get("selector_names_from_cache", 0),
            ),
        )
        if s
    }
    if not states:
        return "no_store"
    return states.pop() if len(states) == 1 else "mixed"


def live_agent_runner(model: str, traces: Path, env: Mapping[str, str]) -> AgentRunner:
    """The real runner: each call pins the card's block, runs the agent with the named model and
    writes the trace into `traces`. Needs RPC and the model service (it costs money)."""
    from agent.config import load_config
    from agent.llm import OpenRouterModel
    from agent.loop import run_agent
    from agent.tools import RunContext
    from agent.trace import write_trace
    from chain.errors import ChainError
    from chain.profile import Services
    from chain.rpc import RpcClient

    config = load_config()

    def run(card: Card, ceiling: float, tags: dict[str, Any]) -> RunResult:
        limits = config.limits.model_copy(update={"max_cost_usd": ceiling})
        try:
            client = RpcClient.connect(card.chain, env, card.block)
            llm = OpenRouterModel(
                config.resolve_model(model), timeout=limits.request_timeout_seconds
            )
            ctx = RunContext(client, card.chain, services=Services.from_env(env))
            agent_run = run_agent(llm, ctx, card.address, limits=limits)
        except ChainError as err:
            return RunResult(None, 0.0, error=err.code)
        state = cache_state(agent_run.metrics.get("counters") or {})
        path = write_trace(agent_run, limits, traces, tags={**tags, "cache_state": state})
        return RunResult(path, agent_run.report.run.cost_usd, agent_run.report.validation.accepted)

    return run


# ---- the judge, after the runs ------------------------------------------------------------------


@dataclass
class Judged:
    slug: str
    called: int = 0
    cached: int = 0
    unparsed: int = 0
    left: int = 0
    stopped: str | None = None


def judge_runset(
    runset: RunSet,
    judges: Sequence[str],
    cache: JudgeCache,
    cap: SpendCap,
    make_client: Callable[[JudgeSpec], JudgeClient],
    *,
    workers: int = 4,
    specs: Mapping[str, JudgeSpec] | None = None,
) -> list[Judged]:
    """Each named judge over every claim and summary of the run-set's accepted reports. Only judges
    listed in judges.toml (which holds their prices) can be used; there is no default."""
    refused = self_grading(judges, runset.manifest["model"]["slug"], load_policy())
    if refused:  # also checked before any money is spent; this is the second lock
        raise ValueError("; ".join(refused))
    specs = specs if specs is not None else load_judges()
    rubric = load_rubric()
    runs, _ = read_runs(runset)
    out = []
    for slug in judges:
        if slug not in specs:
            raise ValueError(f"judge {slug!r} is not in judges.toml")
        spec = specs[slug]
        jobs: list[Job] = []
        for run in runs:
            if not judgeable(run):
                continue
            jobs += [claim_job(c, rubric, slug) for c in claim_inputs(run)]
            summary = summary_input(run)
            if summary is not None:
                jobs.append(summary_job(summary, rubric, slug))
        done = run_jobs(
            jobs,
            make_client(spec),
            cache,
            cap,
            price=Price(spec.input_per_mtok, spec.output_per_mtok),
            max_tokens=spec.max_tokens,
            workers=workers,
        )
        out.append(Judged(slug, done.called, done.cached, done.unparsed, done.left, done.stopped))
        if done.stopped:
            break
    return out
