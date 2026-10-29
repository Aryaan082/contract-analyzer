"""What the paid stages of the source-review trial will cost, worked out before anything is spent.

Every figure here is ARITHMETIC on assumptions, not a measurement: nothing has been sent to any
model. The formula is the note's (4.5, 6.6): a reviewer call costs

    input_tokens x input price + output_tokens x output price

with the whole 32-entry checklist in every call and no prompt-cache discount (an upper bound). The
output is assumed to be 6,000 tokens per call for the models that think first (their hidden thinking
is billed as output) and 3,000 for the Claude models. Prices are USD per million tokens, read from
the public catalogue on 2026-10-28 (the three tiers in `agent/models.toml` match it; a test checks).

Stages (decision 17; Aryaan is asked again before each spend, T3 is decided after he sees the rest):

    T1  smoke   the hostile pairs and four planted items on the `eval` tier, R=3       cap $0.50
    T2  dev     all dev items, R=2, stripped and raw views, plus the blind second pass cap $2.00
    T2b models  the same 56-item pass on other cheap reviewers, one run each           cap $1.00
    T3  Sonnet  the same dev items on Sonnet 5.5, R=1                                  cap $6.00

The 56-item pass is the note's: 72 reviewer calls and about 1.09M input tokens (10 incidents
averaging 2 calls, 10 SmartBugs, 12 benign, 14 hostile fixtures and twins, 10 planted variants).
SmartBugs is a download Aryaan has not approved, so those 10 items are part of the note's mix and
not of anything that can run today.
"""

from collections.abc import Sequence
from dataclasses import dataclass

CHARS_PER_TOKEN = 3.5  # crude, as everywhere in the note; no tokenizer was run

FULL_PASS_ITEMS = 56
FULL_PASS_CALLS = 72
FULL_PASS_INPUT_TOKENS = 1_090_000

CAPS = {"T1": 0.50, "T2": 2.00, "T2b": 1.00, "T3": 6.00}
CAP_TOTAL = 9.50  # decision 17: T1 + T2 + T2b + T3 together
THINKING_OUTPUT = 6_000
CLAUDE_OUTPUT = 3_000
CONFIRMATION_USD = 0.10  # the note's allowance for the blind second pass on the eval tier
NOTE_T1 = ("about 54 runs at roughly $0.005 each", 0.30)
NOTE_T2 = ("about $1.0 to $1.6", 1.6)
NOTE_T3 = ("about $4.5 to $6", 6.0)


@dataclass(frozen=True)
class Price:
    slug: str
    input_per_mtok: float
    output_per_mtok: float
    output_tokens: int  # assumed per call
    note: str = ""

    def call(self, input_tokens: float) -> float:
        return (
            input_tokens * self.input_per_mtok + self.output_tokens * self.output_per_mtok
        ) / 1_000_000


EVAL = Price("deepseek/deepseek-v4.1-flash-1017", 0.15, 0.60, THINKING_OUTPUT, "the `eval` tier")
HAIKU = Price("anthropic/claude-haiku-5.5", 0.10, 0.50, CLAUDE_OUTPUT)
SONNET = Price("anthropic/claude-sonnet-5.5", 2.0, 10.0, CLAUDE_OUTPUT)
GLM = Price("z-ai/glm-5.3-flash", 0.026, 0.10, THINKING_OUTPUT)
QWEN = Price("qwen/qwen3.7-flash", 0.03, 0.13, THINKING_OUTPUT)
MIMO = Price("xiaomi/mimo-v2.6-pro", 0.435, 0.87, THINKING_OUTPUT, "slug not checked")
MINIMAX = Price("minimax/minimax-m3", 0.30, 1.20, THINKING_OUTPUT, "slug not checked")

T2B_MODELS = (
    EVAL,
    HAIKU,
    GLM,
    QWEN,
)  # approved in principle, 2026-10-28; MiMo and MiniMax left out
PRICES = (EVAL, HAIKU, SONNET, GLM, QWEN, MIMO, MINIMAX)


def tokens_of(chars: int) -> float:
    return chars / CHARS_PER_TOKEN


def full_pass_cost(price: Price) -> float:
    """The note's 56-item pass: 72 calls with 1.09M input tokens between them."""
    return (
        FULL_PASS_INPUT_TOKENS * price.input_per_mtok
        + FULL_PASS_CALLS * price.output_tokens * price.output_per_mtok
    ) / 1_000_000


@dataclass(frozen=True)
class Row:
    label: str
    usd: float
    detail: str = ""


@dataclass(frozen=True)
class Estimate:
    stage: str
    title: str
    rows: tuple[Row, ...]
    cap: float
    notes: tuple[str, ...] = ()

    @property
    def total(self) -> float:
        return round(sum(r.usd for r in self.rows), 4)

    @property
    def within_cap(self) -> bool:
        return self.total <= self.cap + 1e-12

    def lines(self) -> list[str]:
        out = [f"{self.stage}  {self.title}"]
        out += [f"  {r.label:<38} ${r.usd:>7.3f}  {r.detail}".rstrip() for r in self.rows]
        verdict = "within" if self.within_cap else "OVER"
        out.append(
            f"  {'total (unmeasured arithmetic)':<38} ${self.total:>7.3f}  "
            f"{verdict} the cap of ${self.cap:.2f}"
        )
        out += [f"  - {n}" for n in self.notes]
        return out


# ---- T1 --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class T1Plan:
    """What T1 would run: the model calls each scenario makes (from a scripted dry run, so the
    prompt sizes are the real ones), times the repeats."""

    pairs: int  # hostile cases with a clean twin
    scenarios: int  # hostile and twin scenarios that call the model
    planted: int
    repeats: int
    call_tokens: tuple[float, ...]  # input tokens of one repeat's calls, estimated from characters

    @property
    def runs(self) -> int:
        return (self.scenarios + self.planted) * self.repeats

    @property
    def calls(self) -> int:
        return len(self.call_tokens) * self.repeats


def t1_estimate(plan: T1Plan, price: Price = EVAL) -> Estimate:
    total = sum(price.call(t) for t in plan.call_tokens) * plan.repeats
    mean_tokens = sum(plan.call_tokens) / len(plan.call_tokens) if plan.call_tokens else 0
    rows = (
        Row(
            f"{plan.calls} reviewer calls on {price.slug.split('/')[-1]}",
            total,
            f"{plan.runs} runs ({plan.pairs} pairs + {plan.planted} planted items), "
            f"R={plan.repeats}, {mean_tokens:,.0f} input tokens a call",
        ),
    )
    notes = (
        f"the note's own figure: {NOTE_T1[0]}, ${NOTE_T1[1]:.2f}; this list is longer because it "
        "counts every pair the suite has, hostile and twin separately",
        f"each call assumed to produce {price.output_tokens:,} output tokens at "
        f"${price.output_per_mtok}/Mtok; prompt sizes are the real ones (characters / 3.5)",
        "an over-cap estimate is Aryaan's call: fewer cases (--cases) or repeats (--repeats)",
    )
    return Estimate(
        "T1", "smoke: hostile pairs and planted items, real reviewer", rows, CAPS["T1"], notes
    )


# ---- T2, T2b, T3 -----------------------------------------------------------------------------


def t2_estimate(
    *,
    dev_share: float = 2 / 3,
    repeats: int = 2,
    arms: int = 2,
    price: Price = EVAL,
    confirmation_usd: float = CONFIRMATION_USD,
) -> Estimate:
    """Dev items only, R repeats, the stripped view and the raw view (eval only) for the A/B, plus
    the blind second pass on the definitions that hold a high candidate."""
    one_pass = full_pass_cost(price) * dev_share
    rows = (
        Row(
            f"{arms} views x R={repeats} x {dev_share:.0%} of the 56-item pass",
            one_pass * repeats * arms,
            f"one full pass on {price.slug.split('/')[-1]} = ${full_pass_cost(price):.3f}",
        ),
        Row("blind second pass (allowance)", confirmation_usd, "from another company"),
    )
    notes = (
        f"the note's own figure: {NOTE_T2[0]}",
        "the faithfulness judges are not included: the source rubric is not approved, so nothing "
        "may be called judged (add them only after Aryaan approves it)",
        "the 10 SmartBugs items need a download Aryaan has not approved; the real run is smaller",
    )
    return Estimate("T2", "dev items, stripped and raw view, second pass", rows, CAPS["T2"], notes)


def t2b_estimate(models: Sequence[Price] = T2B_MODELS, extra: Sequence[Price] = ()) -> Estimate:
    rows = tuple(
        Row(
            p.slug,
            full_pass_cost(p),
            f"{FULL_PASS_CALLS} calls, ${p.input_per_mtok}/${p.output_per_mtok} per Mtok, "
            f"{p.output_tokens:,} out a call" + (f" ({p.note})" if p.note else ""),
        )
        for p in [*models, *extra]
    )
    notes = (
        "one 56-item pass each, same prompt, same labelled set, one run each (note 6.6)",
        "what this can show reliably: cost per review, seconds, reply-format failures, fail-closed "
        "rate, coverage. Accuracy only weakly: under 10 to 15 points is not shown at ~50 items",
        "time is a guess: 72 calls at 30 to 100 s is 40 to 120 minutes one at a time",
    )
    return Estimate("T2b", "cheap reviewer comparison, one pass each", rows, CAPS["T2b"], notes)


def t3_estimate(price: Price = SONNET, items_share: float = 2 / 3) -> Estimate:
    rows = (
        Row(
            f"{items_share:.0%} of the pass on {price.slug.split('/')[-1]}, R=1",
            full_pass_cost(price) * items_share,
            f"full pass = ${full_pass_cost(price):.2f}",
        ),
    )
    notes = (
        f"the note's own figure: {NOTE_T3[0]} (it adds the plain-model arm on the incident items)",
        "over the $4-a-run rule of Component 6: a full T3 needs an explicit go; decided after "
        "T1, T2 and T2b",
    )
    return Estimate("T3", "milestone: Sonnet 5.5 on the dev items", rows, CAPS["T3"], notes)


def all_stages(plan: T1Plan) -> list[Estimate]:
    return [t1_estimate(plan), t2_estimate(), t2b_estimate(), t3_estimate()]


def render(estimates: Sequence[Estimate]) -> list[str]:
    out = [
        "Estimates for the paid trial. NOTHING HAS BEEN SPENT and nothing here calls a model.",
        "All figures are arithmetic on stated assumptions (see evals/source_review/costs.py).",
        "",
    ]
    for e in estimates:
        out += e.lines() + [""]
    out.append(
        f"Caps of decision 17: T1 ${CAPS['T1']:.2f} + T2 ${CAPS['T2']:.2f} "
        f"+ T2b ${CAPS['T2b']:.2f} + T3 ${CAPS['T3']:.2f} = ${CAP_TOTAL:.2f}. "
        "Aryaan is asked again before each stage."
    )
    return out
