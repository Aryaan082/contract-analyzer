"""What a paid command will cost, and the rules that stop it spending by accident.

Every paid command (`python -m evals.run generate`, `heldout`) works out an estimate first, prints
it, and does nothing else unless ALL of these hold:
- `--go` was given (without it the command only prints the estimate and what to add);
- `--max-spend` covers the estimate (the default is $4 for a dev run and $2 for held-out);
- a model that costs more than EXPENSIVE_ABOVE per run (Sonnet) also has `--allow-expensive`;
- a judge runs only if asked for: `--judge-mode quick|milestone` or `--judge SLUG` (no default; the
  policy is evals/judge/policy.toml, and a judge never grades an agent from its own company).
While it runs, the money is counted with the judge's `SpendCap`: before each agent run the most
that run may cost (its ceiling, which is also the per-run cap handed to the agent loop) is
reserved, and the run does not start if that would pass `--max-spend`. What is counted afterwards
is the cost the model service reported. The ledger (`evals/runsets/ledger.jsonl`, git-ignored)
records what each command estimated and actually spent, so the component's roughly $25
envelope can be summed.

The figures below are MEASURED, not guessed; each says where from.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEV_CAP_USD = (
    4.0  # Aryaan's rule: a full dev run under $4 (component6-approach.md, "Spend caps (v2)")
)
HELDOUT_CAP_USD = 2.0
ENVELOPE_USD = 25.0  # the component's rough envelope, same section
EXPENSIVE_ABOVE = 0.02  # USD per run: above this a run needs --allow-expensive (Sonnet is 0.065)


@dataclass(frozen=True)
class AgentCost:
    typical: float  # USD per run: the mean of the measured runs
    ceiling: float  # the most one run may spend: handed to the agent loop as its per-run cap
    seconds: float  # a rough seconds per run when run one at a time (median of the measured runs)
    claims: float  # claims in a delivered report (the judge makes one call per claim and one more)
    source: str


# Measured on the dev contracts' saved traces (2026-10-27): the eval-model smoke test and the Sonnet
# runs of Component 5. The ceilings are set about three times the dearest run seen.
AGENT_COSTS: dict[str, AgentCost] = {
    "deepseek/deepseek-v4.1-flash-1017": AgentCost(
        0.0056, 0.05, 42.0, 12.1,
        "24 runs: mean $0.0056, dearest $0.0143, median 41.8 s, 12.1 claims (smoke test)",
    ),
    "anthropic/claude-sonnet-5.5": AgentCost(
        0.065, 0.25, 26.0, 6.1,
        "5 runs with all chain tools: mean $0.065, range $0.037-0.079; 13 runs: 6.1 claims, 24.5 s",
    ),
    "anthropic/claude-haiku-5.5": AgentCost(
        0.0049, 0.05, 27.0, 9.8,
        "8 runs: mean $0.0049, dearest $0.0076, 26.7 s, 9.8 claims (eval-model smoke test)",
    ),
}  # fmt: skip

# What one pass of a judge over the check set cost, and how many calls that was: the money actually
# spent (retries of cut-off replies included). The cost of one call is the pass cost over its calls,
# and a run-set is priced as (claims in a report + 1) calls a report, so the figure scales with the
# claims. glm and qwen: the stage 1 trial of 2026-10-28 (evals/judge/trials/2026-10-28-stage1.json).
# deepseek: the separate judge-deepseek session reported $0.0575 for one pass (102 calls); that
# figure comes from its report, not from a file in this branch.
JUDGE_PASS: dict[str, tuple[float, int]] = {
    "z-ai/glm-5.3-flash": (0.098, 103),
    "qwen/qwen3.7-flash": (0.046, 103),
    "deepseek/deepseek-v4.1-flash-1017": (0.0575, 102),
}
JUDGE_CALL_USD: dict[str, float] = {slug: usd / calls for slug, (usd, calls) in JUDGE_PASS.items()}


class UnknownCost(ValueError):
    """The cost of a model is not known, so no estimate can be made."""


@dataclass(frozen=True)
class Estimate:
    model: str
    runs: int
    agent_usd: float
    judge_usd: float
    judge_calls: int
    ceiling: float
    seconds: float
    judges: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def total(self) -> float:
        return self.agent_usd + self.judge_usd

    @property
    def expensive(self) -> bool:
        return self.agent_usd / max(self.runs, 1) > EXPENSIVE_ABOVE


def estimate_generation(
    model_slug: str,
    runs: int,
    judges: tuple[str, ...] = (),
    *,
    per_run_usd: float | None = None,
    judge_call_usd: float | None = None,
) -> Estimate:
    """The cost of `runs` agent runs of `model_slug`, then the named judges over their reports.
    An unknown model needs `per_run_usd`, an unknown judge `judge_call_usd`: there is no default."""
    known = AGENT_COSTS.get(model_slug)
    notes = []
    if per_run_usd is not None:
        known = AgentCost(
            per_run_usd, per_run_usd * 4, 60.0, 10.0, "given on the command line (--per-run-usd)"
        )
        notes.append(f"per-run cost ${per_run_usd} was given, not measured")
    if known is None:
        raise UnknownCost(
            f"no measured cost for model {model_slug!r}; known: {', '.join(AGENT_COSTS)}. "
            "Give --per-run-usd to estimate it yourself."
        )
    judge_usd = 0.0
    calls = math.ceil(runs * (known.claims + 1)) if judges else 0
    for slug in judges:
        each = judge_call_usd if judge_call_usd is not None else JUDGE_CALL_USD.get(slug)
        if each is None:
            raise UnknownCost(
                f"no measured cost per call for judge {slug!r}; known: "
                f"{', '.join(JUDGE_CALL_USD)}. Give --judge-call-usd."
            )
        judge_usd += calls * each
    return Estimate(
        model=model_slug,
        runs=runs,
        agent_usd=runs * known.typical,
        judge_usd=judge_usd,
        judge_calls=calls * len(judges),
        ceiling=known.ceiling,
        seconds=runs * known.seconds,
        judges=judges,
        notes=tuple(notes),
    )


def describe(est: Estimate, cap: float) -> list[str]:
    lines = [
        f"estimate: {est.runs} runs of {est.model} = ${est.agent_usd:.3f}"
        + (
            f", plus {est.judge_calls} judge calls ({', '.join(est.judges)}) = ${est.judge_usd:.3f}"
            if est.judges
            else ", no judge (none was named)"
        )
        + f", total ${est.total:.3f}; about {est.seconds / 60:.0f} minutes one run at a time",
        f"spend cap: ${cap:.2f}; the most one run may spend: ${est.ceiling:.2f}",
        *judge_pricing_line(est),
        *est.notes,
    ]
    return lines


def judge_pricing_line(est: Estimate) -> list[str]:
    """How the judge part of an estimate was priced (empty when no judge was named)."""
    if not est.judges:
        return []
    parts = []
    for slug in est.judges:
        if slug in JUDGE_PASS:
            usd, calls = JUDGE_PASS[slug]
            parts.append(f"{slug} ${usd} / {calls} calls = ${usd / calls:.5f} a call")
        else:
            parts.append(f"{slug} (price given on the command line)")
    return [
        "judge pricing: (claims in a report + 1) calls per report per judge, each at the cost per "
        "call measured on one pass of the check set: " + "; ".join(parts)
    ]


@dataclass(frozen=True)
class Decision:
    proceed: bool
    lines: tuple[str, ...]


def decide(
    est: Estimate,
    *,
    go: bool,
    max_spend: float,
    allow_expensive: bool,
    declared: bool = True,
    kind: str = "dev",
) -> Decision:
    """Whether to spend, and, if not, what to add. Never spends on its own say-so."""
    problems = []
    if kind == "heldout" and not declared:
        problems.append("the held-out set needs --declare '<why this look is being taken>'")
    if est.total > max_spend + 1e-12:
        problems.append(
            f"--max-spend ${max_spend:.2f} does not cover the estimate ${est.total:.2f}: "
            f"add --max-spend {math.ceil(est.total * 100) / 100:.2f} or more"
        )
    if est.expensive and not allow_expensive:
        problems.append(
            f"{est.model} costs more than ${EXPENSIVE_ABOVE:.2f} a run: add --allow-expensive"
        )
    if problems:
        return Decision(False, tuple(f"refused: {p}" for p in problems))
    if not go:
        return Decision(
            False, ("nothing was spent. To run it, add --go (and --max-spend if you want a cap).",)
        )
    return Decision(True, ())


# ---- the ledger ----------------------------------------------------------------------------------


def ledger_path(root: Path) -> Path:
    return root / "ledger.jsonl"


def record(root: Path, entry: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
    with ledger_path(root).open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def read_ledger(root: Path) -> list[dict[str, Any]]:
    path = ledger_path(root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def total_spent(rows: list[dict[str, Any]]) -> float:
    return round(sum(float(r.get("spent_usd", 0.0)) for r in rows), 6)
