"""The judge for the Trail of Bits eval: is this flag the same bug as this audit finding?

One call judges one pair: a reference finding (title, files and the description read from the PDF at
run time) and one claim (where a flag points and the reviewer's sentence). The arm is not named, the
flag's severity and checklist entry are not shown, and nothing from the other claims is. Both
texts are
untrusted: they enter the prompt only inside fenced blocks (`evals.judge.rubric.fence`), and the
reply is read strictly, a verdict or a recorded refusal, never guessed.

Which judges may run is `evals/judge/policy.toml` (a judge is never from the company of the model it
grades). The rubric is `evals/datasets/tob_items/rubric.toml`; its hash covers the file and the
reply
schema, and a judged number may be quoted only if the hash equals the one Aryaan approved
(`rubric.approved`).

Verdicts of several judges are combined by `combine`: the rule is fixed in the README.
"""

import hashlib
import json
import math
import random
import re
import threading
import tomllib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.judge.cache import KEEP_RAW, JudgeCache, key_of
from evals.judge.client import JudgeClient, JudgeSpec, ModelUnavailable
from evals.judge.rubric import Prompt, boundary_code, clean_text, fence
from evals.judge.runner import Price, SpendCap
from evals.source_review.tob_items import DATASET

RUBRIC_PATH = DATASET / "rubric.toml"
APPROVED_PATH = DATASET / "rubric.approved"
VERDICTS = ("same", "same_place", "unrelated")
PAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}
MAX_STORED_REASON = 400
DESCRIPTION_CAP = 3_000  # characters of the auditor's description shown to the judge
NOTE_CAP = 400  # characters of the reviewer's sentence
CHARS_PER_TOKEN = 3  # the spend guard's pessimistic count


# ---- the rubric ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rubric:
    version: int
    system: str
    user: str
    sha256: str


def rubric_hash(path: Path = RUBRIC_PATH) -> str:
    digest = hashlib.sha256(path.read_bytes())
    digest.update(b"\0" + json.dumps(PAIR_SCHEMA, sort_keys=True).encode())
    return digest.hexdigest()


def load_rubric(path: Path = RUBRIC_PATH) -> Rubric:
    data = tomllib.loads(path.read_text())
    if tuple(data["verdicts"]) != VERDICTS:
        raise ValueError("rubric verdicts are not the verdicts the scorer counts")
    return Rubric(data["version"], data["system"], data["user"], rubric_hash(path))


def approved_hash(path: Path = APPROVED_PATH) -> str:
    return path.read_text().strip() if path.exists() else ""


def is_approved(rubric: Rubric, path: Path = APPROVED_PATH) -> bool:
    return bool(rubric.sha256) and approved_hash(path) == rubric.sha256


# ---- one pair ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """A labelled finding as the judge sees it. `description` comes from the PDF at run time."""

    id: str
    title: str
    files: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class Claim:
    """A flag as the judge sees it: where it points and the reviewer's sentence. No arm, no
    severity, no checklist entry."""

    file: str
    contract: str
    function: str
    start_line: int
    end_line: int
    note: str

    @property
    def sha(self) -> str:
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).hexdigest()


def _cut(text: str, limit: int) -> str:
    text = clean_text(text)
    return text if len(text) <= limit else text[:limit] + " [cut]"


def pair_prompt(rubric: Rubric, ref: Reference, claim: Claim) -> Prompt:
    description = _cut(ref.description, DESCRIPTION_CAP) or "(no description was read)"
    reference = (
        f"Title: {_cut(ref.title, 300)}\nFiles: {', '.join(_cut(f, 200) for f in ref.files)}\n"
        f"Description:\n{description}"
    )
    place = ".".join(x for x in (claim.contract, claim.function) if x) or "(no function)"
    where = (
        f"Points at: {_cut(claim.file, 200)}, {_cut(place, 200)}, "
        f"lines {claim.start_line} to {claim.end_line}"
    )
    claim_text = f"{where}\nReviewer's sentence: {_cut(claim.note, NOTE_CAP) or '(none)'}"
    code = boundary_code([reference, claim_text])
    user = rubric.user.format(
        reference_block=fence("reference", code, reference),
        claim_block=fence("claim", code, claim_text),
    )
    return Prompt(rubric.system, user)


@dataclass(frozen=True)
class Parsed:
    verdict: str = ""
    reason: str = ""
    error: str | None = None


_FENCE = re.compile(r"^```(?:json|JSON)?[ \t]*\n(.*)\n```$", re.DOTALL)


def parse_pair(text: str | None, finish_reason: str | None = None) -> Parsed:
    """A verdict or a code saying why the reply was refused. Never guesses."""
    if finish_reason == "length":
        return Parsed(error="cut_off")
    if text is None or not text.strip():
        return Parsed(error="empty_reply")
    body = text.strip()
    if fenced := _FENCE.match(body):
        body = fenced.group(1).strip()
    try:
        value = json.loads(body)
    except ValueError:
        return Parsed(error="not_json")
    if not isinstance(value, dict):
        return Parsed(error="not_an_object")
    if set(value) != {"verdict", "reason"}:
        return Parsed(error="wrong_keys")
    if value["verdict"] not in VERDICTS:
        return Parsed(error="bad_verdict")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        return Parsed(error="bad_reason")
    return Parsed(value["verdict"], " ".join(value["reason"].split())[:MAX_STORED_REASON])


def pair_key(ref: Reference, claim: Claim, model: str, rubric: Rubric, repeat: int = 0) -> str:
    ref_sha = hashlib.sha256(
        json.dumps([ref.id, ref.title, ref.files, ref.description]).encode()
    ).hexdigest()
    return key_of(["tob-pair", ref_sha, claim.sha, model, rubric.sha256, repeat])


# ---- combining judges --------------------------------------------------------------------------


def combine(verdicts: Sequence[str]) -> str:
    """One pair, several judges. `same` if all say `same`; `same_place` if all say `same` or
    `same_place` and at least one says `same_place`; `unrelated` if all say it; otherwise
    `disputed`. With one judge, that judge's verdict."""
    if not verdicts:
        return "unjudged"
    kinds = set(verdicts)
    if kinds == {"same"}:
        return "same"
    if kinds <= {"same", "same_place"}:
        return "same_place"
    if kinds == {"unrelated"}:
        return "unrelated"
    return "disputed"


def lenient(verdicts: Sequence[str]) -> str:
    """The sensitivity reading: `same` if any judge says so."""
    if "same" in verdicts:
        return "same"
    return combine(verdicts)


# ---- running the judge ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairJob:
    key: str
    prompt: Prompt
    model: str
    meta: dict[str, Any]


def make_job(
    rubric: Rubric, ref: Reference, claim: Claim, model: str, meta: dict[str, Any], repeat: int = 0
) -> PairJob:
    return PairJob(
        pair_key(ref, claim, model, rubric, repeat), pair_prompt(rubric, ref, claim), model, meta
    )


@dataclass
class JudgeRun:
    jobs: int = 0
    cached: int = 0
    called: int = 0
    unparsed: int = 0
    left: int = 0
    stopped: str | None = None
    spent_usd: float = 0.0
    refused: dict[str, int] = field(default_factory=dict)


def worst_case(job: PairJob, price: Price, max_tokens: int) -> float:
    return price.cost(math.ceil(job.prompt.chars / CHARS_PER_TOKEN) + 50, max_tokens)


def run_pairs(
    jobs: Sequence[PairJob],
    client: JudgeClient,
    cache: JudgeCache,
    cap: SpendCap,
    *,
    spec: JudgeSpec,
    workers: int = 4,
    seed: int = 0,
    progress: Callable[[JudgeRun], None] | None = None,
) -> JudgeRun:
    """Judge the pairs not in the cache, in a shuffled order, stopping cleanly at the cap."""
    price = Price(spec.input_per_mtok, spec.output_per_mtok)
    summary = JudgeRun(jobs=len(jobs))
    unique: dict[str, PairJob] = {}
    for job in jobs:
        unique.setdefault(job.key, job)
    todo = []
    for job in unique.values():
        row = cache.get(job.key)
        if row is not None and row["parsed"]:
            summary.cached += 1
        else:
            todo.append(job)
    random.Random(seed).shuffle(todo)
    lock = threading.Lock()
    stop = threading.Event()

    def one(job: PairJob) -> None:
        if stop.is_set():
            with lock:
                summary.left += 1
            return
        reserved = worst_case(job, price, spec.max_tokens)
        if not cap.reserve(reserved):
            stop.set()
            with lock:
                summary.stopped = summary.stopped or "spend_cap"
                summary.left += 1
            return
        try:
            reply = client.judge(job.prompt, PAIR_SCHEMA, max_tokens=spec.max_tokens)
        except ModelUnavailable as err:
            cap.settle(reserved, 0.0)
            stop.set()
            with lock:
                summary.stopped = summary.stopped or f"unavailable: {err}"[:240]
                summary.left += 1
            return
        cost = reply.cost_usd or price.cost(reply.prompt_tokens, reply.completion_tokens)
        cap.settle(reserved, cost)
        parsed = parse_pair(reply.text, reply.finish_reason)
        cache.put(
            {
                "key": job.key,
                "model": job.model,
                **job.meta,
                "parsed": parsed.error is None,
                "error": parsed.error,
                "verdict": parsed.verdict,
                "reason": parsed.reason,
                "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.completion_tokens,
                "cost_usd": round(cost, 8),
                "finish_reason": reply.finish_reason,
                "raw": (reply.text or "")[:KEEP_RAW] if parsed.error else "",
            }
        )
        with lock:
            summary.called += 1
            if parsed.error:
                summary.unparsed += 1
                summary.refused[parsed.error] = summary.refused.get(parsed.error, 0) + 1
            summary.spent_usd = cap.spent
            if progress:
                progress(summary)

    if workers <= 1:
        for job in todo:
            one(job)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, todo))
    summary.spent_usd = cap.spent
    return summary


def estimate_pairs(
    jobs: Sequence[PairJob], cache: JudgeCache, spec: JudgeSpec, typical_out: int = 150
) -> dict[str, Any]:
    """What judging these pairs would cost with one judge, before anything is sent."""
    price = Price(spec.input_per_mtok, spec.output_per_mtok)
    unique: dict[str, PairJob] = {}
    for job in jobs:
        unique.setdefault(job.key, job)
    todo = [j for j in unique.values() if (row := cache.get(j.key)) is None or not row["parsed"]]
    tokens = sum(math.ceil(j.prompt.chars / 3.7) + 30 for j in todo)
    return {
        "judge": spec.slug,
        "pairs": len(unique),
        "to_call": len(todo),
        "typical_usd": round(price.cost(tokens, typical_out * len(todo)), 4),
        "worst_usd": round(sum(worst_case(j, price, spec.max_tokens) for j in todo), 4),
    }
