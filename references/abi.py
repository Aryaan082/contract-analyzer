"""Function signatures, selectors and hashes.

A selector is the first 4 bytes of keccak256 of a function's signature, e.g.
`transfer(address,uint256)` -> `0xa9059cbb`. Contract code holds selectors, never names.
"""

import re

from web3 import Web3

# `name(type,type)` with nested tuples and arrays, and nothing else: no spaces, no prose. Names
# from outside (explorers, selector databases) are untrusted, and this is the first filter.
_SIGNATURE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{0,63}\([A-Za-z0-9_$,()\[\]]{0,400}\)")
_SELECTOR = re.compile(r"(0x)?[0-9a-fA-F]{8}")
_HEX = re.compile(r"[0-9a-fA-F]*")


def keccak_hex(data: bytes) -> str:
    return Web3.to_hex(Web3.keccak(data))


def selector_of(signature: str) -> str:
    return Web3.to_hex(Web3.keccak(text=signature)[:4])


def is_signature(text: object) -> bool:
    return isinstance(text, str) and _SIGNATURE.fullmatch(text) is not None


def normalize_selector(raw: object) -> str:
    """`0xA9059CBB` or `a9059cbb` -> `0xa9059cbb`. Raises ValueError on anything else."""
    if not isinstance(raw, str) or not _SELECTOR.fullmatch(raw.strip()):
        shown = raw[:20] if isinstance(raw, str) else type(raw).__name__
        raise ValueError(f"Not a 4-byte selector: {shown!r}. Expected 8 hex characters.")
    return "0x" + raw.strip().removeprefix("0x").lower()


def parse_hex(text: str) -> bytes:
    """Hex text (0x prefix optional) to bytes. Raises ValueError on anything but hex digits."""
    cleaned = text.strip().removeprefix("0x").removeprefix("0X")
    if len(cleaned) % 2 or not _HEX.fullmatch(cleaned):
        raise ValueError("Not valid hex: expected an even number of hex digits.")
    return bytes.fromhex(cleaned)


def _canonical_type(param: dict) -> str:
    kind = param["type"]
    if kind.startswith("tuple"):
        inner = ",".join(_canonical_type(c) for c in param["components"])
        return f"({inner}){kind.removeprefix('tuple')}"
    return kind


def abi_signature(entry: dict) -> str:
    """Canonical signature of one ABI `function` entry, tuples expanded."""
    return f"{entry['name']}({','.join(_canonical_type(p) for p in entry.get('inputs', []))})"
