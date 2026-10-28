"""`collection_log.md`: what the scan found and what the build kept, as counts. Nothing here is per
contract for the held-out split."""

from collections import Counter
from typing import Any

from evals.collect.build import BUILT
from evals.collect.rules import DATASETS, Rules


def _table(rows: list[tuple[str, ...]], header: tuple[str, ...]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def render(rules: Rules, result: dict[str, Any]) -> str:
    classes = ("flagged", "benign", "grey", "beyond_reach")
    cards = {"dev": result["dev"], "heldout": result["heldout"]}
    count = {
        (split, klass): sum(c["klass"] == klass for c in cs)
        for split, cs in cards.items()
        for klass in classes
    }
    by_class = [
        (
            klass,
            str(count[("dev", klass)]),
            str(count[("heldout", klass)]),
            str(count[("dev", klass)] + count[("heldout", klass)]),
        )
        for klass in classes
    ]
    groups = Counter(c["group"] for cs in cards.values() for c in cs)
    status = result["inspection_status"]
    windows = ", ".join(f"{a}-{b}" for a, b in rules.windows)
    total = sum(len(cs) for cs in cards.values())
    return f"""# Collection log (Component 6, part C)

Built {BUILT}. Label rules sha256 `{result["sha"]}`. Counts only: there is deliberately no line per
held-out contract here. Every label is unreviewed ({total} of {total} cards in the dev and held-out
files; the {len(result["disputed"])} disputed tokens are unreviewed as well).

## Scan

- Windows (block numbers, Ethereum): {windows}. Tokens paired with WETH on Uniswap V2 created in
  them: {sum(status.values())}.
- Inspection (could the token be a test case at all):
  {", ".join(f"{k} {v}" for k, v in sorted(status.items()))}.
- Measured after the family cap of {rules.max_family_scanned} per code family: {result["measured"]}.
- Of those, {result["crowd"]} had {rules.min_buyers} or more different buyers in 30 days (the rest
  traded too little to be called anything).
- Decided and left out: {", ".join(f"{k} {v}" for k, v in sorted(result["left_out"].items()))}.
- Famous tokens that failed their own sample rule ({rules.famous_senders} senders in
  {rules.famous_blocks} blocks) and were dropped: {result["famous_failed"]}.

## What was kept

{_table(by_class, ("class", "dev", "held-out", "total"))}

By group (dev and held-out together):
{", ".join(f"{k} {v}" for k, v in sorted(groups.items()))}.
Tool-use set: {sum(c.get("tool_use", False) for c in cards["dev"])} contracts, all dev.

## Rules history

`label_rules.toml` has its version history at the top. v1 was in force while the scan measured; no
label had been computed. Before any label was computed, a check on four known honeypots showed that
two of them trade normally for about 36 hours before stopping, so v2 requires sells still happening
in the last 10 days for a benign label. The measurements (counts, not labels) did not change; the
one number v2 needs was added afterwards for the tokens with enough buyers.

## What the outcome rules could not find

Flagged tokens other than the five existing ones: none came out of this scan (see the table). A
honeypot whose trap springs after the early trading looks healthy in Transfer events, and a failed
sell leaves no log, so "holders could not get out" is mostly invisible from history alone. The
existing honeypots (groklet, meowe, asasar, brick) are labelled by the two sources written on their
cards, not by this method, and are dev only.

Decision (Aryaan, 2026-10-28): keep the five existing flagged cards for now. Flagged recall is a
dev-only figure on five contracts, four of them honeypots whose verdict the validator forces to
high_risk; every results file says so in its notes (`evals/score.py`, `FEW_FLAGGED`).

## Limits worth remembering

- Counts of buyers and sellers are of different wallets in Transfer logs; a wallet using a router
  that is not on the list in `evals/collect/rules.py` is counted as itself or, if the router is the
  recipient, as one wallet. That makes tokens look less crowded, which sends them to "unclear" (left
  out), not into a class.
- Only the earliest {rules.max_transfer_logs} transfers after the pinned block were read for each
  token; counts are lower bounds when a token traded more than that.
- Very high sell taxes with working sells are not found by this outcome rule (brick is an example);
  they enter only as the existing contract.
- A token whose pool was drained by its first liquidity provider is left out, not labelled.
- Blacklist events outside the fixed list in `rules.py` are not seen.
- Etherscan's "verified source" is a current fact, not the state at the pinned block.
- Pinned blocks are an hour after the first liquidity; the 30-day windows all ended by
  {rules.latest_end} (2026-10-27 00:37 UTC).
"""


def write_log(rules: Rules, result: dict[str, Any]) -> None:
    (DATASETS / "collection_log.md").write_text(render(rules, result))
