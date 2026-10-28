"""A folder of JSON-lines files that make the scans resumable: one line per finished token, keyed by
token address. Lines are only appended, so an interrupted scan loses at most the token it was on."""

import json
from pathlib import Path
from typing import Any

CACHE = Path(__file__).resolve().parent / "cache"


class Table:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.rows[row["token"]] = row

    def __contains__(self, token: str) -> bool:
        return token in self.rows

    def __len__(self) -> int:
        return len(self.rows)

    def add(self, row: dict[str, Any]) -> None:
        self.rows[row["token"]] = row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
