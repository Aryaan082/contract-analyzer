"""The fixed set of contracts the agent is demonstrated and evaluated on (contracts.toml).

Component 5's done criterion is a valid cited report for each of the five; Component 6 reuses the
same set so agent changes are measured on the same contracts. Each entry has the address, the chain,
the block the evidence is read at (so a run can be repeated), what a good report should say and how
the address was verified.

`contracts.toml` has a second group, the extra candidates (more honeypots), kept apart so the fixed
five never change. `honeypot_is_labels.json` is the one-off record of a second opinion used to
choose them; nothing here or anywhere else calls that service."""

import json
import tomllib

from pydantic import BaseModel

from agent.config import AGENT_DIR, CONTRACTS_FILE

HONEYPOT_LABELS_FILE = AGENT_DIR / "honeypot_is_labels.json"


class Contract(BaseModel, frozen=True):
    id: str
    label: str
    chain: str
    address: str
    block: int
    expect: str  # what a good report says, in plain words
    how_verified: str


class HoneypotIsRecord(BaseModel, frozen=True):
    """What honeypot.is said about a token on the one day it was asked (kept as static data)."""

    id: str
    token_name: str
    pair_created_block: int
    is_honeypot: bool
    simulation_success: bool
    honeypot_reason: str
    buy_tax_percent: float
    sell_tax_percent: float
    holders_analysed: int
    holders_failed: int


def _load(group: str) -> dict[str, Contract]:
    raw = tomllib.loads(CONTRACTS_FILE.read_text())
    return {c["id"]: Contract.model_validate(c) for c in raw.get(group, [])}


def load_contracts() -> dict[str, Contract]:
    """The fixed five."""
    return _load("contract")


def load_candidates() -> dict[str, Contract]:
    """The extra candidates: more honeypots, in a group of their own."""
    return _load("candidate")


def find_contract(contract_id: str) -> Contract | None:
    return {**load_contracts(), **load_candidates()}.get(contract_id)


def load_honeypot_labels() -> dict[str, HoneypotIsRecord]:
    """The recorded honeypot.is answers by checksummed address."""
    raw = json.loads(HONEYPOT_LABELS_FILE.read_text())
    return {a: HoneypotIsRecord.model_validate(r) for a, r in raw["records"].items()}
