"""Agent traces (`agent/runs/*.json`) read into records the scorers can use.

A trace is one run: the conversation, the evidence, the report, tokens and cost. Traces written
since Component 6's instrumentation also carry `calls`, `attempts`, `metrics`, `environment` and
`tags` (see `agent/trace.py`). Older traces have none of those, so everything the scorers need is
rebuilt from what every trace has: the tool calls and their order come from the conversation, the
report from `report`. Timing exists only in the newer ones (`has_timing`).
"""

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple


class Finding(NamedTuple):
    claim: str
    kind: str
    severity: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Call:
    """One tool call the model made (never `submit_report`)."""

    turn: int
    tool: str
    call_id: str
    arguments: dict[str, Any]
    ran: bool  # False: refused because the run was out of tool calls or time
    seconds: float | None = None  # monotonic seconds; None in older traces
    wall_seconds: float | None = None
    evidence_id: str | None = None
    ok: bool | None = None
    duplicate: bool = False
    error: bool = False
    result_sha: str | None = None

    @property
    def address(self) -> str | None:
        value = self.arguments.get("address") or self.arguments.get("token")
        return value.lower() if isinstance(value, str) else None


@dataclass(frozen=True)
class Attempt:
    """One try at `submit_report`."""

    attempt: int
    parsed: bool
    accepted: bool
    claims: int | None
    claims_failing: int | None
    problems: tuple[tuple[str, str], ...]  # (where, code)
    rebuilt: bool = False  # worked out afterwards from the conversation, not recorded by the run


@dataclass(frozen=True)
class RunRecord:
    path: str | None
    chain: str
    address: str  # lower case
    block: int | None
    model: str
    verdict: str
    summary: str
    findings: tuple[Finding, ...]
    checked: tuple[str, ...]
    not_checked: tuple[str, ...]  # "what: why"
    unavailable: tuple[str, ...]  # tools the run was not offered
    stopped_because: str
    accepted: bool
    attempt_count: int
    turns: int
    tool_call_count: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float
    seconds: float
    max_tool_calls: int
    calls: tuple[Call, ...]
    attempts: tuple[Attempt, ...]  # as recorded; empty in older traces
    submissions: tuple[str, ...]  # the raw arguments of every submit_report call
    evidence: tuple[dict[str, Any], ...]
    turn_records: tuple[dict[str, Any], ...]
    metrics: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, Any] = field(default_factory=dict)
    # Evidence id -> the tool message exactly as the model was shown it (the first one that carries
    # the id; a repeated call's "already run" note is not it). The faithfulness judge reads these.
    tool_texts: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.address}:{self.block}"

    @property
    def ran_calls(self) -> tuple[Call, ...]:
        return tuple(c for c in self.calls if c.ran)

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(c.tool for c in self.ran_calls)

    @property
    def has_timing(self) -> bool:
        return bool(self.metrics.get("schema"))

    @property
    def total_seconds(self) -> float:
        """Setup plus loop when recorded, else the loop's own figure."""
        return float(self.metrics["total_s"]) if self.has_timing else self.seconds

    @property
    def report_text(self) -> str:
        """Everything the report says, for searching: summary, claims, checks, what was not
        checked."""
        parts = [self.summary, *(f.claim for f in self.findings), *self.checked, *self.not_checked]
        return "\n".join(parts)


def _loads(text: Any) -> dict[str, Any]:
    try:
        value = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _calls(
    messages: list[dict[str, Any]], recorded: dict[str, dict[str, Any]]
) -> tuple[tuple[Call, ...], tuple[str, ...]]:
    """The tool calls in order (with what the trace recorded about each) and the raw arguments of
    every submit_report."""
    answers = {m.get("tool_call_id"): m.get("content") for m in messages if m.get("role") == "tool"}
    calls: list[Call] = []
    submissions: list[str] = []
    turn = 0
    for message in messages:
        if message.get("role") != "assistant":
            continue
        turn += 1
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            name = str(function.get("name"))
            if name == "submit_report":
                answer = _loads(answers.get(item.get("id")))
                # A submit sent together with other calls is refused unread (no "attempt" in the
                # answer); only a report the validator looked at counts as an attempt.
                if answer.get("accepted") is True or "attempt" in answer:
                    submissions.append(str(function.get("arguments") or ""))
                continue
            shown = _loads(answers.get(item.get("id")))
            note = recorded.get(str(item.get("id")), {})
            refused = "No more tool calls" in str(shown.get("error", ""))
            calls.append(
                Call(
                    turn=turn,
                    tool=name,
                    call_id=str(item.get("id")),
                    arguments=_loads(function.get("arguments")),
                    ran=bool(note.get("ran", not refused)),
                    seconds=note.get("seconds"),
                    wall_seconds=note.get("wall_seconds"),
                    evidence_id=shown.get("evidence_id") or note.get("evidence_id"),
                    ok=shown["ok"] if "ok" in shown else note.get("ok"),
                    duplicate=bool(shown.get("duplicate", note.get("duplicate", False))),
                    error="error" in shown,
                    result_sha=note.get("result_sha"),
                )
            )
    return tuple(calls), tuple(submissions)


def _tool_texts(messages: list[dict[str, Any]]) -> dict[str, str]:
    """What each evidence id looked like to the model: the content of the tool message that first
    carries it. A repeated call answers with a short "already run" note naming the earlier id,
    which is not the evidence."""
    out: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        shown = _loads(message["content"])
        evidence_id = shown.get("evidence_id")
        if isinstance(evidence_id, str) and not shown.get("duplicate"):
            out.setdefault(evidence_id, message["content"])
    return out


def parse_trace(data: dict[str, Any], path: str | None = None) -> RunRecord:
    report, info = data["report"], data["report"]["run"]
    recorded = {str(c.get("call_id")): c for c in data.get("calls") or []}
    calls, submissions = _calls(data.get("messages") or [], recorded)
    attempts = tuple(
        Attempt(
            attempt=a["attempt"],
            parsed=a["parsed"],
            accepted=a["accepted"],
            claims=a.get("claims"),
            claims_failing=a.get("claims_failing"),
            problems=tuple((p["where"], p["code"]) for p in a.get("problems") or []),
        )
        for a in data.get("attempts") or []
    )
    return RunRecord(
        path=path,
        chain=report["chain"],
        address=report["address"].lower(),
        block=report.get("block"),
        model=info["model"],
        verdict=report["verdict"],
        summary=report.get("summary", ""),
        findings=tuple(
            Finding(f["claim"], f["kind"], f["severity"], tuple(f["evidence_ids"]))
            for f in report.get("findings") or []
        ),
        checked=tuple(c["what"] for c in report.get("checked") or []),
        not_checked=tuple(f"{n['what']}: {n['why']}" for n in report.get("not_checked") or []),
        unavailable=tuple(u["tool"] for u in report.get("tools_not_available") or []),
        stopped_because=info["stopped_because"],
        accepted=bool(report["validation"]["accepted"]),
        attempt_count=int(report["validation"]["attempts"]),
        turns=info["turns"],
        tool_call_count=info["tool_calls"],
        prompt_tokens=info["prompt_tokens"],
        completion_tokens=info["completion_tokens"],
        cached_tokens=info["cached_tokens"],
        cost_usd=float(info["cost_usd"]),
        seconds=float(info["seconds"]),
        max_tool_calls=int((data.get("limits") or {}).get("max_tool_calls", 25)),
        calls=calls,
        attempts=attempts,
        submissions=submissions,
        evidence=tuple(data.get("evidence") or []),
        turn_records=tuple(data.get("turns") or []),
        metrics=dict(data.get("metrics") or {}),
        environment=dict(data.get("environment") or {}),
        tags=dict(data.get("tags") or {}),
        tool_texts=_tool_texts(data.get("messages") or []),
    )


def load_trace(path: Path) -> RunRecord:
    try:
        return parse_trace(json.loads(path.read_text()), str(path))
    except OSError as err:
        raise ValueError(f"{path} cannot be read ({type(err).__name__})") from None
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError(f"{path} is not an agent trace ({type(err).__name__}: {err})") from None


def trace_files(places: Iterable[Path]) -> Iterator[Path]:
    """The JSON files in the given files and folders, in name order (trace names start with the
    time, so that is run order)."""
    for place in places:
        if place.is_dir():
            yield from sorted(place.rglob("*.json"))
        else:
            yield place


def load_runs(places: Iterable[Path]) -> tuple[list[RunRecord], list[str]]:
    """Every trace under `places`, and a note for each file that was skipped."""
    runs, skipped = [], []
    for path in trace_files(places):
        try:
            runs.append(load_trace(path))
        except ValueError as err:
            skipped.append(str(err))
    return runs, skipped
