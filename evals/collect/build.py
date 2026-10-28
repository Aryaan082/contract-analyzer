"""Turn the measurements and the curated lists into the dataset files.

Inputs: the scan cache (`measurements.jsonl`, `inspections.jsonl`, `fixed_facts.jsonl`), the written
rules, `incidents.toml` and the fixed contracts in `existing.py`. Outputs, in `evals/datasets/`:

- `contracts.toml`: the dev cards (what dev tooling loads);
- `contracts_heldout.toml`: the held-out cards, loaded only with `--held-out --declare`;
- `disputed.toml`: the 13 tokens where honeypot.is and our rehearsal disagreed (dev, apart);
- `collection_log.md`: counts only, never one line per held-out contract.
"""

import hashlib
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.collect.existing import DISPUTED, EXISTING, FAMOUS, GREY, PINNED, SELL_BLOCKED
from evals.collect.rules import DATASETS, Rules, rules_hash
from evals.collect.scan import Decision, Measurement, decide, split_key
from evals.collect.split import Item, assign, tool_use_set, trajectory_of
from evals.collect.store import Table
from evals.collect.tomlout import cards_file

BUILT = "2026-10-28"
CHAIN = "ethereum"
INCIDENTS = DATASETS / "incidents.toml"
FLAGGED_FACTS = {
    "honeypot-sell-blocked": [SELL_BLOCKED],
    "privileged-mint": ["mint"],
    "address-blacklist": ["blacklist"],
}


@dataclass
class Picked:
    measurement: Measurement
    decision: Decision
    family_size: int


def sample_order(rules: Rules, address: str) -> str:
    return hashlib.sha256(f"{rules.sample_salt}:{CHAIN}:{address}".encode()).hexdigest()


def choose(
    rules: Rules, measurements: list[Measurement], family_sizes: dict[str, int]
) -> tuple[list[Picked], Counter]:
    """One decisive token per code family, up to the targets. Returns them and the counts of what
    was left out and why."""
    left_out: Counter = Counter()
    by_family: dict[str, list[tuple[Measurement, Decision]]] = {}
    for m in measurements:
        by_family.setdefault(m.code_hash, []).append((m, decide(rules, m)))
    decisive: list[Picked] = []
    for family, members in by_family.items():
        labels = {d.label for _, d in members if d.label != "excluded"}
        for _, d in members:
            if d.label == "excluded":
                left_out[d.why] += 1
        if len(labels) > 1:
            left_out["mixed_family"] += sum(d.label != "excluded" for _, d in members)
            continue
        kept = [(m, d) for m, d in members if d.label != "excluded"]
        if not kept:
            continue
        kept.sort(key=lambda x: split_key(CHAIN, x[0].token))
        m, d = kept[0]
        left_out["same_family_as_a_kept_token"] += len(kept) - 1
        decisive.append(Picked(m, d, family_sizes.get(family, len(members))))
    picked: list[Picked] = []
    for label, target in (
        ("flagged", rules.target_new_flagged),
        ("benign", rules.target_new_benign),
    ):
        pool = sorted(
            (p for p in decisive if p.decision.label == label),
            key=lambda p: sample_order(rules, p.measurement.token),
        )
        picked += pool[:target]
        left_out[f"{label}_over_target"] += max(0, len(pool) - target)
    return picked, left_out


def token_card(rules: Rules, p: Picked) -> dict[str, Any]:
    m, d = p.measurement, p.decision
    seen = f"{m.buyers} different wallets bought and {m.sellers} sold"
    if d.label == "flagged":
        what = {
            "few_sellers": "almost none could sell",
            "power_used": "a power in the bytecode was used (supply grew by "
            f"{(m.minted_share or 0):.0%} through a mint function, "
            f"{m.blacklist_events or 0} blacklist events)",
        }[d.why]
    else:
        what = "a healthy share sold, the pool kept its liquidity, no supply growth or blacklist"
    days = rules.window // 7200
    return {
        "id": f"tok-{m.token[2:10]}",
        "chain": CHAIN,
        "address": m.token,
        "block": m.pinned,
        "klass": d.label,
        "reasons": list(d.reasons),
        "traits": ["token"],
        "facts": [f for r in d.reasons for f in FLAGGED_FACTS.get(r, [])],
        "label_source": "script:outcome-v1",
        "group": "obscure",
        "family_size": p.family_size,
        "code_hash": m.code_hash,
        "source": "Uniswap V2 PairCreated (WETH pair) in a scan window; counts from Etherscan "
        "Transfer logs; see label_rules.toml",
        "reason": f"In the {days} days after block {m.pinned}: {seen}; {what}.",
    }


def incident_cards(rules: Rules, facts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not INCIDENTS.exists():
        return []
    cards = []
    for row in tomllib.loads(INCIDENTS.read_text()).get("incident", []):
        address = row["address"].lower()
        fact = facts.get(address, {})
        traits = fact.get("traits", [])
        cards.append(
            {
                "id": f"inc-{row['name'].lower()}",
                "chain": CHAIN,
                "address": address,
                "block": row["attack_block"] - rules.pin_before_attack,
                "klass": "beyond_reach",
                "reasons": row.get("taxonomy", []),
                "traits": traits,
                "implementation": fact.get("implementation"),
                "min_not_checked": 2,
                "label_source": "incident-list",
                "group": "incident",
                "code_hash": fact.get("code_hash", ""),
                "source": f"DeFiHackLabs {row['poc']} (Apache-2.0, Copyright 2022-2026 "
                f"DeFiHackLabs); incident of {row['date']}",
                "reason": row["reason"],
            }
        )
    return cards


def fixed_cards(facts: dict[str, dict[str, Any]], famous_senders: int) -> list[dict[str, Any]]:
    """The eight existing cards as written, and the grey and famous cards with the traits,
    implementation and code family that `verify` read from identify_contract."""
    out = []
    for card in EXISTING + GREY + FAMOUS:
        fact = facts.get(card["address"], {})
        if card["group"] == "famous" and (fact.get("senders") or 0) < famous_senders:
            continue  # a famous token must pass its own sample, like every other label
        merged = dict(card)
        if card["group"] != "existing":
            merged["traits"] = fact.get("traits", [])
            merged["implementation"] = fact.get("implementation")
        merged["code_hash"] = fact.get("code_hash", "")
        out.append(merged)
    return out


def disputed_cards(facts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"disputed-{address[2:10]}",
            "chain": CHAIN,
            "address": address,
            "block": PINNED,
            "klass": "grey",
            "split": "dev",
            "min_not_checked": 1,
            "label_source": "disagreement",
            "group": "disputed",
            "code_hash": facts.get(address, {}).get("code_hash", ""),
            "source": "honeypot.is (queried once on 2026-10-26, not re-queried) against our own "
            "sell rehearsal at the pinned block",
            "reason": f"honeypot.is said honeypot (sell tax {tax}%); our rehearsal said: {ours}. "
            "Neither opinion is the label; reported apart, not scored.",
        }
        for address, tax, ours in DISPUTED
    ]


def read_facts(cache: Path) -> dict[str, dict[str, Any]]:
    path = cache / "fixed_facts.jsonl"
    return {a: row for a, row in Table(path).rows.items()} if path.exists() else {}


def dev_only(card: dict[str, Any]) -> bool:
    return card.get("group") == "existing"


def assemble(
    rules: Rules, cache: Path, picked: list[Picked]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts = read_facts(cache)
    cards = fixed_cards(facts, rules.famous_senders) + [token_card(rules, p) for p in picked]
    cards += incident_cards(rules, facts)
    disputed_hashes = {facts.get(a, {}).get("code_hash", "") for a, _, _ in DISPUTED} - {""}
    items = [
        Item(
            c["id"],
            c["klass"],
            c["chain"],
            c["address"],
            c.get("code_hash", ""),
            dev_only=dev_only(c) or c.get("code_hash", "") in disputed_hashes,
        )
        for c in cards
    ]
    sides = assign(items, rules.heldout_share)
    for card in cards:
        card["split"] = sides[card["id"]]
    dev_rows = [
        (
            c["id"],
            trajectory_of(c["klass"], tuple(c.get("traits", ())), tuple(c.get("reasons", ()))),
            split_key(c["chain"], c["address"]),
        )
        for c in cards
        if c["split"] == "dev"
    ]
    chosen = tool_use_set(dev_rows, rules.tool_use_size)
    for card in cards:
        card["tool_use"] = card["id"] in chosen
    return [c for c in cards if c["split"] == "dev"], [c for c in cards if c["split"] == "heldout"]


def header(rules_sha: str, what: str, cards: list[dict[str, Any]]) -> str:
    counts = Counter(c["klass"] for c in cards)
    listing = ", ".join(f"{k} {counts[k]}" for k in sorted(counts))
    return (
        f"{what}\n"
        f"Built {BUILT} by `python -m evals.collect build`. Do not edit by hand: rebuild.\n"
        f"Label rules: evals/datasets/label_rules.toml, sha256 {rules_sha}\n"
        f"Cards in this file: {len(cards)} ({listing}).\n"
        "Every label here is a script's reading of chain history under the written rules, or a\n"
        "written rule applied to a named contract. Nobody has reviewed any of them\n"
        '(`reviewed_by` is empty); "agrees" means "agrees with the script", not with a person.'
    )


def write_all(rules: Rules, cache: Path, out: Path = DATASETS) -> dict[str, Any]:
    inspections = Table(cache / "inspections.jsonl")
    measurements = [Measurement(**r) for r in Table(cache / "measurements.jsonl").rows.values()]
    sizes = Counter(r["code_hash"] for r in inspections.rows.values() if r.get("code_hash"))
    picked, left_out = choose(rules, measurements, dict(sizes))
    dev, held = assemble(rules, cache, picked)
    sha = rules_hash()
    dev_text = cards_file(header(sha, "Detection and tool-use cards: the DEV split.", dev), dev)
    (out / "contracts.toml").write_text(_with_meta(dev_text, sha, dev))
    (out / "contracts_heldout.toml").write_text(
        _with_meta(
            cards_file(
                header(sha, "HELD-OUT cards. Dev tooling never loads this file.", held), held
            ),
            sha,
            held,
        )
    )
    disputed = disputed_cards(read_facts(cache))
    (out / "disputed.toml").write_text(
        _with_meta(
            cards_file(
                header(sha, "Disputed tokens: honeypot.is and our rehearsal disagreed.", disputed),
                disputed,
            ),
            sha,
            disputed,
        )
    )
    famous_kept = sum(c["group"] == "famous" for c in dev + held)
    return {
        "dev": dev,
        "heldout": held,
        "disputed": disputed,
        "crowd": sum(m.buyers >= rules.min_buyers for m in measurements),
        "famous_failed": len(FAMOUS) - famous_kept,
        "left_out": left_out,
        "picked": picked,
        "inspection_status": Counter(r["status"] for r in inspections.rows.values()),
        "measured": len(measurements),
        "sha": sha,
    }


def _with_meta(text: str, sha: str, cards: list[dict[str, Any]]) -> str:
    counts = Counter(c["klass"] for c in cards)
    meta = f'rules_sha256 = "{sha}"\nbuilt = "{BUILT}"\n'
    meta += "counts = " + "{ " + ", ".join(f"{k} = {v}" for k, v in sorted(counts.items())) + " }\n"
    marker = "\n[[contract]]"
    head, _, rest = text.partition(marker)
    return head.rstrip("\n") + "\n\n" + meta + marker + rest
