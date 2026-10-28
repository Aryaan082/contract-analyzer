"""One JSON file per run in `agent/runs/` (git-ignored): the conversation, every tool call's
result, the evidence registry, tokens, cost and seconds per model call, and the final report. It
holds chain data and model text but never the RPC URL or the API key, which no tool result carries.
Component 7's tracing builds on it, and Component 6 scores it (`evals/`).

The keys added for Component 6 (the older traces have none of them, and the scorer copes):
`calls` (one entry per tool call: seconds, fingerprints), `attempts` (one per `submit_report`),
`metrics` (where the time went, store hits, fingerprints of the prompt and tools), `environment`
(code version, settings file, machine) and `tags` (free labels from the caller, for example
`{"concurrent": true}`)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.config import RUNS_DIR, Limits
from agent.loop import AgentRun
from agent.runmeta import environment


def write_trace(
    run: AgentRun,
    limits: Limits,
    directory: Path = RUNS_DIR,
    *,
    tags: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
) -> Path:
    report = run.report
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(
            {
                "written_at": stamp,
                "limits": limits.model_dump(),
                "report": report.model_dump(mode="json"),
                "turns": run.turns,
                "calls": run.calls,
                "attempts": run.attempts,
                "metrics": run.metrics,
                "environment": environment() if env is None else env,
                "tags": tags or {},
                "evidence": [item.model_dump(mode="json") for item in run.registry],
                "messages": run.messages,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    # Named by the second and the address, so two runs of one contract in the same second (several
    # evals at once) would share a name. A file is only ever created, never overwritten: the second
    # gets "-2", the third "-3". (Two traces of the 2026-10-27 model smoke test were lost to this.)
    name = f"{stamp}-{report.chain}-{report.address[2:10].lower()}"
    for number in range(1, 1000):
        path = directory / (f"{name}.json" if number == 1 else f"{name}-{number}.json")
        try:
            with path.open("x") as handle:
                handle.write(text)
        except FileExistsError:
            continue
        return path
    raise OSError(f"no free trace name for {name}")
