"""Checks on the committed detection set (evals/datasets/contracts*.toml): offline, no chain.

They guard the things that would quietly spoil a number: a code family on both sides of the split, a
split that no longer follows its rule, a label with no source, rules that changed after the labels
were made, and the held-out list leaking into the tooling that dev work uses.
"""

import re
import tomllib
from collections import Counter
from pathlib import Path

import pytest

from evals.cards import load_cards
from evals.collect.rules import HASH_PATH, load_rules, rules_hash
from evals.collect.scan import split_key
from evals.collect.split import (
    CLASS_ORDER,
    TOOL_USE_QUOTAS,
    Item,
    assign,
    tool_use_set,
    trajectory_of,
)
from evals.runs import parse_trace
from evals.score import format_report, score
from tests.evals_support import make_trace

ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "evals" / "datasets"
DEV, HELD, DISPUTED = (
    DATASETS / n for n in ("contracts.toml", "contracts_heldout.toml", "disputed.toml")
)
RULES = load_rules()


def rows(path: Path) -> list[dict]:
    return tomllib.loads(path.read_text()).get("contract", [])


def meta(path: Path) -> dict:
    return {k: v for k, v in tomllib.loads(path.read_text()).items() if k != "contract"}


DEV_ROWS, HELD_ROWS, DISPUTED_ROWS = rows(DEV), rows(HELD), rows(DISPUTED)
ALL = DEV_ROWS + HELD_ROWS + DISPUTED_ROWS


def count(rows_: list[dict], klass: str) -> int:
    return sum(r["class"] == klass for r in rows_)


# -- shape ---------------------------------------------------------------------------------------


def test_the_set_has_the_four_classes_in_useful_numbers():
    both = DEV_ROWS + HELD_ROWS
    assert count(both, "flagged") >= MINIMUM["flagged"]
    assert count(both, "benign") >= MINIMUM["benign"]
    assert count(both, "grey") >= MINIMUM["grey"]
    assert count(both, "beyond_reach") >= MINIMUM["beyond_reach"]
    assert len(DISPUTED_ROWS) == 13


def test_each_file_states_its_own_counts_and_they_are_right():
    for path, items in ((DEV, DEV_ROWS), (HELD, HELD_ROWS), (DISPUTED, DISPUTED_ROWS)):
        stated = meta(path)["counts"]
        assert stated == dict(Counter(r["class"] for r in items)), path.name


def test_ids_and_addresses_are_unique_across_every_file():
    ids = [r["id"] for r in ALL]
    addresses = [(r["chain"], r["address"]) for r in ALL]
    assert len(set(ids)) == len(ids) and len(set(addresses)) == len(addresses)
    assert all(r["address"] == r["address"].lower() for r in ALL)


def test_the_files_load_with_the_scorers_card_reader():
    for path in (DEV, HELD, DISPUTED):
        assert len(load_cards(path)) == len(rows(path))


# -- every label has a reason --------------------------------------------------------------------


def test_every_card_has_a_source_a_written_reason_and_a_label_source():
    for r in ALL:
        assert r.get("source", "").strip(), r["id"]
        assert len(r.get("reason", "").strip()) > 20, r["id"]
        assert r.get("label_source", "").strip(), r["id"]
        assert r.get("group", "").strip(), r["id"]


def test_flagged_cards_say_which_reason_they_are_flagged_for():
    for r in DEV_ROWS + HELD_ROWS:
        if r["class"] == "flagged":
            assert r.get("reasons"), r["id"]


def test_the_incidents_are_attributed_and_carry_no_exploit_text():
    text = (DATASETS / "incidents.toml").read_text()
    assert "Apache License 2.0" in text and "Copyright 2022-2026 DeFiHackLabs" in text
    assert not re.search(r"0x[0-9a-fA-F]{100,}", text), "a long hex blob (calldata?) is not allowed"
    licence = DATASETS / "licenses" / "DeFiHackLabs-LICENSE"
    assert "Apache License" in licence.read_text()
    for r in ALL:
        if r["group"] == "incident":
            assert "DeFiHackLabs" in r["source"] and "Apache-2.0" in r["source"]


# -- the rules ------------------------------------------------------------------------------------


def test_the_rules_file_still_has_the_hash_every_file_recorded():
    recorded = HASH_PATH.read_text().strip()
    assert rules_hash() == recorded
    for path in (DEV, HELD, DISPUTED):
        assert meta(path)["rules_sha256"] == recorded, path.name


# -- the split -----------------------------------------------------------------------------------


def families(rows_: list[dict]) -> set[str]:
    return {r["code_hash"] for r in rows_ if r.get("code_hash")}


def test_no_code_family_is_on_both_sides_of_the_split():
    dev = families(DEV_ROWS + DISPUTED_ROWS)
    assert not dev & families(HELD_ROWS)
    for r in HELD_ROWS:
        assert r["split"] == "heldout"
    for r in DEV_ROWS + DISPUTED_ROWS:
        assert r["split"] == "dev"


def test_one_card_per_scanned_family():
    scanned = [r["code_hash"] for r in DEV_ROWS + HELD_ROWS if r["group"] == "obscure"]
    assert len(scanned) == len(set(scanned))


def split_items() -> list[Item]:
    disputed = families(DISPUTED_ROWS)
    return [
        Item(
            r["id"],
            r["class"],
            r["chain"],
            r["address"],
            r.get("code_hash", ""),
            dev_only=r["group"] == "existing" or r.get("code_hash", "") in disputed,
        )
        for r in DEV_ROWS + HELD_ROWS
    ]


def test_the_split_is_what_the_written_rule_gives():
    sides = assign(split_items(), RULES.heldout_share)
    recorded = {r["id"]: r["split"] for r in DEV_ROWS + HELD_ROWS}
    assert sides == recorded


def test_the_eight_existing_contracts_are_dev_only():
    existing = [r for r in DEV_ROWS + HELD_ROWS if r["group"] == "existing"]
    assert len(existing) == 8 and all(r["split"] == "dev" for r in existing)


def test_each_class_has_about_a_third_held_out():
    for klass in CLASS_ORDER:
        total = count(DEV_ROWS + HELD_ROWS, klass)
        held = count(HELD_ROWS, klass)
        assert held <= round(total / 3) + 1, klass


def test_the_split_key_of_each_card_is_the_documented_hash():
    sample = DEV_ROWS[0]
    assert split_key(sample["chain"], sample["address"]).startswith(
        __import__("hashlib")
        .sha256(f"{sample['chain']}:{sample['address']}".encode())
        .hexdigest()[:8]
    )


# -- the tool-use set -----------------------------------------------------------------------------


def test_the_tool_use_set_is_twenty_dev_contracts_covering_every_class_and_trajectory():
    chosen = [r for r in DEV_ROWS if r.get("tool_use")]
    assert len(chosen) == RULES.tool_use_size
    assert not any(r.get("tool_use") for r in HELD_ROWS + DISPUTED_ROWS)
    assert {r["class"] for r in chosen} >= {"flagged", "benign", "grey", "beyond_reach"}
    seen = {
        trajectory_of(r["class"], tuple(r.get("traits", ())), tuple(r.get("reasons", ())))
        for r in chosen
    }
    assert {name for name, _ in TOOL_USE_QUOTAS} - seen <= TRAJECTORIES_MAY_BE_MISSING


def test_the_tool_use_set_follows_its_rule():
    pool = [
        (
            r["id"],
            trajectory_of(r["class"], tuple(r.get("traits", ())), tuple(r.get("reasons", ()))),
            split_key(r["chain"], r["address"]),
        )
        for r in DEV_ROWS
    ]
    assert tool_use_set(pool, RULES.tool_use_size) == {
        r["id"] for r in DEV_ROWS if r.get("tool_use")
    }


# -- dates and blocks -----------------------------------------------------------------------------


def test_new_tokens_are_pinned_inside_the_scan_windows_with_thirty_days_after():
    for r in DEV_ROWS + HELD_ROWS:
        if r["group"] == "obscure":
            assert (
                RULES.first_launch
                <= r["block"]
                <= RULES.last_launch + RULES.liquidity_within + RULES.pinned_after + 1
            )
            assert r["block"] + RULES.window <= RULES.latest_end


def test_incident_cards_are_pinned_one_block_before_a_july_to_september_attack():
    incidents = tomllib.loads((DATASETS / "incidents.toml").read_text())["incident"]
    by_address = {i["address"]: i for i in incidents}
    cards = [r for r in DEV_ROWS + HELD_ROWS if r["group"] == "incident"]
    assert len(cards) == len(incidents)
    for r in cards:
        row = by_address[r["address"]]
        assert RULES.incident_first <= row["date"] <= RULES.incident_last
        assert r["block"] == row["attack_block"] - RULES.pin_before_attack


# -- the labels are all unreviewed, and the report says so -----------------------------------------


def test_every_label_is_unreviewed_and_the_report_says_how_many():
    for r in ALL:
        assert r.get("reviewed_by", "") == "", r["id"]
    cards = load_cards(DEV)
    assert cards.unreviewed() == (len(cards), len(cards))
    first = cards.cards[0]
    trace = parse_trace(make_trace(address=first.address, block=first.block))
    shown = format_report(score([trace], cards, split="dev"))
    assert f"{len(cards)} of {len(cards)} unreviewed" in shown


# -- the held-out list stays out of the way --------------------------------------------------------


def test_nothing_that_dev_work_uses_names_the_held_out_file():
    allowed = {"build.py", "cli.py", "log.py"}
    offenders = [
        str(path.relative_to(ROOT))
        for folder in ("agent", "chain", "references", "knowledge", "retrieval", "evals")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if "contracts_heldout" in path.read_text()
        and not (path.parent.name == "collect" and path.name in allowed)
    ]
    assert offenders == []


def test_the_dev_files_hold_no_held_out_card_and_the_log_lists_no_held_out_contract():
    assert all(r["split"] == "dev" for r in DEV_ROWS)
    log = (DATASETS / "collection_log.md").read_text()
    for r in HELD_ROWS:
        assert r["address"] not in log and r["address"][2:10] not in log


@pytest.mark.parametrize("path", [DEV, HELD, DISPUTED])
def test_a_file_is_not_hand_edited_header_says_so(path):
    assert "Do not edit by hand" in path.read_text()


# The counts of the first build (collection_log.md). A card removed by accident fails these; adding
# cards by a rebuild does not.
MINIMUM = {"flagged": 5, "benign": 17, "grey": 6, "beyond_reach": 10}
TRAJECTORIES_MAY_BE_MISSING: set[str] = set()
