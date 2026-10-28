"""Run-sets: a folder of agent traces plus a manifest, and the small summary kept for milestones.

    evals/runsets/<name>/manifest.json     what was run, with what, at which commit (git-ignored)
    evals/runsets/<name>/traces/*.json     one trace per run (git-ignored)
    evals/summaries/<name>.json            the committed summary of a milestone dev run-set
    evals/results/<date>-<commit>-<name>.json   the versioned results file (committed with it)

The summary keeps what the free scoring reads from each run (verdict, report text, tool calls with
their arguments and seconds, attempts, cost, tokens, labels the judge gave) and leaves out the
conversation and the evidence, so scoring can be done again, and checked in CI, without a model, a
trace or a cache. `check_summary` scores a summary again and compares it with the committed results
file: a scoring change that moves numbers silently fails there.

A held-out run-set never gets a summary and its manifest names no contract, address or block: only
how many, and a hash of them. (Nobody tunes against a score they cannot see per item, and the same
goes for the files that describe it.)
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.cards import Card, CardSet
from evals.runs import Attempt, Call, Finding, RunRecord, load_runs
from evals.score import score

EVALS_DIR = Path(__file__).resolve().parent
RUNSETS_DIR = EVALS_DIR / "runsets"  # git-ignored
SUMMARIES_DIR = EVALS_DIR / "summaries"
RESULTS_DIR = EVALS_DIR / "results"
LABEL_RULES_HASH = EVALS_DIR / "datasets" / "label_rules.sha256"
SCHEMA_VERSION = 1


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


# ---- the manifest ------------------------------------------------------------


def describe_dataset(cards_path: Path, cards: CardSet, kind: str) -> dict[str, Any]:
    """What the labels were: a hash of the cards file and of the label rules, how many contracts,
    and a hash of their block pins. A dev run-set also lists the pins; a held-out one does not."""
    rules = LABEL_RULES_HASH.read_text().strip() if LABEL_RULES_HASH.exists() else None
    pins = {c.id: c.block for c in cards}
    out: dict[str, Any] = {
        "dataset_sha256": sha256(cards_path.read_bytes() + b"\0" + (rules or "").encode()),
        "label_rules_sha256": rules,
        "contracts": len(cards),
        "block_pins_sha256": sha256(json.dumps(sorted(pins.items()))),
    }
    if kind == "dev":
        out["block_pins"] = pins
        out["cards_file"] = cards_path.name
    return out


def new_manifest(
    *,
    name: str,
    kind: str,
    model_tier: str,
    model_slug: str,
    repeats: int,
    dataset: dict[str, Any],
    judges: tuple[str, ...],
    rubric_sha256: str | None,
    environment: dict[str, Any],
    models_toml_sha: str | None,
    estimated_usd: float,
    max_spend_usd: float,
    declared: str | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "kind": kind,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "planned",
        "stopped_because": None,
        "git_commit": environment.get("git_commit"),
        "git_dirty": environment.get("git_dirty"),
        "model": {"tier": model_tier, "slug": model_slug},
        "hashes": {
            "models_toml_sha": models_toml_sha,
            "system_prompt_sha": None,  # filled from the first trace
            "tools_sha": None,
            **{k: v for k, v in dataset.items() if k.endswith("sha256")},
        },
        "dataset": dataset,
        "repeats": repeats,
        "planned_runs": dataset["contracts"] * repeats,
        "completed_runs": 0,
        "concurrent": workers > 1,
        "judges": {"slugs": list(judges), "rubric_sha256": rubric_sha256 if judges else None},
        "spend": {
            "estimated_usd": round(estimated_usd, 6),
            "max_usd": max_spend_usd,
            "spent_usd": 0.0,
        },
        "declared": declared,
        "environment": environment,
    }


@dataclass
class RunSet:
    path: Path
    manifest: dict[str, Any]

    @property
    def name(self) -> str:
        return self.manifest["name"]

    @property
    def kind(self) -> str:
        return self.manifest["kind"]

    @property
    def traces(self) -> Path:
        return self.path / "traces"

    def save(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "manifest.json").write_text(json.dumps(self.manifest, indent=1) + "\n")

    def trace_files(self) -> list[Path]:
        return sorted(self.traces.glob("*.json")) if self.traces.exists() else []


def unique_name(root: Path, wanted: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", wanted).strip("-") or "runset"
    name, number = safe, 1
    while (root / name).exists():
        number += 1
        name = f"{safe}-{number}"
    return name


def create_runset(root: Path, manifest: dict[str, Any]) -> RunSet:
    runset = RunSet(root / manifest["name"], manifest)
    runset.traces.mkdir(parents=True, exist_ok=True)
    runset.save()
    return runset


def load_runset(path: Path) -> RunSet:
    try:
        return RunSet(path, json.loads((path / "manifest.json").read_text()))
    except (OSError, ValueError) as err:
        raise ValueError(f"{path} is not a run-set ({type(err).__name__})") from None


def list_runsets(root: Path, kind: str | None = None) -> list[RunSet]:
    found = []
    for manifest in sorted(root.glob("*/manifest.json")) if root.exists() else []:
        try:
            runset = load_runset(manifest.parent)
        except ValueError:
            continue
        if kind is None or runset.kind == kind:
            found.append(runset)
    return sorted(found, key=lambda r: (r.manifest["created_at"], r.name))


def latest_runset(root: Path, kind: str) -> RunSet | None:
    """The newest run-set of this kind that has at least one trace."""
    having = [r for r in list_runsets(root, kind) if r.trace_files()]
    return having[-1] if having else None


def read_runs(runset: RunSet) -> tuple[list[RunRecord], list[str]]:
    return load_runs([runset.traces])


# ---- the summary ------------------------------------------------------------


def run_to_dict(run: RunRecord) -> dict[str, Any]:
    """The part of a run the free scoring reads. Attempts are kept as the scorer sees them (rebuilt
    from the evidence for an old trace); the conversation, the evidence and the judge's inputs are
    left out."""
    from evals.citations import attempts_of

    return {
        "trace": Path(run.path).name if run.path else None,
        "chain": run.chain,
        "address": run.address,
        "block": run.block,
        "model": run.model,
        "verdict": run.verdict,
        "summary": run.summary,
        "findings": [[f.claim, f.kind, f.severity, list(f.evidence_ids)] for f in run.findings],
        "checked": list(run.checked),
        "not_checked": list(run.not_checked),
        "unavailable": list(run.unavailable),
        "stopped_because": run.stopped_because,
        "accepted": run.accepted,
        "attempt_count": run.attempt_count,
        "turns": run.turns,
        "tool_call_count": run.tool_call_count,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "cached_tokens": run.cached_tokens,
        "cost_usd": run.cost_usd,
        "seconds": run.seconds,
        "max_tool_calls": run.max_tool_calls,
        "calls": [call.__dict__ for call in run.calls],
        "attempts": [
            {**a.__dict__, "problems": [list(p) for p in a.problems]} for a in attempts_of(run)
        ],
        "turn_records": list(run.turn_records),
        "metrics": run.metrics,
        "environment": run.environment,
        "tags": run.tags,
    }


def run_from_dict(d: dict[str, Any]) -> RunRecord:
    return RunRecord(
        path=d["trace"],
        chain=d["chain"],
        address=d["address"],
        block=d["block"],
        model=d["model"],
        verdict=d["verdict"],
        summary=d["summary"],
        findings=tuple(Finding(c, k, s, tuple(ids)) for c, k, s, ids in d["findings"]),
        checked=tuple(d["checked"]),
        not_checked=tuple(d["not_checked"]),
        unavailable=tuple(d["unavailable"]),
        stopped_because=d["stopped_because"],
        accepted=d["accepted"],
        attempt_count=d["attempt_count"],
        turns=d["turns"],
        tool_call_count=d["tool_call_count"],
        prompt_tokens=d["prompt_tokens"],
        completion_tokens=d["completion_tokens"],
        cached_tokens=d["cached_tokens"],
        cost_usd=d["cost_usd"],
        seconds=d["seconds"],
        max_tool_calls=d["max_tool_calls"],
        calls=tuple(Call(**c) for c in d["calls"]),
        attempts=tuple(
            Attempt(**{**a, "problems": tuple(tuple(p) for p in a["problems"])})
            for a in d["attempts"]
        ),
        submissions=(),
        evidence=(),
        turn_records=tuple(d["turn_records"]),
        metrics=d["metrics"],
        environment=d["environment"],
        tags=d["tags"],
    )


def results_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """The part of a run-set manifest copied into a results file: what was run, not where."""
    keep = (
        "name",
        "kind",
        "model",
        "repeats",
        "hashes",
        "judges",
        "planned_runs",
        "completed_runs",
    )
    return {k: manifest[k] for k in keep if k in manifest} | {
        "spend_usd": manifest["spend"]["spent_usd"],
        "concurrent": manifest.get("concurrent", False),
    }


def build_summary(
    manifest: dict[str, Any],
    runs: list[RunRecord],
    judge_labels: dict[str, Any] | None,
    results_file: str | None,
) -> dict[str, Any]:
    if manifest["kind"] != "dev":
        raise ValueError("only a dev run-set gets a summary: held-out has no per-run detail")
    return {
        "schema_version": SCHEMA_VERSION,
        "what": "Summary of a dev run-set, enough to score it again without a model or the traces.",
        "manifest": manifest,
        "results_file": results_file,
        "judge_labels": judge_labels,
        "runs": [run_to_dict(r) for r in runs],
    }


def write_summary(summary: dict[str, Any], directory: Path = SUMMARIES_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{summary['manifest']['name']}.json"
    path.write_text(json.dumps(summary, indent=1) + "\n")
    return path


def load_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def rescore(summary: dict[str, Any], cards: CardSet, *, resamples: int = 10_000) -> dict[str, Any]:
    """The results document for a summary: the same call a fresh scoring makes, on records rebuilt
    from the summary and the labels it kept."""
    manifest = summary["manifest"]
    runs = [run_from_dict(d) for d in summary["runs"]]
    return score(
        runs,
        cards,
        split="dev",
        label=manifest["name"],
        resamples=resamples,
        judge_labels=summary.get("judge_labels"),
        runset=results_manifest(manifest),
    )


def differences(
    fresh: Any, committed: Any, where: str = "", ignore: tuple[str, ...] = ("created_at",)
) -> list[str]:
    """Where two results documents differ (keys in `ignore`, such as the time of writing, do not
    count), as short paths. Numbers are compared exactly: scoring is deterministic."""
    if isinstance(fresh, dict) and isinstance(committed, dict):
        out = []
        for key in sorted(set(fresh) | set(committed)):
            if key in ignore:
                continue
            if key not in fresh or key not in committed:
                out.append(f"{where}/{key}: present in only one")
            else:
                out += differences(fresh[key], committed[key], f"{where}/{key}", ignore)
        return out
    if isinstance(fresh, list) and isinstance(committed, list):
        if len(fresh) != len(committed):
            return [f"{where}: {len(fresh)} items against {len(committed)}"]
        return [
            d
            for i, (a, b) in enumerate(zip(fresh, committed, strict=True))
            for d in differences(a, b, f"{where}[{i}]", ignore)
        ]
    return [] if fresh == committed else [f"{where}: {fresh!r} against {committed!r}"]


def check_summary(
    path: Path,
    load_cards_for: Callable[[dict[str, Any]], CardSet],
    results_dir: Path = RESULTS_DIR,
    *,
    resamples: int = 10_000,
) -> list[str]:
    """Problems with one committed summary: empty if scoring it again gives exactly the committed
    results file. `load_cards_for(manifest)` returns the cards, and refuses if they are not the
    cards the run-set was scored against."""
    summary = load_summary(path)
    problems = []
    name = summary.get("results_file")
    if not name or not (results_dir / name).exists():
        return [f"{path.name}: the results file {name!r} is not committed"]
    try:
        cards = load_cards_for(summary["manifest"])
    except ValueError as err:
        return [f"{path.name}: {err}"]
    fresh = json.loads(json.dumps(rescore(summary, cards, resamples=resamples)))
    committed = json.loads((results_dir / name).read_text())
    problems += [f"{path.name}{d}" for d in differences(fresh, committed)]
    return problems


def dataset_matches(manifest: dict[str, Any], cards_path: Path, cards: CardSet) -> None:
    """Raise ValueError if these are not the cards and rules the run-set was made against."""
    now = describe_dataset(cards_path, cards, manifest["kind"])
    then = manifest["dataset"]
    if now["dataset_sha256"] != then["dataset_sha256"]:
        raise ValueError(
            "the cards or the label rules changed since this run-set was made "
            f"(dataset {then['dataset_sha256'][:12]} then, {now['dataset_sha256'][:12]} now)"
        )


def card_for_pin(card: Card) -> tuple[str, int]:
    if card.block is None:
        raise ValueError(f"card {card.id} has no block pin: a run-set needs every contract pinned")
    return card.address, card.block
