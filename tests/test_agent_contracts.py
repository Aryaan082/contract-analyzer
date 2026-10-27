"""The fixed set of contracts and the extra candidates (agent/contracts.toml): well-formed, so
Component 6 can rely on them, and honeypot.is never becomes a dependency."""

from pathlib import Path

from agent.contracts import (
    find_contract,
    load_candidates,
    load_contracts,
    load_honeypot_labels,
)
from chain.models import normalize_address

ROOT = Path(__file__).resolve().parent.parent


def test_there_are_five_contracts_with_valid_addresses_and_pinned_blocks():
    contracts = load_contracts()
    assert len(contracts) == 5
    for contract_id, contract in contracts.items():
        assert contract.id == contract_id
        assert normalize_address(contract.address) == contract.address  # checksummed as written
        assert contract.block > 0 and contract.chain
        assert contract.expect.strip() and contract.how_verified.strip()


def test_the_extra_candidates_are_a_separate_well_formed_group():
    fixed, extra = load_contracts(), load_candidates()
    assert 2 <= len(extra) <= 4 and not set(fixed) & set(extra)
    for contract_id, contract in extra.items():
        assert contract.id == contract_id
        assert normalize_address(contract.address) == contract.address
        assert contract.block > 0 and contract.expect.strip() and contract.how_verified.strip()
    assert find_contract("brick") is extra["brick"] or find_contract("brick") == extra["brick"]
    assert find_contract("usdc") == fixed["usdc"] and find_contract("nope") is None


def test_addresses_are_distinct_across_both_groups():
    addresses = [c.address for c in (*load_contracts().values(), *load_candidates().values())]
    assert len(set(addresses)) == len(addresses)


def test_every_honeypot_has_its_recorded_honeypot_is_answer_as_static_data():
    labels = load_honeypot_labels()
    honeypots = [load_contracts()["groklet"], *load_candidates().values()]
    for contract in honeypots:
        record = labels[contract.address]
        assert record.id == contract.id
        assert record.is_honeypot and record.simulation_success
        assert record.holders_analysed >= record.holders_failed >= 0


def test_nothing_in_the_code_calls_honeypot_is():
    """It was used once, for labelling. Any call would name its API host; this test builds that
    name in two pieces so that it does not find itself. (Docstrings may mention the service.)"""
    needle = "api." + "honeypot" + ".is"
    offenders = [
        str(path.relative_to(ROOT))
        for folder in ("agent", "chain", "references", "knowledge", "retrieval", "evals", "tests")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if needle in path.read_text() and path.name != "test_agent_contracts.py"
    ]
    assert offenders == []
