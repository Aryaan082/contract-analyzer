"""Shared output shapes. Every tool returns `ToolResult`: provenance plus data."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel
from web3 import Web3

from chain.errors import ChainError, ToolError


def normalize_address(raw: object) -> str:
    """Return the EIP-55 checksummed form, or raise ChainError(invalid_address).

    Requires a 0x prefix. A mixed-case address with a wrong checksum is rejected, which catches
    typos (Web3.is_address alone does not check this). All-lowercase and all-uppercase carry no
    checksum and are accepted. ENS names are not resolved.
    """
    valid = isinstance(raw, str) and raw.startswith("0x") and Web3.is_address(raw)
    if valid:
        body = raw[2:]
        mixed_case = body != body.lower() and body != body.upper()
        valid = not mixed_case or Web3.is_checksum_address(raw)
    if not valid:
        shown = raw[:60] if isinstance(raw, str) else type(raw).__name__
        raise ChainError(
            "invalid_address",
            f"Not a valid EVM address: {shown!r}. Expected 0x plus 40 hex characters, with a "
            "correct checksum if mixed case.",
        )
    return Web3.to_checksum_address(raw)


def _validate_address(raw: object) -> str:
    try:
        return normalize_address(raw)
    except ChainError as err:
        raise ValueError(err.message) from None


Address = Annotated[str, AfterValidator(_validate_address)]
"""For tool input models: a checksummed address, or a validation error."""


class Provenance(BaseModel, frozen=True):
    """Where a result came from. Fields we never obtained are None (e.g. block, if the node was
    down before `latest` could be resolved)."""

    chain: str
    chain_id: int | None
    address: str | None
    block: int | None
    source: str


class ToolResult[T: BaseModel](BaseModel):
    provenance: Provenance
    data: T | ToolError
