"""Time and cost, reported the way an honest benchmark has to be.

- Never one number: median, 95th percentile, maximum, and ALWAYS n. A 95th percentile of 20 runs is
  the second-largest value, so it is only shown from `MIN_N_P95` runs on; below that the maximum is
  shown and the note says why.
- Every figure is for one model: a cheap model and a strong one are different latencies.
- A run is left out of the time statistics when it cannot be trusted: no timing recorded (an older
  trace), run alongside others (`tags.concurrent`, they fight for the CPU and rate limits), or the
  machine slept (the time-of-day clock moved more than the monotonic one; the gap is `skew_s`). The
  number left out, and why, is printed.
- Cost per report sits beside time: it depends on prompt caching, so the cached share is reported.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from evals.metrics import percentile
from evals.repeats import cluster_bootstrap
from evals.runs import RunRecord

MIN_N_P95 = 40
SLEPT_SECONDS = 1.0  # a gap this large between the two clocks, and larger than the share below
SLEPT_SHARE = 0.05  # of the span, marks it as one the machine slept through


def describe(values: Sequence[float], *, min_n_p95: int = MIN_N_P95) -> dict[str, Any]:
    n = len(values)
    if not n:
        return {"n": 0}
    out: dict[str, Any] = {
        "n": n,
        "min": round(min(values), 3),
        "p50": round(percentile(values, 0.5), 3),
        "max": round(max(values), 3),
        "mean": round(sum(values) / n, 3),
    }
    if n >= min_n_p95:
        out["p95"] = round(percentile(values, 0.95), 3)
        interval = cluster_bootstrap(
            [[v] for v in values], lambda xs: percentile(xs, 0.95), resamples=2000
        )
        out["p95_interval"] = [round(interval.low, 3), round(interval.high, 3)]
    else:
        out["p95"] = None
        out["note"] = f"n={n} is too small for a 95th percentile; the maximum is shown"
    return out


def slept(wall: float | None, mono: float | None) -> bool:
    """Whether the time-of-day clock got ahead of the monotonic one by more than noise."""
    if wall is None or mono is None:
        return False
    gap = wall - mono
    return gap > SLEPT_SECONDS and gap > SLEPT_SHARE * max(mono, 1e-9)


def exclusion(run: RunRecord) -> str | None:
    """Why this run's times cannot be used, or None if they can."""
    if not run.has_timing:
        return "no_timing"
    if run.tags.get("concurrent"):
        return "concurrent"
    if slept(run.metrics.get("wall_s"), run.metrics.get("total_s")):
        return "slept"
    if any(slept(t.get("wall_seconds"), t.get("seconds")) for t in run.turn_records):
        return "slept"
    if any(slept(c.wall_seconds, c.seconds) for c in run.ran_calls):
        return "slept"
    return None


def _column(runs: Sequence[RunRecord], key: str) -> list[float]:
    return [float(r.metrics[key]) for r in runs if key in r.metrics]


def time_summary(runs: Sequence[RunRecord]) -> dict[str, Any]:
    """Per model (and per `cache_state` tag where the runs carry one): end to end, the split
    between the model and each tool, and what was left out."""
    models: dict[str, list[RunRecord]] = defaultdict(list)
    for run in runs:
        models[run.model].append(run)
    out: dict[str, Any] = {}
    for model, group in sorted(models.items()):
        left_out: dict[str, int] = defaultdict(int)
        usable = []
        for run in group:
            reason = exclusion(run)
            if reason:
                left_out[reason] += 1
            else:
                usable.append(run)
        per_tool: dict[str, list[float]] = defaultdict(list)
        for run in usable:
            for call in run.ran_calls:
                if call.seconds is not None:
                    per_tool[call.tool].append(call.seconds)
        states: dict[str, list[float]] = defaultdict(list)
        for run in usable:
            if "cache_state" in run.tags:
                states[str(run.tags["cache_state"])].append(run.total_seconds)
        out[model] = {
            "runs": len(group),
            "used": len(usable),
            "left_out": dict(sorted(left_out.items())),
            "end_to_end_s": describe([r.total_seconds for r in usable]),
            "model_s": describe(_column(usable, "model_s")),
            "tools_s": describe(_column(usable, "tools_s")),
            "overhead_s": describe(_column(usable, "overhead_s")),
            "setup_s": describe(_column(usable, "setup_s")),
            "per_tool_s": {t: describe(v) for t, v in sorted(per_tool.items())},
            "by_cache_state": {s: describe(v) for s, v in sorted(states.items())},
        }
    return out


def cost_summary(runs: Sequence[RunRecord]) -> dict[str, Any]:
    models: dict[str, list[RunRecord]] = defaultdict(list)
    for run in runs:
        models[run.model].append(run)
    out: dict[str, Any] = {}
    for model, group in sorted(models.items()):
        prompt = sum(r.prompt_tokens for r in group)
        out[model] = {
            "runs": len(group),
            "total_usd": round(sum(r.cost_usd for r in group), 4),
            "per_report_usd": describe([r.cost_usd for r in group]),
            "tokens_in_mean": round(prompt / len(group)),
            "tokens_out_mean": round(sum(r.completion_tokens for r in group) / len(group)),
            "cached_share": round(sum(r.cached_tokens for r in group) / prompt, 3)
            if prompt
            else None,
            "tool_calls_mean": round(sum(len(r.ran_calls) for r in group) / len(group), 2),
        }
    return out


def tool_call_counts(runs: Sequence[RunRecord], key: Callable[[RunRecord], str]) -> dict[str, int]:
    """How many runs hit the tool-call cap, by the grouping `key` (for example the model)."""
    out: dict[str, int] = defaultdict(int)
    for run in runs:
        out[key(run)] += len(run.ran_calls) >= run.max_tool_calls
    return dict(sorted(out.items()))
