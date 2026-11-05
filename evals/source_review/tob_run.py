"""Running the Trail of Bits eval: the stages, the their claims, and what the judge made of them.

Stages (evals/datasets/tob_items/README.md):

    screen     plain Opus (arm P) once per report                      -> defines the hard set
    fresh      plain Opus again                                        -> the baseline
    pipeline   `run_review` (arm A), second pass off                   -> the arm under test

A stage writes one checkpoint line per report to a folder OUTSIDE the repo (the lines hold the
reviewers' sentences, which are untrusted text and are never committed). `--resume` reads them and
does not run a finished report again. The judge then compares every flag of a run with every
labelled
finding of its report (`tob_judge`), and `verdicts` turns the judgements into found, mislabelled or
missed for each (run, finding).
"""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent.config import ModelSpec
from evals.judge.cache import JudgeCache
from evals.source_review import compare as cmp
from evals.source_review import tob_items as ti
from evals.source_review import tob_judge as tj
from evals.source_review.compare import Source
from evals.source_review.scoring import Flag
from review.client import RETRY_REMINDER, ReviewerCall, ReviewerClient
from review.pack import Prompt
from review.spans import strip_fence

STAGES = ("screen", "fresh", "pipeline")
ARM_OF = {"screen": "P", "fresh": "P", "pipeline": "A"}
MAX_SILENT = cmp.MAX_SILENT
OUTPUT_LIMIT = {"P": 8192, "A": 8192}  # output tokens a request may write, per arm
PIPELINES = ("single", "two_pass")  # how arm A reads: the pre-registered pass, or leads then checks
# Two requests per chunk: the estimate of arm A is scaled by this. One measurement (DeepSeek, the
# smallest report, one retry included): 2.9 times the single pass. The real cost is whatever
# OpenRouter reports.
PASS_FACTOR = {"single": 1.0, "two_pass": 3.0}
RUNS = "runs"  # the folder of the pre-registered runs; another name keeps a re-run apart from them
RUNS_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


# ---- checkpoints --------------------------------------------------------------------------------


def checkpoint_path(cache: Path, side: str, stage: str, runs: str = RUNS) -> Path:
    return cache / runs / f"{side}-{stage}.jsonl"


def read_stage_rows(cache: Path, side: str, stage: str, runs: str = RUNS) -> list[dict]:
    path = checkpoint_path(cache, side, stage, runs)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def read_stage(cache: Path, side: str, stage: str, runs: str = RUNS) -> dict[str, dict]:
    """The latest record of each report (a report run again replaces its earlier line)."""
    return {r["item"]: r for r in read_stage_rows(cache, side, stage, runs)}


def replaced_usd(cache: Path, side: str, stage: str, runs: str = RUNS) -> float:
    """Money paid for records that a later run replaced (replies cut off and run again)."""
    rows = read_stage_rows(cache, side, stage, runs)
    last = {r["item"]: i for i, r in enumerate(rows)}
    return round(sum(r.get("cost_usd", 0.0) for i, r in enumerate(rows) if last[r["item"]] != i), 4)


def finished(record: dict) -> bool:
    return bool(record.get("complete"))


def cut_off(record: dict) -> bool:
    """The reply hit the output limit, so by the fixed rule the report has no findings from it."""
    return any("cut off" in f for f in record.get("failures", []))


def superseded(record: dict, max_tokens: int) -> bool:
    """A record that was cut off at a lower output limit than the one now asked for: it is run
    again (the old line stays in the file; the later one wins when the file is read)."""
    return cut_off(record) and record.get("max_tokens", OUTPUT_LIMIT["P"]) < max_tokens


def strip_notes(record: dict) -> dict:
    """The record as it may be committed: flags without the reviewers' sentences."""
    flags = [{k: v for k, v in f.items() if k != "note"} for f in record["flags"]]
    return {**record, "flags": flags}


# ---- what the model sent instead of the JSON ----------------------------------------------------


def reply_shape(text: str | None) -> str:
    """How a reply relates to the JSON object that was asked for: `json` (bare), `fenced` (one
    Markdown fence round it, which the pipeline now reads), `empty`, or `prose` (anything else)."""
    if text is None or not text.strip():
        return "empty"
    for body, shape in ((text.strip(), "json"), (strip_fence(text), "fenced")):
        try:
            if isinstance(json.loads(body), dict):
                return shape
        except (ValueError, RecursionError):
            pass
    return "prose"


class Recording:
    """A client that passes every request on and writes the raw text of each reply that is not a
    bare JSON object to a file OUTSIDE the repo (never committed): what the model sends instead of
    what was asked for. `begin` says which report the next requests belong to. A request is a
    `retry` when it carries the fixed reminder that `review.client` adds the second time."""

    def __init__(self, inner: ReviewerClient, path: Path, stage: str = "") -> None:
        self.inner, self.path, self.stage, self.item = inner, path, stage, ""
        self.name = inner.name

    def begin(self, item: str) -> None:
        self.item = item

    def review(self, prompt: Prompt, schema: dict, *, max_tokens: int) -> ReviewerCall:
        reply = self.inner.review(prompt, schema, max_tokens=max_tokens)
        shape = reply_shape(reply.text)
        if shape != "json":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "item": self.item, "stage": self.stage, "shape": shape,
                "retry": RETRY_REMINDER in prompt.user, "finish_reason": reply.finish_reason,
                "completion_tokens": reply.completion_tokens, "text": reply.text,
            }  # fmt: skip
            with self.path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        return reply


# ---- running a stage ---------------------------------------------------------------------------


def stage_estimate_usd(
    source: Source,
    stage: str,
    slug: str = "x",
    spec: ModelSpec = cmp.OPUS,
    pipeline: str = "single",
) -> float:
    """The central cost of one report in one stage, from the real prompts (no model)."""
    item = type("Item", (), {"id": slug, "group": "tob"})()
    est = cmp.estimate_item(item, source)  # type: ignore[arg-type]
    if est is None:
        return 0.0
    if ARM_OF[stage] == "P":
        return cmp.call_usd(est.p_tokens, est.p_calls * cmp.OUT_CENTRAL, spec)
    return PASS_FACTOR[pipeline] * cmp.call_usd(est.a_tokens, est.a_calls * cmp.OUT_CENTRAL, spec)


def run_stage(
    reports: Sequence[ti.Report],
    sources: dict[str, Source],
    stage: str,
    client: ReviewerClient,
    *,
    spent: Callable[[], float],
    max_spend: float,
    done: dict[str, dict],
    checkpoint: Path,
    max_tokens: int | None = None,
    out: Callable[[str], None] = print,
    begin: Callable[[str], None] | None = None,
    spec: ModelSpec = cmp.OPUS,
    pipeline: str = "single",
    reasoning: str = "",
) -> list[dict]:
    """One arm over the reports, in order. Stops when the next report's central estimate would pass
    `max_spend`, or after MAX_SILENT reports in a row where no request was answered. Every request
    is also guarded by the `Budget` that `client` is. `max_tokens` is the output limit of a request
    (default `OUTPUT_LIMIT` of the arm); a record cut off at a lower limit is run again. `begin`
    is called with the report's slug just before its first request."""
    limit = max_tokens or OUTPUT_LIMIT[ARM_OF[stage]]
    records: list[dict] = []
    stopped = ""
    silent = 0
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    for r in reports:
        if r.slug in done and finished(done[r.slug]) and not superseded(done[r.slug], limit):
            records.append(done[r.slug])
            continue
        source = sources[r.slug]
        if stopped:
            records.append({"item": r.slug, "stage": stage, "complete": False, "skipped": stopped})
            out(f"  {r.slug}: NOT RUN ({stopped})")
            continue
        if spent() + stage_estimate_usd(source, stage, r.slug, spec, pipeline) > max_spend:
            stopped = "spend cap"
            records.append({"item": r.slug, "stage": stage, "complete": False, "skipped": stopped})
            out(f"  {r.slug}: NOT RUN ({stopped})")
            continue
        if begin is not None:
            begin(r.slug)
        if ARM_OF[stage] == "P":
            rec = cmp.run_arm_p(source, client, limit)
            mode: dict[str, str] = {}
        else:
            rec = cmp.run_arm_a(source, client, limit, pipeline)
            mode = {"pipeline_mode": pipeline}
        record = {
            "item": r.slug, "stage": stage, "max_tokens": limit, "model": client.name,
            "reasoning": reasoning or "default", **mode, **asdict(rec),
        }  # fmt: skip
        records.append(record)
        with checkpoint.open("a") as f:
            f.write(json.dumps(record) + "\n")
        silent = silent + 1 if not rec.complete else 0
        if silent >= MAX_SILENT:
            stopped = "model not answering"
            out(f"  stopping: no request was answered on {MAX_SILENT} reports in a row")
        out(
            f"  {r.slug}: {stage} {len(rec.flags)} flags ${rec.cost_usd:.3f} {rec.seconds:.0f}s"
            f"{'' if rec.complete else ' INCOMPLETE'} | spent ${spent():.3f}"
        )
    return records


# ---- the claims a run made ---------------------------------------------------------------------


def claims_of(record: dict, source: Source) -> list[tj.Claim]:
    """The record's flags as the judge sees them: a place and a sentence. Flags with the same place
    and sentence are one claim. File ids (arm A's `F3`) become the path both arms were shown."""
    paths = {f"F{i}": path for i, (path, _) in enumerate(source.files, 1)}
    seen: dict[tj.Claim, None] = {}
    for d in record.get("flags", []):
        flag = Flag(**d)
        claim = tj.Claim(
            file=paths.get(flag.file_id, flag.file_id),
            contract=flag.contract or "",
            function=flag.function or "",
            start_line=flag.start_line,
            end_line=flag.end_line,
            note=flag.note,
        )
        seen.setdefault(claim, None)
    return list(seen)


def descriptions(cache: Path, reports: Sequence[ti.Report]) -> dict[str, str]:
    """label id -> the auditor's Description text, read from the PDF now (third-party text that is
    never written to the repo)."""
    out: dict[str, str] = {}
    for r in reports:
        parsed = ti.read_pdf(cache / "pdfs" / r.file)
        if parsed.report is None:
            continue
        for raw in parsed.report.findings:
            out[f"{r.slug}:{raw.finding_id or f'F{raw.number:02d}'}"] = raw.description
    return out


def references(labels: Sequence[ti.Label], desc: dict[str, str]) -> dict[str, tj.Reference]:
    return {
        lab.id: tj.Reference(lab.id, lab.title, lab.files, desc.get(lab.id, "")) for lab in labels
    }


# ---- the judge over the flags ------------------------------------------------------------------


def judge_jobs(
    rubric: tj.Rubric,
    model: str,
    refs: dict[str, tj.Reference],
    labels_of: dict[str, list[ti.Label]],
    records: dict[str, dict],
    sources: dict[str, Source],
    stage: str,
) -> list[tj.PairJob]:
    """Every flag of every report's run against every labelled finding of that report."""
    jobs = []
    for slug, record in records.items():
        if not finished(record) or slug not in sources:
            continue
        for claim in claims_of(record, sources[slug]):
            for lab in labels_of.get(slug, []):
                if lab.file_scoped and claim.file not in lab.files:
                    continue  # the wide key's extra findings: only flags in their own files
                meta = {"report": slug, "label": lab.id, "stage": stage}
                jobs.append(tj.make_job(rubric, refs[lab.id], claim, model, meta))
    return jobs


@dataclass
class LabelVerdict:
    """What one run made of one labelled finding, from the judge's pair verdicts."""

    found: bool = False  # some flag is `same` by a majority of the judges
    found_unanimous: bool = False  # some flag is `same` for every judge
    found_lenient: bool = False  # some flag is `same` for at least one judge
    mislabelled: bool = False  # not found, and some flag is `same_place`
    unjudged: int = 0  # pairs with no usable verdict from every judge
    disputed: int = 0
    matches: list[int] = field(default_factory=list)  # indices of claims judged `same`

    @property
    def kind(self) -> str:
        if self.found:
            return "found"  # one `same` pair settles it, whatever else is unjudged
        if self.unjudged:
            return "unjudged"
        return "mislabelled" if self.mislabelled else "missed"


def verdicts(
    rubric: tj.Rubric,
    judges: Sequence[str],
    refs: dict[str, tj.Reference],
    labels_of: dict[str, list[ti.Label]],
    records: dict[str, dict],
    sources: dict[str, Source],
    cache: JudgeCache,
) -> dict[tuple[str, str], LabelVerdict]:
    """(report, label id) -> LabelVerdict for one run's records, from the cached judgements."""
    out: dict[tuple[str, str], LabelVerdict] = {}
    for slug, record in records.items():
        if not finished(record) or slug not in sources:
            continue
        claims = claims_of(record, sources[slug])
        for lab in labels_of.get(slug, []):
            v = LabelVerdict()
            for i, claim in enumerate(claims):
                if lab.file_scoped and claim.file not in lab.files:
                    continue  # never judged, and not a gap: such a pair is out of scope
                got = []
                for judge in judges:
                    row = cache.get(tj.pair_key(refs[lab.id], claim, judge, rubric))
                    if row is not None and row["parsed"]:
                        got.append(row["verdict"])
                if len(got) < len(judges):
                    v.unjudged += 1
                    continue
                strict, loose = tj.combine(got), tj.lenient(got)
                if tj.unanimous(got):
                    v.found_unanimous = True
                v.disputed += strict == "disputed"
                if strict == "same":
                    v.found = True
                    v.matches.append(i)
                if loose == "same":
                    v.found_lenient = True
                if strict == "same_place":
                    v.mislabelled = True
            v.mislabelled = v.mislabelled and not v.found
            out[(slug, lab.id)] = v
    return out


def hard_labels(
    screen: dict[tuple[str, str], LabelVerdict],
) -> tuple[set[str], set[str]]:
    """(hard label ids, ids left out because a pair was unjudged): the findings plain missed or
    mislabelled in the screening run."""
    hard = {lab for (_, lab), v in screen.items() if v.kind in ("missed", "mislabelled")}
    unjudged = {lab for (_, lab), v in screen.items() if v.kind == "unjudged"}
    return hard, unjudged


def unmatched_flags(
    rubric: tj.Rubric,
    judges: Sequence[str],
    refs: dict[str, tj.Reference],
    labels_of: dict[str, list[ti.Label]],
    record: dict,
    source: Source,
    cache: JudgeCache,
) -> tuple[int, int]:
    """(flags, flags that match no labelled finding of the report). A flag with a disputed or
    missing verdict against some finding is not counted as unmatched. A lower bound on precision:
    real bugs the auditors did not list count as unmatched."""
    claims = claims_of(record, source)
    labs = labels_of.get(record["item"], [])
    unmatched = 0
    for claim in claims:
        clear = True
        for lab in labs:
            if lab.file_scoped and claim.file not in lab.files:
                continue  # out of scope for this finding: counts as unrelated
            got = []
            for judge in judges:
                row = cache.get(tj.pair_key(refs[lab.id], claim, judge, rubric))
                got.append(row["verdict"] if row is not None and row["parsed"] else "")
            if "" in got:
                clear = False
                break
            if tj.combine(got) != "unrelated":
                clear = False
                break
        unmatched += clear
    return len(claims), unmatched
