"""Shared helpers for the references tests: committed fixtures and tiny byte builders."""

import json
from dataclasses import dataclass
from pathlib import Path

from references.abi import parse_hex, selector_of

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "references"


@dataclass(frozen=True)
class Fixture:
    name: str
    code: bytes
    selectors: frozenset[str]
    signatures: dict[str, str]  # selector -> signature


def load_fixture(name: str) -> Fixture:
    record = json.loads((FIXTURES / f"{name}.json").read_text())
    return Fixture(
        name=name,
        code=parse_hex(record["code"]),
        selectors=frozenset(record["selectors"]),
        signatures=record["signatures"],
    )


def selector_for(fixture: Fixture, signature_start: str) -> str:
    """The selector of the fixture function whose signature starts with the given text."""
    matches = [s for s, sig in fixture.signatures.items() if sig.startswith(signature_start)]
    assert len(matches) == 1, (signature_start, matches)
    return matches[0]


def sel(signature: str) -> str:
    return selector_of(signature)


def solidity_tail(hash_bytes: bytes = bytes(range(0xE0, 0x100))) -> bytes:
    """A metadata tail shaped like old solc's: {"bzzr0": <32 bytes>} then a 2-byte length."""
    assert len(hash_bytes) == 32
    cbor = b"\xa1" + b"\x65bzzr0" + b"\x58\x20" + hash_bytes
    return cbor + len(cbor).to_bytes(2, "big")
