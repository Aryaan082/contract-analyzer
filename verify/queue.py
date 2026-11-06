"""The review queue: the flags the reasoning verifier calls valid, as a file a person acts on
(Aryaan, 2026-11-06: "my approval should only be needed to add to the directory").

`entries_from` turns the shown places of a `stage.ReviewList` into pending entries. A person marks
each one approved or rejected (`decide`). Only an approved entry can become a directory record
(`directory_record`), and never one from a held-out report: those entries are shown but marked
`directory_ok: false`, and `decide` refuses to approve them. Nothing here writes to the directory
itself; the approved records are the staging area, tagged with where they came from
(`source: model_verified_user_approved`) so that an evaluation can leave them out.

A place a report, a check or a test already vouches for is not queued: it needs no approval. The
sentences are the models' and are untrusted; they are cleaned (`reason.clean`) before they get
here, they describe the flaw and the fix in words, and the directory never holds attack steps or
exploit code.
"""

import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

from verify.reason import clean
from verify.stage import Annotated, ReviewList

STATUSES = ("pending", "approved", "rejected")
SOURCE = "model_verified_user_approved"
FORMAT = 1


@dataclass(frozen=True)
class QueueEntry:
    id: str
    report: str
    file: str
    contract: str
    function: str
    start_line: int
    end_line: int
    severity: str  # the flagging reader's guess, not the verifier's
    claim: str
    counter: str
    scenario: str
    harm: str
    fix: str
    confidence: str
    category: str  # Trail of Bits' type, from the verifier (`verify.definitions`)
    tob_severity: str  # the verifier's severity in their scale, not the flagging reader's guess
    difficulty: str
    root_cause: str  # where the cause is when it is not the flagged function
    verifier: str  # the model that said valid
    layers: tuple[str, ...]
    directory_ok: bool  # false for a held-out report
    status: str = "pending"
    decided_on: str = ""
    note: str = ""  # the person's own words

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}; use one of {STATUSES}")


def entry_of(a: Annotated, *, verifier: str, directory_ok: bool) -> QueueEntry:
    assert a.reasoned is not None and a.reasoned.verdict == "valid"
    c, r = a.candidate, a.reasoned
    return QueueEntry(
        id=c.id, report=c.report, file=c.file, contract=c.contract, function=c.function,
        start_line=c.start_line, end_line=c.end_line, severity=c.severity,
        claim=clean(c.note, 400), counter=r.counter, scenario=r.scenario, harm=r.harm, fix=r.fix,
        confidence=r.confidence, category=r.category, tob_severity=r.severity,
        difficulty=r.difficulty, root_cause=r.root_cause, verifier=verifier,
        layers=tuple(sorted(c.layers)), directory_ok=directory_ok,
    )  # fmt: skip


def entries_from(
    review: ReviewList, *, verifier: str, held_out_reports: Iterable[str] = ()
) -> list[QueueEntry]:
    """Pending entries for the places the verifier calls valid and no report, check or test
    already vouches for."""
    held = set(held_out_reports)
    return [
        entry_of(a, verifier=verifier, directory_ok=a.candidate.report not in held)
        for a in review.shown
        if a.status == "valid"
    ]


def merge(existing: Sequence[QueueEntry], new: Iterable[QueueEntry]) -> list[QueueEntry]:
    """The queue after a new run: an entry already in it keeps its status and note, whatever the
    new run says; a new one is added pending."""
    seen = {e.id for e in existing}
    return [*existing, *(e for e in new if e.id not in seen)]


def decide(
    entries: Sequence[QueueEntry], entry_id: str, status: str, *, note: str = "", on: str = ""
) -> list[QueueEntry]:
    """The queue with one entry approved or rejected. A held-out report's entry cannot be
    approved. Raises KeyError for an unknown id and ValueError for a status that is not a
    decision."""
    if status not in ("approved", "rejected"):
        raise ValueError("a decision is approved or rejected")
    out, found = [], False
    for e in entries:
        if e.id == entry_id:
            found = True
            if status == "approved" and not e.directory_ok:
                raise ValueError("a held-out report's entry cannot go into the directory")
            e = replace(
                e, status=status, note=clean(note, 300), decided_on=on or date.today().isoformat()
            )
        out.append(e)
    if not found:
        raise KeyError(entry_id)
    return out


def directory_record(entry: QueueEntry) -> dict:
    """What the directory would hold for an approved entry: where, what, the harm and the fix. No
    page or auditor: the source says who vouched for it."""
    if entry.status != "approved" or not entry.directory_ok:
        raise ValueError("only an approved entry of a report that may feed the directory")
    return {
        "id": f"{entry.report}:MODEL-{entry.id}",
        "source": SOURCE,
        "report": entry.report,
        "file": entry.file,
        "contract": entry.contract,
        "function": entry.function,
        "lines": [entry.start_line, entry.end_line],
        "title": entry.claim[:160],
        "description": f"{entry.scenario} {entry.harm}".strip(),
        "recommendation": entry.fix,
        "type": entry.category,
        "severity": entry.tob_severity or entry.severity,
        "difficulty": entry.difficulty,
        "root_cause": entry.root_cause,
        "verifier": entry.verifier,
        "approved_on": entry.decided_on,
    }


def approved_records(entries: Iterable[QueueEntry]) -> list[dict]:
    return [directory_record(e) for e in entries if e.status == "approved" and e.directory_ok]


def load(path: Path) -> list[QueueEntry]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if data.get("format") != FORMAT:
        raise ValueError(f"{path} is not a review queue of format {FORMAT}")
    return [QueueEntry(**{**e, "layers": tuple(e["layers"])}) for e in data["entries"]]


def save(path: Path, entries: Sequence[QueueEntry]) -> None:
    """Written whole to a temporary file and moved into place, so a crash never leaves half a
    queue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"format": FORMAT, "entries": [asdict(e) for e in entries]}, indent=1)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
