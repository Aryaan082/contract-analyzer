"""One JSON file per run in `agent/runs/` (git-ignored): the conversation, every tool call's
result, the evidence registry, tokens and cost per model call, and the final report. It holds
chain data and model text but never the RPC URL or the API key, which no tool result carries.
Component 7's tracing builds on it."""

import json
from datetime import UTC, datetime
from pathlib import Path

from agent.config import RUNS_DIR, Limits
from agent.loop import AgentRun


def write_trace(run: AgentRun, limits: Limits, directory: Path = RUNS_DIR) -> Path:
    report = run.report
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}-{report.chain}-{report.address[2:10].lower()}.json"
    path.write_text(
        json.dumps(
            {
                "written_at": stamp,
                "limits": limits.model_dump(),
                "report": report.model_dump(mode="json"),
                "turns": run.turns,
                "evidence": [item.model_dump(mode="json") for item in run.registry],
                "messages": run.messages,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    return path
