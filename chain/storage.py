"""get_storage: one raw 32-byte storage slot of a contract, at the pinned block.

Storage has no types. The same 32 bytes can be a number, an address or the first letters of a
string, so the tool returns the bytes and says whether they are all zero; `as_address` is a
guess that fits when the top 12 bytes are zero (an owner or admin slot looks like that). What a
slot MEANS depends on the contract's layout, which the bytecode alone does not give. Slots for
proxy pointers are named in chain/slots.py and may be passed by name.
"""

from pydantic import BaseModel
from web3 import Web3

from chain.errors import ChainError
from chain.models import ToolResult
from chain.rpc import RpcClient
from chain.slots import PRESETS
from chain.tooling import run_tool

SOURCE = "rpc"


class StorageData(BaseModel, frozen=True):
    slot: str  # 0x hex, no leading zeros
    preset: str | None  # the name from chain/slots.py if the slot was asked for by name
    value: str  # the 32 bytes read, 0x hex
    is_zero: bool  # an unset slot reads as all zero bytes
    as_address: str | None  # a guess: the value read as an address, if it fits one


def parse_slot(raw: int | str) -> int:
    """A slot number: an int, or text in decimal or 0x hex. Or ChainError(invalid_slot)."""
    try:
        if isinstance(raw, bool):
            raise ValueError
        if isinstance(raw, int):
            slot = raw
        else:
            text = raw.strip()
            slot = int(text, 16) if text.lower().startswith("0x") else int(text)
    except (ValueError, AttributeError):
        slot = -1
    if not 0 <= slot < 2**256:
        raise ChainError(
            "invalid_slot", "Slot must be a number from 0 to 2^256 - 1 (decimal or 0x hex)."
        )
    return slot


def resolve_slot(raw: int | str) -> tuple[str | None, int]:
    """(preset name or None, slot). A preset name from chain/slots.py stands for its slot."""
    if isinstance(raw, str) and raw.strip().lower() in PRESETS:
        name = raw.strip().lower()
        return name, PRESETS[name]
    return None, parse_slot(raw)


def as_address(value: bytes) -> str | None:
    if len(value) == 32 and value[:12] == bytes(12) and any(value[12:]):
        return Web3.to_checksum_address(value[12:])
    return None


def get_storage(client: RpcClient, address: str, slot: int | str) -> ToolResult[StorageData]:
    """The tool. `slot` is a number (int, decimal or 0x text) or a preset name."""

    def work(checked: str) -> StorageData:
        preset, number = resolve_slot(slot)
        value = client.get_storage_at(checked, number)
        return StorageData(
            slot=hex(number),
            preset=preset,
            value=Web3.to_hex(value),
            is_zero=not any(value),
            as_address=as_address(value),
        )

    return run_tool(ToolResult[StorageData], client, address, SOURCE, work)
