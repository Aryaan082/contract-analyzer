"""Tool use: did the run look in the right places, and only those?

A run passes when every rule that applies to it holds. The rules are fixed before anyone looks at
traces, and the per-rule failure counts are reported next to the pass rate, because "62% pass" says
nothing and "fails the implementation-address rule in 9 of 40" says what to fix.

Three kinds of rule:
- hygiene, for every run: `identify_contract` first, no call repeated, under the tool-call cap, a
  validated report at the end;
- linked to what the card says about the contract (its traits and its labelled reasons): a proxy
  needs `resolve_proxy` and an analysis of the implementation, a blocked-sell honeypot needs
  `simulate_sell`, a flagged control (mint, blacklist, admin) needs a read of who holds it;
- explicit: the card's own `requires` and `forbids`.

A tool the run was not offered (no explorer key, say) cannot be required: that rule is skipped.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from evals.cards import Card
from evals.runs import RunRecord

ANALYSIS_TOOLS = ("risk_scan", "list_selectors", "match_references", "lookup_selector")
CONTROL_READS = ("read_getter", "read_storage")

# Labelled reason -> groups of tools; the run must call at least one tool from every group.
REASON_TOOLS: dict[str, tuple[tuple[str, ...], ...]] = {
    "honeypot-sell-blocked": (("simulate_sell",),),
    "hidden-transfer-tax": (("simulate_sell",),),
    "mutable-fees": (("risk_scan", "list_selectors", "simulate_sell"),),
    "privileged-mint": (("risk_scan", "list_selectors"), CONTROL_READS),
    "address-blacklist": (("risk_scan", "list_selectors"), CONTROL_READS),
    "pausable-transfers": (("risk_scan", "list_selectors"), CONTROL_READS),
    "single-key-admin": (CONTROL_READS + ("resolve_proxy",),),
    "upgradeable-implementation": (("resolve_proxy",),),
    "lookalike": (("identify_contract",),),
}


@dataclass(frozen=True)
class RuleResult:
    name: str
    passed: bool | None  # None: the rule did not apply to this run
    detail: str = ""


@dataclass(frozen=True)
class Trajectory:
    rules: tuple[RuleResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed is not False for r in self.rules)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.rules if r.passed is False)


def _called(run: RunRecord, *tools: str) -> bool:
    return any(t in tools for t in run.tools)


def _argument_key(call: Any) -> tuple[str, str]:
    return call.tool, repr(sorted(call.arguments.items()))


def check(run: RunRecord, card: Card | None = None) -> Trajectory:
    """The rules for this run. Without a card only the hygiene rules apply."""
    rules: list[RuleResult] = []

    def add(name: str, passed: bool | None, detail: str = "") -> None:
        rules.append(RuleResult(name, passed, detail))

    def needs(name: str, groups: Iterable[tuple[str, ...]], *, named: bool = False) -> None:
        """The run must call at least one tool of every group, unless it was not offered any.
        With `named`, each group's rule carries its tools in its name, so two groups under one
        reason cannot hide each other in the failure counts."""
        for group in groups:
            rule = f"{name}:{'/'.join(group)}" if named else name
            offered = [t for t in group if t not in run.unavailable]
            if not offered:
                add(rule, None, f"{'/'.join(group)} not offered")
            else:
                add(rule, _called(run, *offered), f"needs one of {', '.join(offered)}")

    first = run.tools[0] if run.tools else None
    add("identify_first", first == "identify_contract", f"first tool: {first}")

    seen = Counter(_argument_key(c) for c in run.ran_calls if c.tool != "taxonomy_lookup")
    repeated = sorted(f"{tool}" for (tool, _), n in seen.items() if n > 1)
    add("no_repeat_calls", not repeated, f"repeated: {', '.join(repeated)}" if repeated else "")

    add(
        "under_tool_cap",
        len(run.ran_calls) < run.max_tool_calls,
        f"{len(run.ran_calls)} calls, cap {run.max_tool_calls}",
    )
    add("finished", run.stopped_because == "report_accepted", f"stopped: {run.stopped_because}")

    if card is None:
        return Trajectory(tuple(rules))

    if "proxy" in card.traits:
        add("proxy_resolved", _called(run, "resolve_proxy"), "needs resolve_proxy")
        analysed = {
            c.address for c in run.ran_calls if c.tool in ANALYSIS_TOOLS and c.address is not None
        }
        if card.implementation:
            ok = card.implementation in analysed
            add("implementation_analysed", ok, f"needs an analysis of {card.implementation}")
        else:
            ok = bool(analysed - {card.address})
            add(
                "implementation_analysed",
                ok,
                "needs an analysis of an address other than the proxy",
            )
    if "token" in card.traits:
        needs("sell_rehearsed", (("simulate_sell",),))
    if "not_a_token" in card.traits or "official" in card.traits:
        add("no_pointless_sell", not _called(run, "simulate_sell"), "simulate_sell does not apply")
    needs("source_checked", (("get_verified_source",),))
    if "official" not in card.traits:
        needs("context_looked_up", (("taxonomy_lookup", "search_knowledge"),))
    for reason in card.reasons:
        needs(f"reason:{reason}", REASON_TOOLS.get(reason, ()), named=True)
    for tool in card.requires:
        needs(f"requires:{tool}", ((tool,),))
    for tool in card.forbids:
        add(f"forbids:{tool}", not _called(run, tool), f"{tool} must not be called")
    return Trajectory(tuple(rules))


def rule_failures(trajectories: Iterable[Trajectory]) -> dict[str, dict[str, int]]:
    """For each rule: in how many runs it applied and in how many it failed."""
    out: dict[str, dict[str, int]] = {}
    for trajectory in trajectories:
        for rule in trajectory.rules:
            if rule.passed is None:
                continue
            row = out.setdefault(rule.name, {"applied": 0, "failed": 0})
            row["applied"] += 1
            row["failed"] += rule.passed is False
    return dict(sorted(out.items()))
