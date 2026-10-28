"""What the judge is given, built from a run record.

For a claim: its text, kind and severity, and the text of every record it cites, as the agent saw
it (the tool message, or the knowledge record's text), cut to a total of EVIDENCE_CAP characters.
Not the verdict, the other claims, the summary or the agent's reasoning.

For a summary: its sentences, and the report's own findings, checked items and not-checked items.
Only accepted reports are judged: a report the validator refused never reached anyone.
"""

import hashlib
import json
import re
from dataclasses import dataclass

from evals.judge.rubric import EVIDENCE_CAP, Prompt, Rubric, claim_prompt, summary_prompt
from evals.runs import RunRecord

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
CUT_NOTE = "\n[cut: {n} more characters of this record are not shown]"


@dataclass(frozen=True)
class Record:
    id: str
    source: str  # "tool" | "knowledge" | "missing"
    text: str  # as the agent saw it, cut to its share of the cap
    cut: int = 0  # characters left out


@dataclass(frozen=True)
class ClaimInput:
    claim: str
    kind: str
    severity: str
    records: tuple[Record, ...]

    @property
    def claim_sha(self) -> str:
        return _sha(self.claim)

    @property
    def evidence_sha(self) -> str:
        return _sha(json.dumps([(r.id, r.text) for r in self.records]))

    def prompt(self, rubric: Rubric) -> Prompt:
        return claim_prompt(
            rubric,
            claim=self.claim,
            kind=self.kind,
            severity=self.severity,
            evidence=[(r.id, r.source, r.text) for r in self.records],
        )

    @property
    def cut(self) -> int:
        return sum(r.cut for r in self.records)

    @property
    def untrusted_texts(self) -> list[str]:
        return [self.claim, *(r.text for r in self.records)]


@dataclass(frozen=True)
class SummaryInput:
    sentences: tuple[str, ...]
    findings: tuple[str, ...]
    checked: tuple[str, ...]
    not_checked: tuple[str, ...]

    @property
    def summary_sha(self) -> str:
        return _sha(json.dumps(self.sentences))

    @property
    def context_sha(self) -> str:
        return _sha(json.dumps([self.findings, self.checked, self.not_checked]))

    def prompt(self, rubric: Rubric) -> Prompt:
        return summary_prompt(
            rubric,
            sentences=list(self.sentences),
            findings=list(self.findings),
            checked=list(self.checked),
            not_checked=list(self.not_checked),
        )

    @property
    def untrusted_texts(self) -> list[str]:
        return [*self.sentences, *self.findings, *self.checked, *self.not_checked]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def split_sentences(text: str) -> list[str]:
    """The sentences of a summary: split where a full stop, question mark or exclamation mark is
    followed by a space and a capital letter, digit, quote or bracket."""
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def _shown(run: RunRecord, evidence_id: str) -> tuple[str, str]:
    """(source, the text the agent saw) for a cited id."""
    registry = {str(e.get("id")): e for e in run.evidence}
    item = registry.get(evidence_id)
    if item is None:
        return "missing", "(no record with this id exists in the run)"
    if item.get("source") == "knowledge":
        return "knowledge", str(item.get("data") or "")
    text = run.tool_texts.get(evidence_id)
    if text is None:  # a trace without its messages: the registry entry stands in for them
        text = json.dumps(
            {
                "evidence_id": evidence_id,
                "tool": item.get("tool"),
                "ok": item.get("ok"),
                "provenance": item.get("provenance"),
                "data": item.get("data"),
            },
            ensure_ascii=False,
        )
    return "tool", text


def records_for(
    run: RunRecord, evidence_ids: tuple[str, ...], cap: int = EVIDENCE_CAP
) -> tuple[Record, ...]:
    """The cited records in the order cited, each once, sharing `cap` characters: a record that
    does not fit is cut where it stands and the ones after it are cut to nothing."""
    out: list[Record] = []
    left = cap
    for evidence_id in dict.fromkeys(evidence_ids):
        source, text = _shown(run, evidence_id)
        if len(text) <= left:
            out.append(Record(evidence_id, source, text))
            left -= len(text)
            continue
        kept = text[: max(left, 0)]
        out.append(
            Record(
                evidence_id,
                source,
                kept + CUT_NOTE.format(n=len(text) - len(kept)),
                len(text) - len(kept),
            )
        )
        left = 0
    return tuple(out)


def claim_inputs(run: RunRecord) -> list[ClaimInput]:
    return [
        ClaimInput(f.claim, f.kind, f.severity, records_for(run, f.evidence_ids))
        for f in run.findings
    ]


def summary_input(run: RunRecord) -> SummaryInput | None:
    sentences = split_sentences(run.summary)
    if not sentences:
        return None
    return SummaryInput(
        sentences=tuple(sentences),
        findings=tuple(f.claim for f in run.findings),
        checked=tuple(run.checked),
        not_checked=tuple(run.not_checked),
    )


def judgeable(run: RunRecord) -> bool:
    """Only a report that was accepted and delivered is judged."""
    return run.accepted and run.stopped_because == "report_accepted"
