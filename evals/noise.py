"""The noise study's analysis: how much does a contract's result change between runs of the same
agent, and why. Offline; it reads a run-set of traces and the cards. The definitions are in
evals/noise-study.md and were fixed before any run.

Everything compares runs of ONE contract with each other, and every figure carries its n.

- verdict flip, exact: the contract's runs do not all have the same verdict (an abstain counts as a
  verdict); direction: among the runs that did not abstain, some warn and some do not. Abstains are
  counted apart, never as a pass or a fail.
- tool-set change: the set of distinct tools called differs between runs.
- attribution: for each pair of runs of a contract, the calls (a tool and its arguments) both made
  are compared by the fingerprint of their results. The same calls and results -> the model
  (`same_inputs`); different calls, but every shared call returned the same -> the model chose
  differently from the same facts (`calls_differ`); a shared call returned a different result -> the
  tools (`results_differ`). Set-based: the order of the calls is not used. Two tools need care
  (found in the first real run, see `call_results`): `lookup_selector` reports where its answer came
  from (`asked_database`, `from_cache`), which is not part of the answer; `taxonomy_lookup` without
  arguments reads the evidence the run had gathered so far, so its question includes `signals_used`.

Shares of contracts come with the cluster bootstrap that `repeats.py` uses (it resamples contracts,
not runs) and with the exact binomial interval, because the bootstrap of a share of ten contracts
collapses to a point at zero or ten.
"""

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import fields
from itertools import combinations
from typing import Any

from evals.cards import Card, CardSet
from evals.detection import verdict_right
from evals.noise_select import NOTE_PATH, STUDY_PATH, Decision, Study, files_sha256
from evals.repeats import cluster_bootstrap, contract_rates
from evals.results_file import build_results
from evals.runs import RunRecord
from evals.timing import cost_summary, time_summary
from evals.trajectory import check

ABSTAIN = "insufficient_evidence"
WARNING = ("elevated_risk", "high_risk")
CLASSES = ("same_inputs", "calls_differ", "results_differ", "unknown")
SCHEMA_VERSION = 1


# ---- small statistics ----------------------------------------------------------------------------


def _cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def _bisect(f: Callable[[float], float], increasing: bool) -> float:
    low, high = 0.0, 1.0
    for _ in range(80):
        mid = (low + high) / 2
        if (f(mid) < 0) == increasing:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def exact_interval(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Clopper-Pearson interval for k out of n."""
    tail = (1 - level) / 2
    low = 0.0 if k == 0 else _bisect(lambda p: (1 - _cdf(k - 1, n, p)) - tail, True)
    high = 1.0 if k == n else _bisect(lambda p: _cdf(k, n, p) - tail, False)
    return low, high


def _round(x: float) -> float:
    return round(x, 4)


def share_of(flags: Sequence[bool], *, resamples: int) -> dict[str, Any]:
    """k of n with both intervals; n = 0 gives only the count."""
    n, k = len(flags), sum(flags)
    if not n:
        return {"k": 0, "n": 0}
    boot = cluster_bootstrap(
        [[f] for f in flags], lambda xs: sum(xs) / len(xs), resamples=resamples
    )
    low, high = exact_interval(k, n)
    return {
        "k": k,
        "n": n,
        "share": _round(k / n),
        "bootstrap": [_round(boot.low), _round(boot.high)],
        "exact_95": [_round(low), _round(high)],
    }


def _parts(obj: Any) -> dict[str, Any]:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


# ---- grouping ------------------------------------------------------------------------------------


def group_runs(
    runs: Sequence[RunRecord], cards: CardSet
) -> tuple[dict[str, list[RunRecord]], dict[str, Card], int]:
    """Runs by contract (in repeat order), the card of each, and how many runs match no card."""
    grouped: dict[str, list[RunRecord]] = defaultdict(list)
    found: dict[str, Card] = {}
    loose = 0
    for run in runs:
        card = cards.find(run)
        if card is None:
            loose += 1
            continue
        grouped[card.id].append(run)
        found[card.id] = card
    for rows in grouped.values():
        rows.sort(key=lambda r: (int(r.tags.get("repeat") or 0), r.path or ""))
    return dict(grouped), found, loose


def stance(verdict: str) -> str:
    return "warn" if verdict in WARNING else "clear"


def cache_state_of(run: RunRecord) -> str:
    return str(run.tags.get("cache_state", "untagged"))


# ---- the measures --------------------------------------------------------------------------------


def verdict_block(by_contract: dict[str, list[RunRecord]], resamples: int) -> dict[str, Any]:
    exact, substantive, direction = [], [], []
    contracts: dict[str, Any] = {}
    flipped: dict[str, dict[str, int]] = {}
    for card_id, rows in by_contract.items():
        verdicts = [r.verdict for r in rows]
        real = [v for v in verdicts if v != ABSTAIN]
        row: dict[str, Any] = {
            "runs": len(rows),
            "verdicts": dict(Counter(verdicts)),
            "abstains": len(verdicts) - len(real),
        }
        if len(rows) >= 2:
            row["flips_exact"] = len(set(verdicts)) > 1
            exact.append(row["flips_exact"])
            if row["flips_exact"]:
                flipped[card_id] = dict(Counter(verdicts))
        if len(real) >= 2:
            row["flips_substantive"] = len(set(real)) > 1
            row["flips_direction"] = len({stance(v) for v in real}) > 1
            substantive.append(row["flips_substantive"])
            direction.append(row["flips_direction"])
        contracts[card_id] = row
    return {
        "exact": share_of(exact, resamples=resamples),
        "substantive": share_of(substantive, resamples=resamples),
        "direction": share_of(direction, resamples=resamples),
        "flipped": dict(sorted(flipped.items())),
        "contracts": dict(sorted(contracts.items())),
    }


def abstain_block(by_contract: dict[str, list[RunRecord]], resamples: int) -> dict[str, Any]:
    everyone = [r for rows in by_contract.values() for r in rows]
    abstained = [r for r in everyone if r.verdict == ABSTAIN]
    clusters = [[r.verdict == ABSTAIN for r in rows] for rows in by_contract.values()]
    out: dict[str, Any] = {
        "runs": len(abstained),
        "of": len(everyone),
        "contracts": sum(any(c) for c in clusters),
        "contracts_mixed": sum(any(c) and not all(c) for c in clusters),
        "by_cause": dict(Counter(r.stopped_because for r in abstained)),
        "hit_tool_cap_runs": sum(len(r.ran_calls) >= r.max_tool_calls for r in everyone),
    }
    if clusters:
        interval = cluster_bootstrap(
            clusters, lambda xs: sum(xs) / len(xs) if xs else float("nan"), resamples=resamples
        )
        out["share"] = [_round(interval.mean), _round(interval.low), _round(interval.high)]
    return out


def tool_block(by_contract: dict[str, list[RunRecord]], resamples: int) -> dict[str, Any]:
    changed, per_contract = [], {}
    for card_id, rows in by_contract.items():
        sets = [frozenset(r.tools) for r in rows]
        union = frozenset().union(*sets) if sets else frozenset()
        always = frozenset.intersection(*sets) if sets else frozenset()
        counts = [len(r.ran_calls) for r in rows]
        row: dict[str, Any] = {
            "runs": len(rows),
            "calls_min": min(counts),
            "calls_max": max(counts),
            "sometimes": {t: sum(t in s for s in sets) for t in sorted(union - always)},
        }
        if len(rows) >= 2:
            row["set_changed"] = len(set(sets)) > 1
            changed.append(row["set_changed"])
        per_contract[card_id] = row
    everyone = [r for rows in by_contract.values() for r in rows]
    return {
        "set_changed": share_of(changed, resamples=resamples),
        "contracts": dict(sorted(per_contract.items())),
        "runs_calling": dict(sorted(Counter(t for r in everyone for t in set(r.tools)).items())),
        "runs": len(everyone),
    }


def stability_block(
    by_contract: dict[str, list[RunRecord]], found: dict[str, Card], resamples: int
) -> dict[str, Any]:
    """Pass rates over repeats, with the harness's own measures: the share of single runs that are
    right, of contracts where the majority is right, of contracts where every run is right."""
    out: dict[str, Any] = {}
    right = {
        cid: [bool(verdict_right(r, found[cid])) for r in rows]
        for cid, rows in by_contract.items()
        if found[cid].scored
    }
    if right:
        out["verdict_right"] = _parts(contract_rates(right, resamples=resamples))
    passed = {cid: [check(r, found[cid]).passed for r in rows] for cid, rows in by_contract.items()}
    if passed:
        out["tool_use_pass"] = _parts(contract_rates(passed, resamples=resamples))
    return out


# ---- attribution ---------------------------------------------------------------------------------


# What the recorded fingerprint of a tool result gets wrong for two tools (found by diffing the
# results of identical calls in the first noise run-set):
# - `lookup_selector` says in its data how many names came from the cache and how many from the
#   selector database. That is bookkeeping about the run's stores, so the first run of a contract
#   (cold) and the next ones (warm) got different fingerprints for the same names.
# - `taxonomy_lookup` called without arguments looks at the evidence recorded so far, so the same
#   call is a different question after a different earlier call. Its question includes the
#   signals it used (`signals_used`), and a result that differs for the same signals is real.
BOOKKEEPING = {"lookup_selector": ("asked_database", "from_cache")}  # keys under "data"
DERIVED_INPUT = {"taxonomy_lookup": "signals_used"}  # a top-level key of the message


def _shown(run: RunRecord, call_id: str) -> dict[str, Any] | None:
    text = run.tool_messages.get(call_id)
    try:
        shown = json.loads(text) if text else None
    except ValueError:
        return None
    return shown if isinstance(shown, dict) else None


def call_results(run: RunRecord) -> dict[tuple[str, str], str] | None:
    """The calls a run made, each a tool and its arguments (and, for a tool that reads the run's
    own evidence, what it read), with the fingerprint of its result. The recorded fingerprint is
    used, except for the tools above, which are read again from the tool message the model was
    shown. None if the run has no fingerprints (an older trace)."""
    out: dict[tuple[str, str], str] = {}
    for call in run.ran_calls:
        if call.duplicate:
            continue
        if call.result_sha is None:
            return None
        arguments = json.dumps(call.arguments, sort_keys=True, default=str)
        sha = call.result_sha
        if call.tool in BOOKKEEPING or call.tool in DERIVED_INPUT:
            shown = _shown(run, call.call_id)
            if shown is not None:
                shown.pop("evidence_id", None)
                data = shown.get("data")
                if isinstance(data, dict):
                    for key in BOOKKEEPING.get(call.tool, ()):
                        data.pop(key, None)
                sha = hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()[:16]
                if call.tool in DERIVED_INPUT:
                    arguments += json.dumps(shown.get(DERIVED_INPUT[call.tool]), sort_keys=True)
        out.setdefault((call.tool, arguments), sha)
    return out


def classify(a: dict[tuple[str, str], str] | None, b: dict[tuple[str, str], str] | None) -> str:
    if a is None or b is None:
        return "unknown"
    if any(a[k] != b[k] for k in a.keys() & b.keys()):
        return "results_differ"
    return "same_inputs" if a.keys() == b.keys() else "calls_differ"


BUCKETS = (
    "different_direction",
    "same_direction_other_verdict",
    "same_verdict",
    "abstain_involved",
)


def bucket_of(a: RunRecord, b: RunRecord) -> str:
    if ABSTAIN in (a.verdict, b.verdict):
        return "abstain_involved"
    if a.verdict == b.verdict:
        return "same_verdict"
    return (
        "different_direction"
        if stance(a.verdict) != stance(b.verdict)
        else ("same_direction_other_verdict")
    )


def attribution_block(by_contract: dict[str, list[RunRecord]]) -> dict[str, Any]:
    counts: dict[str, Counter[str]] = {b: Counter() for b in BUCKETS}
    contracts: dict[str, set[str]] = {b: set() for b in BUCKETS}
    per_tool: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # tool -> [shared, differing]
    state_mix: dict[str, Counter[str]] = {"verdict_differs": Counter(), "verdict_same": Counter()}
    by_contract_rows: dict[str, dict[str, int]] = {}
    for card_id, rows in by_contract.items():
        keyed = [call_results(r) for r in rows]
        mine: Counter[str] = Counter()
        for (i, a), (j, b) in combinations(enumerate(rows), 2):
            kind = classify(keyed[i], keyed[j])
            bucket = bucket_of(a, b)
            counts[bucket][kind] += 1
            contracts[bucket].add(card_id)
            if keyed[i] is not None and keyed[j] is not None:
                for key in keyed[i].keys() & keyed[j].keys():
                    per_tool[key[0]][0] += 1
                    per_tool[key[0]][1] += keyed[i][key] != keyed[j][key]
            if bucket != "abstain_involved":
                mixed = (
                    "same_state" if cache_state_of(a) == cache_state_of(b) else "different_state"
                )
                state_mix["verdict_same" if bucket == "same_verdict" else "verdict_differs"][
                    mixed
                ] += 1
            if bucket in ("different_direction", "same_direction_other_verdict"):
                mine[kind] += 1
        if mine:
            by_contract_rows[card_id] = dict(mine)
    return {
        "buckets": {
            b: {
                "pairs": sum(counts[b].values()),
                "contracts": len(contracts[b]),
                **{c: counts[b].get(c, 0) for c in CLASSES},
            }
            for b in BUCKETS
        },
        "per_tool": {
            t: {"shared_calls": n, "differing": d, "share": _round(d / n)}
            for t, (n, d) in sorted(per_tool.items())
        },
        "state_mix": {k: dict(v) for k, v in state_mix.items()},
        "differing_verdict_by_contract": dict(sorted(by_contract_rows.items())),
    }


def differing_pairs(attribution: dict[str, Any]) -> tuple[int, int]:
    """(pairs of non-abstaining runs with a different verdict, how many have results_differ)."""
    rows = [
        attribution["buckets"][b] for b in ("different_direction", "same_direction_other_verdict")
    ]
    return sum(r["pairs"] for r in rows), sum(r["results_differ"] for r in rows)


# ---- the decision rule ---------------------------------------------------------------------------


def decide_r(
    analysis: dict[str, Any], decision: Decision, expected_contracts: int
) -> dict[str, Any]:
    """The decision rule of evals/noise-study.md, applied. A proposal for Aryaan to approve."""
    verdict = analysis["verdict"]
    exact_n = verdict["exact"]["n"]
    if exact_n < expected_contracts:
        return {
            "level": None,
            "reason": f"only {exact_n} of the study's {expected_contracts} contracts have two or "
            "more runs: the rule is not applied to an incomplete study",
            "overrides": [],
        }
    direction = verdict["direction"]["k"]
    exact = verdict["exact"]["k"]
    if direction >= decision.noisy_direction_min or exact >= decision.noisy_exact_min:
        level = "noisy"
    elif direction <= decision.calm_direction_max and exact <= decision.calm_exact_max:
        level = "calm"
    else:
        level = "moderate"
    out: dict[str, Any] = {
        "level": level,
        "direction_flips": direction,
        "exact_flips": exact,
        **{
            "calm": {
                "quick": "R=1",
                "milestone": "R=3",
                "top_up": "none",
            },
            "moderate": {
                "quick": "R=1 only to check that nothing broke; R=3 to compare two changes",
                "milestone": "R=3, then 2 more repeats for each contract that disagrees (cap 5)",
                "top_up": "2 more repeats for contracts whose runs disagree, capped at 5 in all",
            },
            "noisy": {
                "quick": "R=3",
                "milestone": "R=5 for every contract",
                "top_up": "none",
            },
        }[level],
    }
    overrides = []
    abstains = analysis["abstains"]["runs"]
    if abstains > decision.abstain_max:
        overrides.append(
            f"abstains: {abstains} of {analysis['abstains']['of']} runs abstained (more than "
            f"{decision.abstain_max}): find the cause before choosing R; repeats do not fix an "
            "abstain caused by a cap or a time limit"
        )
    pairs, by_tools = differing_pairs(analysis["attribution"])
    if pairs and by_tools / pairs >= decision.tools_share:
        overrides.append(
            f"tools: {by_tools} of {pairs} pairs with a different verdict had a different result "
            "for a call both runs made: make the volatile tool repeatable before spending on "
            "repeats"
        )
    out["overrides"] = overrides
    out["floor"] = "a milestone run keeps R at 3 or more whatever the study finds"
    return out


# ---- the whole analysis --------------------------------------------------------------------------


def analyse(
    runs: Sequence[RunRecord], cards: CardSet, study: Study, *, resamples: int = 10_000
) -> dict[str, Any]:
    by_contract, found, loose = group_runs(runs, cards)
    registered = set(study.ids)
    outside = sorted(set(by_contract) - registered)
    for card_id in outside:  # a contract the study does not name is not part of it
        del by_contract[card_id]
    analysed = [r for rows in by_contract.values() for r in rows]
    out: dict[str, Any] = {
        "runs": len(analysed),
        "planned_runs": study.contracts * study.repeats,
        "contracts": len(by_contract),
        "contracts_registered": study.contracts,
        "unmatched_runs": loose,
        "outside_study": outside,
        "runs_per_contract": {c: len(r) for c, r in sorted(by_contract.items())},
    }
    if not by_contract:
        return out | {"verdict": None}
    out["verdict"] = verdict_block(by_contract, resamples)
    out["abstains"] = abstain_block(by_contract, resamples)
    out["tool_set"] = tool_block(by_contract, resamples)
    out["stability"] = stability_block(by_contract, found, resamples)
    out["attribution"] = attribution_block(by_contract)
    out["cache_state"] = dict(sorted(Counter(cache_state_of(r) for r in analysed).items()))
    out["time"] = time_summary(analysed)
    out["cost"] = cost_summary(analysed)
    out["decision"] = decide_r(out, study.decision, study.contracts)
    return out


def registration_notes(manifest: dict[str, Any]) -> list[str]:
    """Where the run-set's registration and the files as they are now differ, if they do."""
    recorded = manifest.get("noise_study") or {}
    if not recorded:
        return ["this run-set records no registration of the noise study"]
    notes = []
    if recorded.get("files_sha256") != files_sha256((STUDY_PATH, NOTE_PATH)):
        notes.append(
            "the registration files (noise-study.md, noise_study.toml) differ from what was "
            "registered when this run-set was made"
        )
    if recorded.get("uncommitted"):
        notes.append(f"registration files had uncommitted changes: {recorded['uncommitted']}")
    return notes


def build_document(
    manifest: dict[str, Any], analysis: dict[str, Any], runs: Sequence[RunRecord]
) -> dict[str, Any]:
    commits = sorted({r.environment["git_commit"] for r in runs if r.environment.get("git_commit")})
    results_manifest = {**manifest, "git_commits": commits, "kind": "noise"}
    return build_results(
        results_manifest,
        {
            "schema_version_noise": SCHEMA_VERSION,
            "what": "Run-to-run noise: ten contracts x repeats, compared with each other only.",
            "registration_notes": registration_notes(manifest),
            "noise": analysis,
        },
    )


# ---- the printed report --------------------------------------------------------------------------


def _contracts(n: int) -> str:
    return f"{n} contract{'' if n == 1 else 's'}"


def _iv(x: dict[str, Any] | None) -> str:
    if not x:
        return "n/a"
    return f"{x['value']:.2f} [{x['low']:.2f}-{x['high']:.2f}]"


def _s(row: dict[str, Any]) -> str:
    if not row.get("n"):
        return "n/a (no contract has two or more runs)"
    return (
        f"{row['k']} of {row['n']} contracts ({row['share']:.0%}); bootstrap "
        f"{row['bootstrap'][0]:.0%}-{row['bootstrap'][1]:.0%}, exact 95% "
        f"{row['exact_95'][0]:.0%}-{row['exact_95'][1]:.0%}"
    )


def render(doc: dict[str, Any]) -> str:
    a = doc["noise"]
    lines = [
        f"NOISE STUDY: {a['runs']} runs of {a['planned_runs']} planned, {a['contracts']} of "
        f"{a['contracts_registered']} contracts"
    ]
    for note in doc["registration_notes"]:
        lines.append(f"  note: {note}")
    if a.get("outside_study"):
        lines.append(f"  left out (not in the study): {', '.join(a['outside_study'])}")
    if a["unmatched_runs"]:
        lines.append(f"  {a['unmatched_runs']} run(s) match no card and were left out")
    if a.get("verdict") is None:
        return "\n".join([*lines, "  no runs to analyse"])
    v = a["verdict"]
    lines += [
        "",
        "Verdict (abstain counted as a verdict in the first line, apart in the others)",
        f"  differs in any verdict:        {_s(v['exact'])}",
        f"  differs, abstains left out:    {_s(v['substantive'])}",
        f"  flips warn/clear (direction):  {_s(v['direction'])}",
    ]
    for card_id, counts in v["flipped"].items():
        lines.append(f"    {card_id}: " + ", ".join(f"{n}x {x}" for x, n in sorted(counts.items())))
    b = a["abstains"]
    share = f" ({b['share'][0]:.0%} of runs)" if "share" in b else ""
    lines += [
        "",
        f"Abstains: {b['runs']} of {b['of']} runs{share} in {_contracts(b['contracts'])} "
        f"({b['contracts_mixed']} also had a run that did not abstain); ended because: "
        + (", ".join(f"{n}x {c}" for c, n in sorted(b["by_cause"].items())) or "n/a"),
        f"  runs that hit the tool-call cap: {b['hit_tool_cap_runs']} of {b['of']}",
    ]
    t = a["tool_set"]
    lines += ["", f"Tool set changed between runs: {_s(t['set_changed'])}"]
    for card_id, row in t["contracts"].items():
        if row["sometimes"]:
            varied = ", ".join(f"{k} in {n} of {row['runs']}" for k, n in row["sometimes"].items())
            lines.append(f"    {card_id}: calls {row['calls_min']}-{row['calls_max']}; {varied}")
    for name, rates in a["stability"].items():
        lines.append(
            f"  {name.replace('_', ' ')}: single run {_iv(rates['single_run'])}, majority "
            f"{_iv(rates['majority'])}, every run {_iv(rates['all_correct'])}; "
            f"{rates['stable_pass']} always, {rates['unstable']} sometimes, "
            f"{rates['stable_fail']} never"
        )
    lines += [
        "",
        "Why runs differ (pairs of runs of one contract; one contract's pairs are not independent)",
    ]
    for name in BUCKETS:
        row = a["attribution"]["buckets"][name]
        where = _contracts(row["contracts"])
        lines.append(
            f"  {name.replace('_', ' '):<30} {row['pairs']:>4} pairs in {where:>11}: "
            f"same inputs {row['same_inputs']}, different calls {row['calls_differ']}, "
            f"different results {row['results_differ']}, unknown {row['unknown']}"
        )
    pairs, by_tools = differing_pairs(a["attribution"])
    if pairs:
        lines.append(
            f"  of {pairs} pairs with a different verdict, {by_tools} had a different result for a "
            f"call both runs made (the tools), {pairs - by_tools} did not (the model)"
        )
    hot = {k: r for k, r in a["attribution"]["per_tool"].items() if r["differing"]}
    lines.append(
        "  tools whose result differed for an identical call: "
        + (", ".join(f"{k} {r['differing']}/{r['shared_calls']}" for k, r in hot.items()) or "none")
    )
    lines += [
        "",
        "Cache state of the runs: " + ", ".join(f"{k} {n}" for k, n in a["cache_state"].items()),
    ]
    for model, row in a["time"].items():
        e = row["end_to_end_s"]
        if e.get("n"):
            lines.append(
                f"Time ({model}, sequential): median {e['p50']} s, max {e['max']} s over {e['n']} "
                f"runs; {row['runs'] - row['used']} left out"
            )
        for state, s in row["by_cache_state"].items():
            lines.append(f"    {state}: median {s['p50']} s over {s['n']} runs")
    for model, row in a["cost"].items():
        lines.append(f"Cost ({model}): ${row['total_usd']} for {row['runs']} runs")
    d = a["decision"]
    lines += ["", "Decision rule for R (a proposal for Aryaan to approve; evals/noise-study.md)"]
    if d["level"] is None:
        lines.append(f"  not applied: {d['reason']}")
    else:
        lines += [
            f"  the study is {d['level']}: {d['direction_flips']} contract(s) flip direction, "
            f"{d['exact_flips']} differ in any verdict",
            f"  quick runs: {d['quick']}",
            f"  milestone runs: {d['milestone']}",
            f"  {d['floor']}",
        ]
        lines += [f"  OVERRIDE {o}" for o in d["overrides"]]
    return "\n".join(lines)
