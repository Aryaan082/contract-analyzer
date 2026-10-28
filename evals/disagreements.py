"""Where two judges gave different labels to the same claim or summary sentence, with both reasons.

A milestone run is judged by two judges from two companies (evals/judge/policy.toml). Where they
differ is what Aryaan reviews later. The results file holds the ids and the two labels; this shows
the claim, what it cites and both judges' reasons, read from the judge cache. Dev run-sets only:
held-out has totals and no per-claim detail anywhere.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evals.judge.cache import JudgeCache, claim_key, summary_key
from evals.judge.faithfulness import run_key
from evals.judge.inputs import claim_inputs, judgeable, summary_input
from evals.runs import RunRecord


def _verdict(row: dict[str, Any] | None, index: int = 0) -> tuple[str | None, str]:
    if row is None or not row.get("parsed") or index >= len(row.get("verdicts") or []):
        return None, ""
    found = row["verdicts"][index]
    return found["label"], found["reason"]


def list_disagreements(
    runs: Sequence[RunRecord], cache: JudgeCache, judges: Sequence[str], rubric: str
) -> list[dict[str, Any]]:
    """Every claim and summary sentence both judges labelled and labelled differently."""
    if len(judges) != 2:
        raise ValueError("disagreements need exactly two judges")
    found: list[dict[str, Any]] = []
    for number, run in enumerate(runs):
        if not judgeable(run):
            continue
        where = run_key(run, number)
        for index, inp in enumerate(claim_inputs(run)):
            both = [
                _verdict(
                    cache.get(
                        claim_key(
                            inp.claim_sha, inp.evidence_sha, inp.kind, inp.severity, j, rubric
                        )
                    )
                )
                for j in judges
            ]
            if None not in (both[0][0], both[1][0]) and both[0][0] != both[1][0]:
                found.append(
                    {
                        "what": "claim",
                        "trace": where,
                        "number": index + 1,
                        "text": inp.claim,
                        "kind": inp.kind,
                        "severity": inp.severity,
                        "cites": [r.id for r in inp.records],
                        "labels": {
                            j: {"label": v[0], "reason": v[1]}
                            for j, v in zip(judges, both, strict=True)
                        },
                    }
                )
        sent = summary_input(run)
        if sent is None:
            continue
        rows = [
            cache.get(summary_key(sent.summary_sha, sent.context_sha, j, rubric)) for j in judges
        ]
        for index, text in enumerate(sent.sentences):
            both = [_verdict(row, index) for row in rows]
            if None not in (both[0][0], both[1][0]) and both[0][0] != both[1][0]:
                found.append(
                    {
                        "what": "summary sentence",
                        "trace": where,
                        "number": index + 1,
                        "text": text,
                        "labels": {
                            j: {"label": v[0], "reason": v[1]}
                            for j, v in zip(judges, both, strict=True)
                        },
                    }
                )
    return found


def render(items: Sequence[dict[str, Any]], judges: Sequence[str], rubric: str) -> str:
    """The list as Markdown for a person to read. The text is claim and judge text, untrusted."""
    lines = [
        f"# Judge disagreements ({len(items)})",
        "",
        f"Judges: {', '.join(judges)}. Rubric {rubric[:12]}. A judge's opinion, not truth; "
        "agreement shows consistency, not correctness.",
        "",
    ]
    for n, item in enumerate(items, start=1):
        lines.append(f"## {n}. {item['what']} {item['number']} of {item['trace']}")
        lines.append("")
        lines.append(f"> {item['text']}")
        if item["what"] == "claim":
            lines.append(
                f"({item['kind']}, {item['severity']}, "
                f"cites {', '.join(item['cites']) or 'nothing'})"
            )
        lines.append("")
        for judge, found in item["labels"].items():
            lines.append(f"- **{judge}**: {found['label']}: {found['reason']}")
        lines.append("")
    return "\n".join(lines)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
