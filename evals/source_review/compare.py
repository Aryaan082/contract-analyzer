"""Head-to-head: one model reading contracts plain (arm P) and through our source review (arm A).

Same model, same contract text, temperature 0, same output limit. Arm P (`plain.py`) sends the raw
source with a short neutral instruction. Arm A is `review.reviewer.run_review` with the model as
the reviewer (stripped view, 32-entry checklist, span checks), no blind second pass. A is NOT the
full `python -m agent analyze`: planted variants are not deployed, so the full agent cannot see
them; what is compared is the reading of source, not the whole agent.

Items (DEV split only; held-out items are never loaded here), in this order so that a run stopped
by its cap still holds whole families: the planted variants (vulnerable, safe twin, renamed copy,
new code no model has seen), then the labelled dev incidents (public code, labels from public
write-ups: MAY BE MEMORISED, reported apart), then the dev benign contracts that the explorer can
read. Third-party source is fetched at run time and never written to the repo; the results hold
names, lines, severities, counts and money, no source text.

Scoring is deterministic and function-level, against the draft labels (`reviewed_by` is empty on
all of them: NO NUMBER IS FINAL until Aryaan reviews ten labels). No model judges anything.

Money: `Budget` wraps the one real client. Before each request it refuses if the money already
spent plus the worst this request could cost (a pessimistic token count and the full output limit)
would pass `--max-spend`; after each reply it adds the cost OpenRouter reported. A refused request
is "unavailable": its contract is not scored, and the report lists what was and was not run.
"""

import json
import math
import os
import statistics
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from agent.config import ModelSpec
from evals.metrics import Interval, paired_difference
from evals.source_review import costs, plain
from evals.source_review.cards import REPO_ROOT, Card, CardSet
from evals.source_review.hostile import Scenario, make_bundle, scripted_factory
from evals.source_review.items import fetch_bundle, flaws_of
from evals.source_review.scoring import (
    Flag,
    RunCounts,
    flags_of,
    ratio_interval,
    share_interval,
)
from review.client import (
    UNAVAILABLE,
    ModelUnavailable,
    OpenRouterReviewer,
    ReviewerCall,
    ReviewerClient,
    ScriptedReviewer,
)
from review.known import KnownList
from review.pack import Prompt
from review.reviewer import ReviewResult, run_review
from review.types import REVIEWER_SCHEMA, SEVERITY_RANK, Bundle

OPUS = ModelSpec(name="anthropic/claude-opus-5.5", input_per_mtok=4.0, output_per_mtok=20.0)
RESULTS_DIR = REPO_ROOT / "evals" / "source_review" / "results"
FIXTURE_PATH = "contracts/Contract.sol"  # one neutral name for every fixture, so a file name
# never says `vulnerable` to the model that reads paths (arm P)
OUT_CENTRAL = costs.CLAUDE_OUTPUT  # assumed output tokens per request, central case
RETRIES = 6  # backoff 1, 2, 4, 8, 16, 32 s: a provider hiccup of a minute is survived
OUT_LEAN = 1_500  # the lean case in the estimate
PESSIMISTIC_CHARS_PER_TOKEN = 3.0  # for the spend guard: more tokens than the central estimate
GROUPS = ("planted_vulnerable", "planted_safe", "planted_renamed", "incident", "benign")
BANNER = (
    "DRAFT LABELS: every label is unreviewed (reviewed_by is empty on all of them), so no number "
    "here is final until Aryaan has reviewed ten. One run per contract at temperature 0: model "
    "noise is not measured."
)
MAX_SILENT = 3
INCIDENT_NOTE = "public code with public write-ups: a model may have seen both (MAY BE MEMORISED)"


# ---- items and sources ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    card: Card
    group: str

    @property
    def id(self) -> str:
        return self.card.id


def dev_items(cards: CardSet) -> list[Item]:
    """The dev items in run order. Planted: file order, so a family stays together."""
    safe = cards.twin_ids
    out = []
    for card in cards.of("planted", split="dev"):
        if card.id in safe:
            group = "planted_safe"
        elif card.renamed_of:
            group = "planted_renamed"
        elif card.functions:
            group = "planted_vulnerable"
        else:
            continue
        out.append(Item(card, group))
    out += [Item(c, "incident") for c in cards.of("incident", split="dev") if c.functions]
    out += [Item(c, "benign") for c in cards.of("benign", split="dev")]
    return out


@dataclass(frozen=True)
class Source:
    bundle: Bundle | None
    known: KnownList | None  # None: the real known-library list (contracts); empty for fixtures
    status: str = "ok"
    reason: str = ""

    @property
    def files(self) -> tuple[tuple[str, str], ...]:
        return tuple((f.path, f.content) for f in self.bundle.files) if self.bundle else ()


def fixture_source(card: Card) -> Source:
    path = card.fixture_path
    if path is None:
        raise ValueError(f"{card.id} is a contract reference, not a fixture")
    return Source(make_bundle([(FIXTURE_PATH, path.read_text())]), KnownList())


def load_sources(
    items: Sequence[Item], *, explorer: Any = None, cache_dir: Path | None = None
) -> dict[str, Source]:
    """Fixture files are read; contracts are fetched through the read-only explorer client (a
    bundle cached under `cache_dir`, outside the repo, is reused). Nothing goes into the repo."""
    out: dict[str, Source] = {}
    for item in items:
        card = item.card
        if card.contract is None:
            out[item.id] = fixture_source(card)
            continue
        cached = cache_dir / f"{card.id}.json" if cache_dir else None
        if cached and cached.exists():
            out[item.id] = Source(Bundle.model_validate_json(cached.read_text()), None)
            continue
        if explorer is None:
            out[item.id] = Source(None, None, "unavailable", "no explorer key (ETHERSCAN_API_KEY)")
            continue
        got = fetch_bundle(explorer, card.contract)
        if got.status != "ok" or got.bundle is None:
            out[item.id] = Source(None, None, got.status, got.reason or got.status)
            continue
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(got.bundle.model_dump_json())
        out[item.id] = Source(got.bundle, None)
    return out


def make_explorer():
    """The existing read-only explorer client, key from `.env` (never printed), or None."""
    from dotenv import load_dotenv

    from chain.explorer import ExplorerClient
    from retrieval.paths import env_files

    for path in env_files():
        load_dotenv(path)
    return ExplorerClient.from_env(os.environ)


# ---- the money -----------------------------------------------------------------------------------


def call_usd(input_tokens: float, output_tokens: float, spec: ModelSpec = OPUS) -> float:
    return (input_tokens * spec.input_per_mtok + output_tokens * spec.output_per_mtok) / 1e6


def with_schema(prompt: Prompt, schema: dict) -> Prompt:
    """The prompt with the reply's JSON schema written into the system message, for a model that
    cannot be given `response_format`. The same text goes to both arms."""
    note = (
        "\n\nThe JSON schema of your reply. Reply with one JSON object that matches it and "
        "nothing else:\n" + json.dumps(schema, separators=(",", ":"))
    )
    return replace(prompt, system=prompt.system + note)


class Budget:
    """The one real client behind a spend cap. It is the only object that reaches the model.
    `schema_in_prompt` writes the reply schema into each prompt (before the cost guard sees it)."""

    def __init__(
        self,
        inner: ReviewerClient,
        max_usd: float,
        spec: ModelSpec = OPUS,
        *,
        schema_in_prompt: bool = False,
    ) -> None:
        self.inner, self.max_usd, self.spec = inner, max_usd, spec
        self.schema_in_prompt = schema_in_prompt
        self.name = inner.name
        self.spent = 0.0
        self.refused = 0  # requests not sent because they might have passed the cap
        self.errors: list[str] = []  # why requests failed (the service's words, bounded)

    def review(self, prompt: Prompt, schema: dict, *, max_tokens: int) -> ReviewerCall:
        if self.schema_in_prompt:
            prompt = with_schema(prompt, schema)
        worst = call_usd(prompt.chars / PESSIMISTIC_CHARS_PER_TOKEN, max_tokens, self.spec)
        if self.spent + worst > self.max_usd + 1e-12:
            self.refused += 1
            raise ModelUnavailable("the spend cap would be passed")
        try:
            reply = self.inner.review(prompt, schema, max_tokens=max_tokens)
        except ModelUnavailable as err:
            if len(self.errors) < 50:
                self.errors.append(str(err)[:240])
            raise
        self.spent += reply.cost_usd
        return reply


def credits_remaining(opener: Callable = urllib.request.urlopen) -> float | None:
    """What the OpenRouter account has left (read-only endpoint, no spend); None if unreadable.
    OpenRouter refuses a request with a 402 when this is below the request's worst case, which is
    how an empty account looks from the runner."""
    from dotenv import load_dotenv

    from retrieval.embedders import KEY_VARIABLE
    from retrieval.paths import env_files

    for path in env_files():
        load_dotenv(path)
    key = os.environ.get(KEY_VARIABLE, "").strip()
    if not key:
        return None
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/credits", headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with opener(request, timeout=30) as response:
            data = json.load(response)["data"]
        return float(data["total_credits"]) - float(data["total_usage"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def real_budget(max_usd: float) -> Budget:
    """The only way the paid run reaches the model: the real client behind the cap. Measured
    2026-10-29: OpenRouter has no endpoint for anthropic/claude-opus-5.5 that accepts
    `response_format` (a 404 for json_schema and for json_object), so both arms ask for JSON in
    the prompt (`structured="none"`) with the schema written into the system message; each arm
    checks the reply strictly all the same."""
    client = OpenRouterReviewer(OPUS, structured="none", retries=RETRIES)
    return Budget(client, max_usd, schema_in_prompt=True)


# ---- the estimate --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemEstimate:
    item: str
    group: str
    p_calls: int
    p_tokens: float  # estimated input tokens over all of arm P's requests
    a_calls: int
    a_tokens: float

    def usd(self, *, ceiling: bool, max_tokens: int = plain.DEFAULT_MAX_TOKENS) -> float:
        per = costs.CHARS_PER_TOKEN / PESSIMISTIC_CHARS_PER_TOKEN if ceiling else 1.0
        out = max_tokens if ceiling else OUT_CENTRAL
        calls = self.p_calls + self.a_calls
        return call_usd((self.p_tokens + self.a_tokens) * per, calls * out)


def estimate_item(item: Item, source: Source) -> ItemEstimate | None:
    """What the two arms would send for this item, from the real prompts of both (a scripted
    reviewer records arm A's; no model is called). None if the source cannot be read."""
    if source.bundle is None:
        return None
    plain_chars = [
        with_schema(p, plain.PLAIN_SCHEMA).chars for p in plain.plain_prompts(source.files)
    ]
    probe = ScriptedReviewer(replies=['{"items": []}'])
    run_review(source.bundle, probe, known=source.known, second=None)
    return ItemEstimate(
        item.id,
        item.group,
        len(plain_chars),
        sum(costs.tokens_of(c) for c in plain_chars),
        len(probe.prompts),
        sum(costs.tokens_of(with_schema(p, REVIEWER_SCHEMA).chars) for p in probe.prompts),
    )


def estimate_lines(estimates: Sequence[ItemEstimate], unreadable: dict[str, str]) -> list[str]:
    out = ["Cost estimate (arithmetic on the real prompts; NO MODEL HAS BEEN CALLED)"]
    out.append(
        f"model {OPUS.name}: ${OPUS.input_per_mtok}/Mtok in, ${OPUS.output_per_mtok}/Mtok out; "
        "no prompt caching; tokens = characters / 3.5 (no tokenizer was run)"
    )
    out.append(
        f"{'group':<20}{'items':>6}{'P calls':>9}{'A calls':>9}{'in tokens':>12}{'central':>10}"
    )
    for group in GROUPS:
        rows = [e for e in estimates if e.group == group]
        if rows:
            out.append(
                f"{group:<20}{len(rows):>6}{sum(e.p_calls for e in rows):>9}"
                f"{sum(e.a_calls for e in rows):>9}"
                f"{sum(e.p_tokens + e.a_tokens for e in rows):>12,.0f}"
                f"{'$' + format(sum(e.usd(ceiling=False) for e in rows), '.2f'):>10}"
            )
    big = sorted(estimates, key=lambda e: -e.usd(ceiling=False))[:5]
    out.append(
        "costliest items (central): "
        + ", ".join(f"{e.item} ${e.usd(ceiling=False):.2f}" for e in big)
    )
    central = sum(e.usd(ceiling=False) for e in estimates)
    ceiling = sum(e.usd(ceiling=True) for e in estimates)
    arm_p = sum(call_usd(e.p_tokens, e.p_calls * OUT_CENTRAL) for e in estimates)
    out.append(
        f"TOTAL {len(estimates)} items: central ${central:.2f} (arm P ${arm_p:.2f}, arm A "
        f"${central - arm_p:.2f}; {OUT_CENTRAL:,} output tokens a request), ceiling ${ceiling:.2f} "
        f"(3.0 chars a token, every request writing {plain.DEFAULT_MAX_TOKENS:,} tokens)"
    )
    requests = sum(e.p_calls + e.a_calls for e in estimates)
    lean = central - requests * (OUT_CENTRAL - OUT_LEAN) * OPUS.output_per_mtok / 1e6
    out.append(
        f"lean case (replies of {OUT_LEAN:,} tokens, {requests} requests): ${lean:.2f}. "
        "No reply length has been measured."
    )
    for item, why in unreadable.items():
        out.append(f"not readable, not run: {item}: {why}")
    return out


# ---- running one item ----------------------------------------------------------------------------


@dataclass
class ArmRecord:
    arm: str
    complete: bool  # every request was answered (a refused or failed one makes it incomplete)
    flags: list[dict] = field(default_factory=list)  # names, lines, severity: no source text
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    status: str = ""  # arm A's coverage status; arm P's is "read"
    failures: list[str] = field(default_factory=list)  # reply-format failures, fixed words
    dropped: dict[str, int] = field(default_factory=dict)  # reply items thrown away, by reason

    def flag_list(self) -> tuple[Flag, ...]:
        return tuple(Flag(**d) for d in self.flags)


def run_arm_p(source: Source, client: ReviewerClient, max_tokens: int) -> ArmRecord:
    start = time.monotonic()
    run = plain.run_plain(source.files, client, max_tokens=max_tokens)
    return ArmRecord(
        "P",
        not run.unavailable,
        [asdict(f) for f in run.flags],
        run.calls,
        run.prompt_tokens,
        run.completion_tokens,
        run.cost_usd,
        time.monotonic() - start,
        "read",
        list(run.failures),
        dict(run.dropped),
    )


def run_arm_a(source: Source, client: ReviewerClient, max_tokens: int) -> ArmRecord:
    start = time.monotonic()
    assert source.bundle is not None
    result = run_review(
        source.bundle, client, known=source.known, second=None, max_tokens=max_tokens
    )
    return record_of_review(result, time.monotonic() - start)


def record_of_review(result: ReviewResult, seconds: float) -> ArmRecord:
    calls = result.calls
    return ArmRecord(
        "A",
        not any(c.unreadable == UNAVAILABLE for c in calls),
        [asdict(f) for f in flags_of(result)],
        len(calls),
        sum(c.prompt_tokens for c in calls),
        sum(c.completion_tokens for c in calls),
        sum(c.cost_usd for c in calls),
        seconds,
        result.summary.status,
        [c.unreadable for c in calls if c.unreadable and c.unreadable != UNAVAILABLE],
        dict(result.dropped),
    )


ClientFor = Callable[[Item, Source], ReviewerClient]


def run_compare(
    items: Sequence[Item],
    sources: dict[str, Source],
    plain_client: ClientFor,
    our_client: ClientFor,
    *,
    spent: Callable[[], float],
    max_spend: float,
    done: dict[str, dict] | None = None,
    checkpoint: Path | None = None,
    max_tokens: int = plain.DEFAULT_MAX_TOKENS,
    out: Callable[[str], None] = print,
) -> list[dict]:
    """Run both arms on every item in order, stopping when the next item would not fit under
    `max_spend` (central estimate; each request is also guarded by `Budget`). `done` holds records
    of an earlier, stopped run, which are kept and not re-run. Returns one record per item."""
    records = []
    stopped = ""  # why the run stopped, once it has
    silent = 0  # items in a row where no request was answered
    for item in items:
        if done and item.id in done and _finished(done[item.id]):
            records.append(done[item.id])
            continue
        source = sources[item.id]
        if source.bundle is None:
            records.append({"item": item.id, "group": item.group, "skipped": source.reason})
            continue
        est = estimate_item(item, source)
        if not stopped and est and spent() + est.usd(ceiling=False) > max_spend:
            stopped = "spend cap"
        if stopped:
            records.append({"item": item.id, "group": item.group, "skipped": stopped})
            out(f"  {item.id}: NOT RUN ({stopped})")
            continue
        p = run_arm_p(source, plain_client(item, source), max_tokens)
        a = run_arm_a(source, our_client(item, source), max_tokens)
        record = {"item": item.id, "group": item.group, "P": asdict(p), "A": asdict(a)}
        records.append(record)
        silent = silent + 1 if not (p.complete or a.complete) else 0
        if silent >= MAX_SILENT:
            stopped = "model not answering"
            out(f"  stopping: no request was answered on {MAX_SILENT} items in a row")
        if checkpoint:
            with checkpoint.open("a") as f:
                f.write(json.dumps(record) + "\n")
        out(
            f"  {item.id}: P {len(p.flags)} flags ${p.cost_usd:.3f} {p.seconds:.0f}s | "
            f"A {len(a.flags)} flags ${a.cost_usd:.3f} {a.seconds:.0f}s | spent ${spent():.3f}"
        )
    return records


def scripted_plain(item: Item, source: Source) -> ReviewerClient:
    """Arm P's stand-in for the free runs: names exactly the labelled functions."""
    findings = [
        {
            "file": FIXTURE_PATH,
            "contract": f.contract,
            "function": f.function,
            "start_line": 1,
            "end_line": 1,
            "severity": "medium",
            "description": "scripted",
        }
        for f in item.card.functions
    ]
    return ScriptedReviewer([json.dumps({"findings": findings})], name=OPUS.name, cost_usd=0.02)


def scripted_ours(item: Item, source: Source) -> ReviewerClient:
    """Arm A's stand-in for the free runs: the kit's scripted reviewer, handed the labels."""
    flaws = flaws_of(item.card)
    return scripted_factory(Scenario(files=source.files, flaws=flaws), flaws, OPUS.name)


def scripted_run(
    items: Sequence[Item],
    sources: dict[str, Source],
    *,
    max_spend: float = 1.0,
    out: Callable[[str], None] = print,
) -> tuple[list[dict], float]:
    """Both arms with the scripted clients behind real `Budget`s: the free end-to-end run."""
    budgets: list[Budget] = []

    def behind_budget(make: ClientFor) -> ClientFor:
        def factory(item: Item, source: Source) -> ReviewerClient:
            budgets.append(Budget(make(item, source), max_spend))
            return budgets[-1]

        return factory

    def total() -> float:
        return sum(b.spent for b in budgets)

    records = run_compare(
        items,
        sources,
        behind_budget(scripted_plain),
        behind_budget(scripted_ours),
        spent=total,
        max_spend=max_spend,
        out=out,
    )
    return records, total()


def have_key() -> bool:
    """Whether OPENROUTER_API_KEY is set (in the shell or a `.env`); the value is never read out."""
    from dotenv import load_dotenv

    from retrieval.embedders import KEY_VARIABLE
    from retrieval.paths import env_files

    for path in env_files():
        load_dotenv(path)
    return bool(os.environ.get(KEY_VARIABLE, "").strip())


def _finished(record: dict) -> bool:
    return "P" in record and record["P"]["complete"] and record["A"]["complete"]


def read_checkpoint(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {
        r["item"]: r for r in (json.loads(line) for line in path.read_text().splitlines() if line)
    }


# ---- scoring -------------------------------------------------------------------------------------


def counts_from_flags(card: Card, flags: Sequence[Flag], *, min_severity: str = "low") -> RunCounts:
    """`scoring.run_counts` over bare flags (arm P has no `ReviewResult`); a test checks that the
    two agree on arm A's results."""
    floor = SEVERITY_RANK[min_severity]
    by_key: dict[tuple[str | None, str | None], set[str]] = {}
    loose = set()
    for f in flags:
        if SEVERITY_RANK[f.severity] < floor:
            continue
        if f.function is None or f.contract is None:
            loose.add((f.file_id, f.start_line, f.end_line))
        else:
            by_key.setdefault(f.key, set()).add(f.entry)
    hit = set(by_key) & card.function_keys
    return RunCounts(
        tp=len(hit),
        fp=len(set(by_key) - card.function_keys) + len(loose),
        fn=len(card.function_keys - set(by_key)),
        flagged_labelled=len(hit),
        entry_right=0,
    )


def sign_test_p(a_only: int, p_only: int) -> float:
    """Two-sided exact sign test on the pairs where exactly one arm found the bug."""
    n = a_only + p_only
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(a_only, p_only) + 1)) / 2**n
    return min(1.0, 2 * tail)


def smallest_split(n: int) -> str:
    """The most even split of `n` one-sided pairs that the sign test calls significant (p<0.05)."""
    for losing in range(n // 2, -1, -1):
        if sign_test_p(n - losing, losing) < 0.05:
            return f"{n - losing}:{losing}"
    return "none (too few pairs)"


def _num(x: float) -> float | None:
    return None if x != x else round(x, 4)


def _iv(iv: Interval) -> list[float | None]:
    return [_num(iv.mean), _num(iv.low), _num(iv.high)]


def fmt(iv: Sequence[float | None] | Interval) -> str:
    mean, low, high = (iv.mean, iv.low, iv.high) if isinstance(iv, Interval) else iv
    if mean is None or mean != mean:
        return "n/a"
    return f"{mean:.2f} [{low:.2f}–{high:.2f}]"


def _scored(records: Sequence[dict]) -> list[dict]:
    """Items where both arms answered every request: the only ones that are compared."""
    return [r for r in records if "P" in r and r["P"]["complete"] and r["A"]["complete"]]


def build_report(records: Sequence[dict], cards: CardSet, meta: dict) -> dict:
    scored = _scored(records)
    by_id = {r["item"]: r for r in scored}
    flags = {
        (r["item"], arm): ArmRecord(**r[arm]).flag_list() for r in scored for arm in ("P", "A")
    }
    report: dict[str, Any] = {"meta": meta, "banner": BANNER, "groups": {}, "paired": {}}
    for group in GROUPS:
        members = [r for r in scored if r["group"] == group]
        report["groups"][group] = {arm: _group(cards, members, flags, arm) for arm in ("P", "A")}
        report["groups"][group]["n"] = len(members)
    for group in ("planted_vulnerable", "planted_renamed", "incident"):
        members = [r for r in scored if r["group"] == group]
        report["paired"][group] = _paired(cards, members, flags)
    report["twins"] = _twins(cards, by_id, flags)
    report["renamed_gap"] = _renamed_gap(cards, by_id, flags)
    report["cost"] = _cost(records)
    report["failures"] = _failures(records)
    report["per_item"] = [_row(cards, r, flags) for r in records]
    report["not_scored"] = [
        {
            "item": r["item"],
            "why": r.get("skipped")
            or "a request was refused or failed: "
            + ", ".join(a for a in ("P", "A") if not r[a]["complete"]),
        }
        for r in records
        if r not in scored
    ]
    return report


def _group(cards: CardSet, members: list[dict], flags: dict, arm: str) -> dict:
    rows = [(cards.get(r["item"]), flags[(r["item"], arm)]) for r in members]
    if not rows:
        return {}
    out: dict[str, Any] = {"flags_per_item": _num(statistics.fmean(len(f) for _, f in rows))}
    labelled = [(c, f) for c, f in rows if c.functions]
    if labelled:
        counts = [counts_from_flags(c, f) for c, f in labelled]
        out["found_items"] = sum(x.tp >= 1 for x in counts)
        out["labelled_items"] = len(counts)
        out["recall"] = _iv(ratio_interval([[(x.tp, x.tp + x.fn)] for x in counts]))
        out["precision"] = _iv(ratio_interval([[(x.tp, x.tp + x.fp)] for x in counts]))
        medium = [counts_from_flags(c, f, min_severity="medium") for c, f in labelled]
        out["precision_medium_plus"] = _iv(ratio_interval([[(x.tp, x.tp + x.fp)] for x in medium]))
        out["recall_medium_plus"] = _iv(ratio_interval([[(x.tp, x.tp + x.fn)] for x in medium]))
    else:
        medium_flags = [[f for f in fl if SEVERITY_RANK[f.severity] >= 1] for _, fl in rows]
        out["with_medium_plus_flag"] = sum(bool(m) for m in medium_flags)
        out["false_alarm_rate"] = _iv(share_interval([[bool(m)] for m in medium_flags]))
        out["medium_plus_flags_per_item"] = _num(statistics.fmean(len(m) for m in medium_flags))
    return out


def _paired(cards: CardSet, members: list[dict], flags: dict) -> dict:
    if not members:
        return {}
    both = a_only = p_only = neither = 0
    recall_p, recall_a = [], []
    for r in members:
        card = cards.get(r["item"])
        got = {}
        for arm in ("P", "A"):
            x = counts_from_flags(card, flags[(r["item"], arm)])
            got[arm] = x
        recall_p.append(got["P"].tp / (got["P"].tp + got["P"].fn))
        recall_a.append(got["A"].tp / (got["A"].tp + got["A"].fn))
        hit_p, hit_a = got["P"].tp >= 1, got["A"].tp >= 1
        both += hit_p and hit_a
        a_only += hit_a and not hit_p
        p_only += hit_p and not hit_a
        neither += not hit_p and not hit_a
    diff = paired_difference(recall_a, recall_p)
    return {
        "items": len(members),
        "both_found": both,
        "a_found_p_missed": a_only,
        "p_found_a_missed": p_only,
        "neither": neither,
        "sign_test_p": round(sign_test_p(a_only, p_only), 4),
        "needs_for_p_under_0.05": smallest_split(a_only + p_only),
        "recall_a_minus_p": _iv(diff.interval),
        "difference_shown": diff.shown,
    }


def _twins(cards: CardSet, by_id: dict, flags: dict) -> dict:
    """Per arm, over the planted families: the bug found in the vulnerable code AND the fixed
    function not flagged in the safe twin (the model told the two apart)."""
    out: dict[str, Any] = {}
    for arm in ("P", "A"):
        pairs = separated = fixed_flagged = 0
        for card in cards.of("planted", split="dev"):
            safe = cards.twin_of(card)
            if not card.functions or safe is None or card.id not in by_id or safe.id not in by_id:
                continue
            pairs += 1
            found = counts_from_flags(card, flags[(card.id, arm)]).tp >= 1
            on_fix = {f.key for f in flags[(safe.id, arm)]} & card.function_keys
            fixed_flagged += bool(on_fix)
            separated += found and not on_fix
        out[arm] = {"pairs": pairs, "separated": separated, "fixed_function_flagged": fixed_flagged}
    return out


def _renamed_gap(cards: CardSet, by_id: dict, flags: dict) -> dict:
    out = {}
    for arm in ("P", "A"):
        orig, ren = [], []
        for card in cards.of("planted", split="dev"):
            copies = [c for c in cards.renamed_copies(card) if c.id in by_id]
            if card.id in by_id and card.functions and copies:
                orig.append(counts_from_flags(card, flags[(card.id, arm)]).tp >= 1)
                ren.append(counts_from_flags(copies[0], flags[(copies[0].id, arm)]).tp >= 1)
        out[arm] = {"families": len(orig), "original_found": sum(orig), "renamed_found": sum(ren)}
    return out


def _cost(records: Sequence[dict]) -> dict:
    out: dict[str, Any] = {}
    ran = [r for r in records if "P" in r]
    for arm in ("P", "A"):
        usd = [r[arm]["cost_usd"] for r in ran]
        sec = [r[arm]["seconds"] for r in ran]
        out[arm] = {
            "contracts": len(ran),
            "total_usd": round(sum(usd), 4),
            "mean_usd": _num(statistics.fmean(usd)) if usd else None,
            "median_usd": _num(statistics.median(usd)) if usd else None,
            "mean_seconds": _num(statistics.fmean(sec)) if sec else None,
            "median_seconds": _num(statistics.median(sec)) if sec else None,
            "calls": sum(r[arm]["calls"] for r in ran),
            "prompt_tokens": sum(r[arm]["prompt_tokens"] for r in ran),
            "completion_tokens": sum(r[arm]["completion_tokens"] for r in ran),
        }
    out["total_usd"] = round(out["P"]["total_usd"] + out["A"]["total_usd"], 4)
    return out


def _failures(records: Sequence[dict]) -> dict:
    out: dict[str, Any] = {}
    ran = [r for r in records if "P" in r]
    for arm in ("P", "A"):
        reasons: dict[str, int] = {}
        dropped: dict[str, int] = {}
        for r in ran:
            for f in r[arm]["failures"]:
                reasons[f] = reasons.get(f, 0) + 1
            for k, v in r[arm]["dropped"].items():
                dropped[k] = dropped.get(k, 0) + v
        out[arm] = {
            "contracts_with_unreadable_reply": sum(bool(r[arm]["failures"]) for r in ran),
            "unreadable_replies": reasons,
            "reply_items_dropped": dropped,
            "contracts_incomplete": sum(not r[arm]["complete"] for r in ran),
        }
    out["A_status"] = {}
    for r in ran:
        s = r["A"]["status"]
        out["A_status"][s] = out["A_status"].get(s, 0) + 1
    return out


def _row(cards: CardSet, r: dict, flags: dict) -> dict:
    if "P" not in r:
        return {"item": r["item"], "group": r["group"], "not_run": r.get("skipped", "")}
    card = cards.get(r["item"])
    row: dict[str, Any] = {"item": r["item"], "group": r["group"]}
    for arm in ("P", "A"):
        fl = ArmRecord(**r[arm]).flag_list()
        row[arm] = {
            "complete": r[arm]["complete"],
            "found": counts_from_flags(card, fl).tp >= 1 if card.functions else None,
            "flags": len(fl),
            "medium_plus": sum(SEVERITY_RANK[f.severity] >= 1 for f in fl),
            "usd": round(r[arm]["cost_usd"], 4),
            "seconds": round(r[arm]["seconds"], 1),
        }
    return row


# ---- the written report --------------------------------------------------------------------------


def render(report: dict) -> list[str]:
    m = report["meta"]
    g, pr = report["groups"], report["paired"]
    out = [
        f"# Plain {m['model']} versus our source review, same model ({m['date']})",
        "",
        f"**{report['banner']}**",
        "",
        f"Arm P: one request with the raw source and a neutral instruction. Arm A: `run_review` "
        f"(stripped view, checklist, span checks), no second pass. Not the full agent. Dev items "
        f"only. Scored items: {sum(v['n'] for v in g.values())} of {m['items_total']} "
        f"({m['spent']:.2f} dollars spent of a cap of {m['max_spend']:.2f}).",
        "",
        "## Finding the planted bug (function level, 95% interval over items)",
        "",
        "| group | n | P recall | A recall | P precision | A precision |",
        "|---|---|---|---|---|---|",
    ]
    for key in ("planted_vulnerable", "planted_renamed", "incident"):
        if g[key]["n"]:
            p, a = g[key]["P"], g[key]["A"]
            name = key + (" (may be memorised)" if key == "incident" else "")
            out.append(
                f"| {name} | {g[key]['n']} | {p['found_items']}/{p['labelled_items']} "
                f"{fmt(p['recall'])} | {a['found_items']}/{a['labelled_items']} "
                f"{fmt(a['recall'])} | {fmt(p['precision'])} | {fmt(a['precision'])} |"
            )
    out += ["", "## Paired view (an item counts as found if any labelled function was flagged)", ""]
    out += [
        "| group | n | both | A only | P only | neither | sign test p | A−P recall |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key, v in pr.items():
        if v:
            out.append(
                f"| {key} | {v['items']} | {v['both_found']} | {v['a_found_p_missed']} | "
                f"{v['p_found_a_missed']} | {v['neither']} | {v['sign_test_p']} | "
                f"{fmt(v['recall_a_minus_p'])} "
                f"{'SHOWN' if v['difference_shown'] else 'not shown'} |"
            )
    out += ["", "## False alarms", ""]
    out += ["| group | n | P | A |", "|---|---|---|---|"]
    for key in ("planted_safe", "benign"):
        if g[key]["n"]:
            p, a = g[key]["P"], g[key]["A"]
            out.append(
                f"| {key}: any medium-or-high flag | {g[key]['n']} | {p['with_medium_plus_flag']} "
                f"{fmt(p['false_alarm_rate'])} | {a['with_medium_plus_flag']} "
                f"{fmt(a['false_alarm_rate'])} |"
            )
    t = report["twins"]
    out += [
        "",
        f"Twin discrimination (bug found in the vulnerable code and the fixed function not flagged "
        f"in the safe twin), of {t['P']['pairs']} families: P {t['P']['separated']}, "
        f"A {t['A']['separated']}. The fixed function flagged anyway: P "
        f"{t['P']['fixed_function_flagged']}, A {t['A']['fixed_function_flagged']}.",
    ]
    rg = report["renamed_gap"]
    out.append(
        f"Memorisation control, families found in the original / in the renamed copy: P "
        f"{rg['P']['original_found']}/{rg['P']['renamed_found']}, A "
        f"{rg['A']['original_found']}/{rg['A']['renamed_found']} (of {rg['P']['families']})."
    )
    c = report["cost"]
    out += [
        "",
        "## Cost and time per contract",
        "",
        "| arm | total | mean | median | mean s | median s | calls |",
    ]
    out.append("|---|---|---|---|---|---|---|")
    for arm in ("P", "A"):
        x = c[arm]
        out.append(
            f"| {arm} | ${x['total_usd']:.3f} | ${x['mean_usd'] or 0:.3f} | "
            f"${x['median_usd'] or 0:.3f} | {x['mean_seconds'] or 0:.0f} | "
            f"{x['median_seconds'] or 0:.0f} | {x['calls']} |"
        )
    f = report["failures"]
    out += [
        "",
        f"Unreadable replies: P {f['P']['unreadable_replies'] or 'none'}, "
        f"A {f['A']['unreadable_replies'] or 'none'}. Reply items dropped: P "
        f"{f['P']['reply_items_dropped'] or 'none'}, A {f['A']['reply_items_dropped'] or 'none'}. "
        f"Arm A coverage: {f['A_status']}.",
    ]
    if report["not_scored"]:
        out += ["", "## Not scored", ""]
        out += [f"- {n['item']}: {n['why']}" for n in report["not_scored"]]
    out += [
        "",
        "## Per item",
        "",
        "| item | group | P found/flags/med+ | A found/flags/med+ | P $ | A $ |",
    ]
    out.append("|---|---|---|---|---|---|")
    for row in report["per_item"]:
        if "P" in row:
            cells = [
                f"{'-' if row[a]['found'] is None else ('yes' if row[a]['found'] else 'NO')}"
                f"/{row[a]['flags']}/{row[a]['medium_plus']}"
                for a in ("P", "A")
            ]
            out.append(
                f"| {row['item']} | {row['group']} | {cells[0]} | {cells[1]} | "
                f"{row['P']['usd']:.3f} | {row['A']['usd']:.3f} |"
            )
    out += ["", "## What this can and cannot show", ""]
    out += read_limits(report)
    return out


def read_limits(report: dict) -> list[str]:
    v = report["paired"].get("planted_vulnerable") or {}
    lines = [
        "- A run is one reading per contract at temperature 0; repeat noise is unmeasured.",
        "- The three variants of a planted family share code, so the 12 originals are the "
        "independent items; the renamed copies are a control, not extra evidence.",
        "- Intervals resample items, so with 12 items a 95% interval is about ±25 points wide "
        "either side near 50%.",
    ]
    if v:
        n = v["a_found_p_missed"] + v["p_found_a_missed"]
        needs = smallest_split(n)
        verdict = (
            f"A split of {needs} or more lopsided would be significant at p < 0.05."
            if needs[0].isdigit()
            else f"{n} disagreements are too few for any split to be significant."
        )
        lines.append(
            f"- On the planted originals the arms disagree on {n} of {v['items']} items "
            f"(A only {v['a_found_p_missed']}, P only {v['p_found_a_missed']}, sign test "
            f"p = {v['sign_test_p']}). {verdict}"
        )
    lines += [
        "- Incident results may be memorised and have 5 items at most: read them as anecdotes.",
        "- Draft labels are unreviewed; a flagged function that is not labelled counts as a false "
        "positive even when it may be a real second issue.",
    ]
    return lines


def write_report(report: dict, directory: Path = RESULTS_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"compare-{report['meta']['date']}"
    js, md = directory / f"{stem}.json", directory / f"{stem}.md"
    js.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    md.write_text("\n".join(render(report)) + "\n")
    return js, md


def today() -> str:
    return date.today().isoformat()


def results_row(report: dict) -> str:
    """The one row for evals/results.md (the convention: change, metrics, date)."""
    g, m = report["groups"], report["meta"]
    v, safe = g["planted_vulnerable"], g["planted_safe"]
    c = report["cost"]
    notes = (
        f"DRAFT labels (unreviewed), one run each at temperature 0; incidents may be memorised; "
        f"full report evals/source_review/results/compare-{m['date']}.md"
    )
    return (
        f"| {m['date']} | plain Opus 5.5 vs our source review (same model, stripped view + "
        f"checklist + span checks, no second pass) | {m['model']} | P, A | "
        f"{sum(x['n'] for x in g.values())} of {m['items_total']} | "
        f"{v['P'].get('found_items')}/{v['P'].get('labelled_items')} vs "
        f"{v['A'].get('found_items')}/{v['A'].get('labelled_items')} | "
        f"{safe['P'].get('with_medium_plus_flag')}/{safe['n']} vs "
        f"{safe['A'].get('with_medium_plus_flag')}/{safe['n']} | "
        f"${c['P']['total_usd']:.2f} / ${c['A']['total_usd']:.2f} | {notes} |"
    )
