"""The versioned results file: one JSON document per scored run-set, written to `evals/results/`.

It holds the manifest (what was scored, with which model, at which commit, how many labels nobody
has reviewed), every layer's metrics with their intervals, and the time and cost blocks.
Per-contract
detail is left out for the held-out split, as the retrieval layer already does: nobody tunes against
a score they cannot see per item.

`schema_version` changes whenever a key is renamed or removed, so an old file can still be read.
"""

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from evals.metrics import Interval

SCHEMA_VERSION = 1
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def interval_json(value: Interval | None) -> dict[str, float] | None:
    if value is None:
        return None
    return {
        "value": round(value.mean, 4),
        "low": round(value.low, 4),
        "high": round(value.high, 4),
    }


def clean(obj: Any) -> Any:
    """The structure with Intervals turned into dicts and NaN into None, ready for JSON."""
    if isinstance(obj, Interval):
        return interval_json(obj)
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else round(obj, 4)
    return obj


def results_name(created: date, commit: str | None, label: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in label).strip("-") or "runset"
    return f"{created.isoformat()}-{(commit or 'nocommit')[:8]}-{safe}.json"


def build_results(manifest: dict[str, Any], layers: dict[str, Any]) -> dict[str, Any]:
    return clean(
        {
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "manifest": manifest,
            **layers,
        }
    )


def write_results(results: dict[str, Any], label: str, directory: Path = RESULTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    commits = results["manifest"].get("git_commits") or []
    path = directory / results_name(date.today(), commits[0] if commits else None, label)
    path.write_text(json.dumps(results, indent=1) + "\n")
    return path
