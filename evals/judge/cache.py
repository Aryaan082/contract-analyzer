"""Judgements, cached on disk so that scoring again costs nothing.

One JSON line per judgement, appended and never rewritten, in `<folder>/judgements.jsonl`. The key
is a hash of what decides the answer: the task, the claim and the evidence (or the summary and the
report's items), the judge model, the rubric hash, and a repeat number (0 for the normal pass; the
stability check uses 1 to get a second, independent answer to the same input). If a key occurs twice
in the file the later line wins, which is how an unparsed reply is retried.

A record keeps what was answered and what it cost, but not the claim or the evidence themselves.
Those are rebuilt from the traces when scoring, which looks the answers up by key.
"""

import json
import threading
from pathlib import Path
from typing import Any

from evals.judge.rubric import JUDGE_DIR

CACHE_DIR = JUDGE_DIR / "cache"
FILE_NAME = "judgements.jsonl"
KEEP_RAW = 1500  # characters of the raw reply kept, for reading what a refused reply said


def key_of(parts: list[Any]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def claim_key(
    claim_sha: str,
    evidence_sha: str,
    kind: str,
    severity: str,
    model: str,
    rubric: str,
    repeat: int = 0,
) -> str:
    return key_of(["claim", claim_sha, evidence_sha, kind, severity, model, rubric, repeat])


def summary_key(
    summary_sha: str, context_sha: str, model: str, rubric: str, repeat: int = 0
) -> str:
    return key_of(["summary", summary_sha, context_sha, model, rubric, repeat])


class JudgeCache:
    """In memory when `directory` is None (the tests)."""

    def __init__(self, directory: Path | None = None) -> None:
        self.path = None if directory is None else directory / FILE_NAME
        self._rows: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        if self.path is not None and self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._rows[row["key"]] = row

    def get(self, key: str) -> dict[str, Any] | None:
        return self._rows.get(key)

    def put(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._rows[row["key"]] = row
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as handle:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")

    def __len__(self) -> int:
        return len(self._rows)

    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows.values())
