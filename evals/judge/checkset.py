"""The check set: claims and summaries with known answers, used to check a judge.

`build` reads `checkset_spec.toml` and the dev traces it names and writes two committed files, so
that nothing downstream needs the traces:
- `checkset/items.jsonl`, one item per line: a claim (text, kind, severity, the records it cites)
  or a summary (sentences and the report's own items), with its answer key (fault or sound, which
  labels count as catching it, which sentences are scored).
- `checkset/evidence.json`, the evidence texts once each, by hash (many claims cite one record).

The faults are ours (see the spec's header). Every planted claim also has to pass the project's own
citation validator, so each one is a fault the validator accepts: the judge is not being asked
to find what the validator already finds.
"""

import hashlib
import json
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.report import FindingIn
from agent.validator import check_finding
from evals.citations import _registry as registry_of
from evals.judge.cache import JudgeCache, claim_key, summary_key
from evals.judge.compare import Scored, agreement, catch_and_false_alarm, stability
from evals.judge.inputs import (
    ClaimInput,
    Record,
    SummaryInput,
    records_for,
    split_sentences,
)
from evals.judge.rubric import JUDGE_DIR, Rubric
from evals.judge.runner import Job, claim_job, summary_job
from evals.runs import RunRecord, load_trace

SPEC_PATH = JUDGE_DIR / "checkset_spec.toml"
CHECKSET_DIR = JUDGE_DIR / "checkset"
ITEMS_PATH = CHECKSET_DIR / "items.jsonl"
EVIDENCE_PATH = CHECKSET_DIR / "evidence.json"

SUMMARY_CATCH = ("partly", "not_supported", "contradicted")


# ---- building ------------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class SpecError(ValueError):
    pass


def _apply(text: str, replace: list[list[str]], where: str) -> str:
    for old, new in replace:
        if old not in text:
            raise SpecError(f"{where}: {old[:60]!r} is not in the text")
        text = text.replace(old, new, 1)
    return text


def _joined(records: Sequence[Record]) -> str:
    return "\n".join(r.text for r in records)


def _check_tokens(where: str, text: str, present: list[str], absent: list[str]) -> None:
    for token in present:
        if token not in text:
            raise SpecError(f"{where}: {token!r} is not in the cited evidence")
    for token in absent:
        if token in text:
            raise SpecError(f"{where}: {token!r} is in the cited evidence but should not be")


def _validator_accepts(
    run: RunRecord, claim: str, kind: str, severity: str, ids: list[str]
) -> bool:
    finding = FindingIn(claim=claim, kind=kind, severity=severity, evidence_ids=list(ids))  # type: ignore[arg-type]
    return not check_finding(finding, 0, registry_of(run))


def _claim_row(
    item_id: str,
    run: RunRecord,
    finding: int,
    *,
    claim: str,
    ids: tuple[str, ...],
    fault: bool,
    origin: str,
    operator: str,
    catch: list[str],
    note: str,
    twin_of: str | None,
    evidence: dict[str, str],
) -> dict[str, Any]:
    base = run.findings[finding]
    records = records_for(run, ids)
    for record in records:
        evidence[_sha(record.text)] = record.text
    return {
        "id": item_id,
        "task": "claim",
        "fault": fault,
        "origin": origin,
        "operator": operator,
        "catch": catch if fault else [],
        "note": note,
        "twin_of": twin_of,
        "source": {
            "trace": Path(run.path or "").name,
            "model": run.model,
            "address": run.address,
            "block": run.block,
            "finding": finding,
        },
        "claim": claim,
        "kind": base.kind,
        "severity": base.severity,
        "records": [
            {"id": r.id, "source": r.source, "sha": _sha(r.text), "cut": r.cut} for r in records
        ],
        "validator_accepts": _validator_accepts(run, claim, base.kind, base.severity, list(ids)),
    }


def build(traces: Path, spec_path: Path = SPEC_PATH) -> tuple[list[dict[str, Any]], dict[str, str]]:
    spec = tomllib.loads(spec_path.read_text())
    runs: dict[str, RunRecord] = {}

    def run_of(name: str) -> RunRecord:
        if name not in runs:
            runs[name] = load_trace(traces / name)
        return runs[name]

    rows: list[dict[str, Any]] = []
    evidence: dict[str, str] = {}
    twins: dict[tuple[str, int], str] = {}

    def add(row: dict[str, Any]) -> None:
        if not row["validator_accepts"]:
            raise SpecError(f"{row['id']}: the citation validator rejects this claim")
        rows.append(row)

    for entry in spec.get("planted", []):
        run = run_of(entry["trace"])
        index = entry["finding"]
        base = run.findings[index]
        ids = tuple(entry.get("evidence") or base.evidence_ids)
        claim = _apply(base.claim, entry.get("replace", []), entry["id"]) + entry.get("append", "")
        if claim == base.claim and ids == base.evidence_ids:
            raise SpecError(f"{entry['id']}: nothing was changed")
        twin_text = _joined(records_for(run, base.evidence_ids))
        _check_tokens(f"{entry['id']} (twin)", twin_text, entry.get("twin_tokens", []), [])
        fault_text = _joined(records_for(run, ids))
        _check_tokens(f"{entry['id']} (fault)", fault_text, [], entry.get("absent_tokens", []))
        twin_id = None
        if entry.get("twin", True):
            key = (entry["trace"], index)
            if key not in twins:
                twins[key] = f"twin:{entry['id']}"
                add(
                    _claim_row(
                        twins[key],
                        run,
                        index,
                        claim=base.claim,
                        ids=base.evidence_ids,
                        fault=False,
                        origin="sound",
                        operator=entry["operator"],
                        catch=[],
                        note=f"the unedited claim behind {entry['id']}",
                        twin_of=None,
                        evidence=evidence,
                    )
                )
            twin_id = twins[key]
        add(
            _claim_row(
                entry["id"],
                run,
                index,
                claim=claim,
                ids=ids,
                fault=True,
                origin="planted",
                operator=entry["operator"],
                catch=entry["catch"],
                note=entry.get("note", ""),
                twin_of=twin_id,
                evidence=evidence,
            )
        )

    for entry in spec.get("sound", []):
        run = run_of(entry["trace"])
        base = run.findings[entry["finding"]]
        _check_tokens(
            entry["id"], _joined(records_for(run, base.evidence_ids)), entry.get("tokens", []), []
        )
        add(
            _claim_row(
                entry["id"],
                run,
                entry["finding"],
                claim=base.claim,
                ids=base.evidence_ids,
                fault=False,
                origin="sound",
                operator="",
                catch=[],
                note=entry.get("note", ""),
                twin_of=None,
                evidence=evidence,
            )
        )

    for entry in spec.get("real", []):
        run = run_of(entry["trace"])
        base = run.findings[entry["finding"]]
        _check_tokens(
            entry["id"],
            _joined(records_for(run, base.evidence_ids)),
            [],
            entry.get("absent_tokens", []),
        )
        add(
            _claim_row(
                entry["id"],
                run,
                entry["finding"],
                claim=base.claim,
                ids=base.evidence_ids,
                fault=True,
                origin="real",
                operator="real",
                catch=entry["catch"],
                note=entry.get("note", ""),
                twin_of=None,
                evidence=evidence,
            )
        )

    for entry in spec.get("summary", []):
        run = run_of(entry["trace"])
        text = _apply(run.summary, entry.get("replace", []), entry["id"])
        sentences = split_sentences(text)
        for position, new in sorted(entry.get("insert", [])):
            if not 1 <= position <= len(sentences) + 1:
                raise SpecError(f"{entry['id']}: cannot insert at {position}")
            sentences.insert(position - 1, new)
        roles = {str(n): "fault" for n in entry.get("fault", [])}
        roles.update({str(n): "sound" for n in entry.get("sound", [])})
        if any(not 1 <= int(n) <= len(sentences) for n in roles):
            raise SpecError(f"{entry['id']}: a scored sentence number is out of range")
        rows.append(
            {
                "id": entry["id"],
                "task": "summary",
                "origin": entry["origin"],
                "operator": "real"
                if entry["origin"] == "real"
                else ("replace" if "replace" in entry else "insert" if "insert" in entry else ""),
                "note": entry.get("note", ""),
                "source": {
                    "trace": Path(run.path or "").name,
                    "model": run.model,
                    "address": run.address,
                    "block": run.block,
                },
                "sentences": sentences,
                "findings": [f.claim for f in run.findings],
                "checked": list(run.checked),
                "not_checked": list(run.not_checked),
                "roles": roles,
            }
        )
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise SpecError(f"duplicate item ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    return rows, evidence


def write(rows: list[dict[str, Any]], evidence: dict[str, str]) -> None:
    CHECKSET_DIR.mkdir(parents=True, exist_ok=True)
    ITEMS_PATH.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    EVIDENCE_PATH.write_text(json.dumps(dict(sorted(evidence.items())), indent=0) + "\n")


# ---- reading ------------------------------------------------------------


@dataclass(frozen=True)
class CheckItem:
    row: dict[str, Any]
    claim: ClaimInput | None
    summary: SummaryInput | None

    @property
    def id(self) -> str:
        return self.row["id"]

    @property
    def task(self) -> str:
        return self.row["task"]

    def scored(self) -> list[tuple[str, bool]]:
        """(unit id, is a fault) for everything this item scores: the claim itself, or each
        scored summary sentence."""
        if self.task == "claim":
            return [(self.id, bool(self.row["fault"]))]
        return [
            (f"{self.id}#{n}", role == "fault")
            for n, role in sorted(self.row["roles"].items(), key=lambda kv: int(kv[0]))
        ]


def load_items(
    items_path: Path = ITEMS_PATH, evidence_path: Path = EVIDENCE_PATH
) -> list[CheckItem]:
    evidence = json.loads(evidence_path.read_text())
    out = []
    for line in items_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["task"] == "claim":
            records = tuple(
                Record(r["id"], r["source"], evidence[r["sha"]], r["cut"]) for r in row["records"]
            )
            out.append(
                CheckItem(
                    row, ClaimInput(row["claim"], row["kind"], row["severity"], records), None
                )
            )
        else:
            out.append(
                CheckItem(
                    row,
                    None,
                    SummaryInput(
                        tuple(row["sentences"]),
                        tuple(row["findings"]),
                        tuple(row["checked"]),
                        tuple(row["not_checked"]),
                    ),
                )
            )
    return out


def jobs_for(items: Sequence[CheckItem], rubric: Rubric, model: str, repeat: int = 0) -> list[Job]:
    jobs = []
    for item in items:
        if item.claim is not None:
            jobs.append(claim_job(item.claim, rubric, model, repeat))
        elif item.summary is not None:
            jobs.append(summary_job(item.summary, rubric, model, repeat))
    return jobs


# ---- scoring ------------------------------------------------------------


def labels_for(
    items: Sequence[CheckItem], cache: JudgeCache, model: str, rubric: str, repeat: int = 0
) -> dict[str, str | None]:
    """unit id -> the judge's label, None where there is no usable judgement."""
    out: dict[str, str | None] = {}
    for item in items:
        if item.claim is not None:
            c = item.claim
            row = cache.get(
                claim_key(c.claim_sha, c.evidence_sha, c.kind, c.severity, model, rubric, repeat)
            )
            out[item.id] = row["verdicts"][0]["label"] if row and row["parsed"] else None
        elif item.summary is not None:
            s = item.summary
            row = cache.get(summary_key(s.summary_sha, s.context_sha, model, rubric, repeat))
            for unit, _ in item.scored():
                n = int(unit.split("#")[1])
                out[unit] = row["verdicts"][n - 1]["label"] if row and row["parsed"] else None
    return out


def scored_units(items: Sequence[CheckItem], labels: dict[str, str | None]) -> list[Scored]:
    units = []
    for item in items:
        for unit, fault in item.scored():
            if item.task == "claim":
                catch = tuple(item.row["catch"])
                operator = item.row["operator"] or "-"
            else:
                catch = SUMMARY_CATCH
                operator = f"summary-{item.row['operator'] or 'original'}"
            units.append(Scored(unit, fault, item.row["origin"], operator, catch, labels.get(unit)))
    return units


def evaluate(
    items: Sequence[CheckItem],
    cache: JudgeCache,
    models: Sequence[str],
    rubric: str,
    *,
    stability_models: Sequence[str] = (),
) -> dict[str, Any]:
    """Catch and false-alarm rates for each judge (claims and summary sentences apart), the
    agreement of two judges with every disagreement listed, and, for judges that were asked twice,
    how many labels changed."""
    claims = [i for i in items if i.task == "claim"]
    summaries = [i for i in items if i.task == "summary"]
    report: dict[str, Any] = {"rubric_sha256": rubric, "judges": list(models), "per_judge": {}}
    all_labels: dict[str, dict[str, str | None]] = {}
    for model in models:
        labels = labels_for(items, cache, model, rubric)
        all_labels[model] = labels
        report["per_judge"][model] = {
            "claims": catch_and_false_alarm(scored_units(claims, labels)),
            "summary_sentences": catch_and_false_alarm(scored_units(summaries, labels)),
            "unlabelled": sum(1 for v in labels.values() if v is None),
            "cost": _cost(cache, model, rubric),
        }
    if len(models) == 2:
        report["agreement"] = agreement(all_labels[models[0]], all_labels[models[1]])
    for model in stability_models:
        again = labels_for(items, cache, model, rubric, repeat=1)
        report.setdefault("stability", {})[model] = stability(
            all_labels.get(model) or labels_for(items, cache, model, rubric), again
        )
    return report


def _cost(cache: JudgeCache, model: str, rubric: str) -> dict[str, Any]:
    rows = [r for r in cache.rows() if r["model"] == model and r["rubric"] == rubric]
    return {
        "calls": len(rows),
        "usd": round(sum(r["cost_usd"] for r in rows), 6),
        "refused_replies": sum(1 for r in rows if not r["parsed"]),
        "quotes_not_found": sum(1 for r in rows for q in r.get("quotes_found", []) if q is False),
    }
