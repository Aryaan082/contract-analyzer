"""Builders for the scoring tests: a trace the way `agent/trace.py` writes it, from a few facts.

`make_trace` assembles the conversation (one tool call per model turn), the evidence, the recorded
calls and metrics, so a test states only what it cares about: the verdict, the tools, the seconds.
`as_old_trace` strips the keys that older traces do not have.
"""

import json
from typing import Any

ADDR = "0x" + "ab" * 20
IMPL = "0x" + "cd" * 20

OLD_KEYS = ("calls", "attempts", "metrics", "environment", "tags")
SEVERITY = {"high_risk": "high", "elevated_risk": "medium"}


def make_trace(
    *,
    address: str = ADDR,
    chain: str = "ethereum",
    block: int | None = 100,
    model: str = "vendor/cheap",
    verdict: str = "elevated_risk",
    tools: list[tuple[str, dict[str, Any]]] | None = None,
    summary: str = "A token with a mint function; several checks were not run.",
    claim: str = "The contract has a mint function.",
    not_checked: tuple[tuple[str, str], ...] = (("Who may call mint", "No owner read."),),
    unavailable: tuple[str, ...] = (),
    first_try_ok: bool = True,
    stopped: str = "report_accepted",
    timing: bool = True,
    tool_seconds: float = 1.0,
    model_seconds: float = 2.0,
    skew: float = 0.0,
    tags: dict[str, Any] | None = None,
    cost: float = 0.01,
    max_tool_calls: int = 25,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tools = tools if tools is not None else [("identify_contract", {"address": address})]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "Analyse it."},
    ]
    evidence, calls = [], []
    for number, (name, arguments) in enumerate(tools, start=1):
        call_id = f"call_{number}"
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        )
        data = {"summary": "standard"} if name == "identify_contract" else {"note": name}
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(
                    {"evidence_id": f"T{number}", "tool": name, "ok": True, "data": data}
                ),
            }
        )
        evidence.append(
            {
                "id": f"T{number}",
                "source": "tool",
                "ok": True,
                "citation": f"{name}(...)",
                "tool": name,
                "arguments": arguments,
                "provenance": None,
                "data": data,
            }
        )
        calls.append(
            {
                "turn": number,
                "tool": name,
                "call_id": call_id,
                "ran": True,
                "seconds": tool_seconds,
                "wall_seconds": tool_seconds,
                "arguments_sha": f"a{number}",
                "result_sha": f"r{number}",
                "evidence_id": f"T{number}",
                "ok": True,
                "duplicate": False,
                "error": False,
            }
        )

    severity = SEVERITY.get(verdict, "info")
    good = {
        "verdict": verdict,
        "summary": summary,
        "findings": [
            {"claim": claim, "kind": "observation", "severity": severity, "evidence_ids": ["T1"]}
        ],
        "checked": [{"what": "Contract kind", "evidence_ids": ["T1"]}],
        "not_checked": [{"what": w, "why": y} for w, y in not_checked],
    }
    bad = {
        **good,
        "findings": [
            {
                "claim": "Invented.",
                "kind": "observation",
                "severity": "info",
                "evidence_ids": ["T99"],
            }
        ],
    }
    submissions = [good] if stopped == "report_accepted" else []
    attempts_log = []
    if not submissions:
        pass  # the run ended before it tried a report
    elif first_try_ok:
        attempts_log.append(
            {
                "attempt": 1,
                "turn": len(tools) + 1,
                "parsed": True,
                "accepted": True,
                "claims": 1,
                "claims_failing": 0,
                "problems": [],
            }
        )
    else:
        submissions = [bad, good]
        attempts_log = [
            {
                "attempt": 1,
                "turn": len(tools) + 1,
                "parsed": True,
                "accepted": False,
                "claims": 1,
                "claims_failing": 1,
                "problems": [{"where": "findings[0]", "code": "unknown_evidence"}],
            },
            {
                "attempt": 2,
                "turn": len(tools) + 2,
                "parsed": True,
                "accepted": True,
                "claims": 1,
                "claims_failing": 0,
                "problems": [],
            },
        ]
    for number, body in enumerate(submissions, start=1):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"submit_{number}",
                        "type": "function",
                        "function": {"name": "submit_report", "arguments": json.dumps(body)},
                    }
                ],
            }
        )
        messages.append(
            {"role": "tool", "tool_call_id": f"submit_{number}", "content": '{"accepted": true}'}
        )

    turns_count = len(tools) + len(submissions)
    model_s = model_seconds * turns_count
    tools_s = tool_seconds * len(tools)
    loop_s = model_s + tools_s
    accepted = stopped == "report_accepted"
    trace: dict[str, Any] = {
        "written_at": "20261028T000000Z",
        "limits": {"max_turns": 12, "max_tool_calls": max_tool_calls},
        "report": {
            "chain": chain,
            "address": address,
            "block": block,
            "verdict": verdict,
            "summary": summary,
            "findings": good["findings"],
            "checked": good["checked"],
            "not_checked": good["not_checked"],
            "tools_not_available": [{"tool": t, "what": "not offered"} for t in unavailable],
            "evidence": [],
            "validation": {
                "accepted": accepted,
                "attempts": len(submissions),
                "problems": [],
            },
            "run": {
                "model": model,
                "turns": turns_count,
                "tool_calls": len(tools),
                "prompt_tokens": 1000 * turns_count,
                "completion_tokens": 100 * turns_count,
                "cached_tokens": 750 * turns_count,
                "cost_usd": cost,
                "seconds": round(loop_s, 1),
                "stopped_because": stopped,
            },
        },
        "turns": [
            {"turn": i, "tools": [t[0]], "seconds": model_seconds, "wall_seconds": model_seconds}
            for i, t in enumerate(tools, start=1)
        ],
        "evidence": evidence,
        "messages": messages,
        "calls": calls,
        "attempts": attempts_log,
        "metrics": {
            "schema": 2,
            "setup_s": 0.5,
            "loop_s": loop_s,
            "total_s": loop_s + 0.5,
            "model_s": model_s,
            "tools_s": tools_s,
            "overhead_s": 0.0,
            "wall_s": loop_s + 0.5 + skew,
            "skew_s": skew,
            "counters": {},
        },
        "environment": environment if environment is not None else {"git_commit": "abc12345"},
        "tags": tags or {},
    }
    if not timing:
        trace["metrics"] = {}
        for call in trace["calls"]:
            call["seconds"] = call["wall_seconds"] = None
    return trace


def as_old_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """The same run as a trace from before the instrumentation: no `calls`, `attempts`, `metrics`,
    `environment` or `tags`."""
    return {k: v for k, v in trace.items() if k not in OLD_KEYS}


def tools_for(*names: str, address: str = ADDR) -> list[tuple[str, dict[str, Any]]]:
    return [(n, {"address": address}) for n in names]
