"""Structured failures.

Raw errors from web3, requests or a node are never shown to the agent: they can carry the RPC
URL (which holds an API key) and, for reverts, text chosen by whoever wrote the contract. Every
failure is classified into one of a few codes with a message written here.
"""

import re
from typing import Literal

from pydantic import BaseModel
from web3.exceptions import Web3RPCError

ErrorCode = Literal[
    "chain_not_configured",
    "invalid_address",
    "invalid_block",
    "invalid_slot",
    "invalid_bytecode",
    "invalid_selector",
    "invalid_amount",
    "unknown_reference",
    "library_invalid",
    "rpc_unreachable",
    "rpc_error",
    "state_unavailable",
    "internal_error",
]


class ErrorDetail(BaseModel, frozen=True):
    code: ErrorCode
    message: str


class ToolError(BaseModel, frozen=True):
    """What a tool puts in `data` when it fails."""

    error: ErrorDetail


class ChainError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_tool_error(self) -> ToolError:
        return ToolError(error=ErrorDetail(code=self.code, message=self.message))


# Substrings that nodes use when they no longer hold the state for a block. Taken from common
# client messages; an unrecognised message falls through to `rpc_error`, which is still an error.
# "header not found" is safe to read as pruned only because RpcClient checks that a requested
# block is not past the chain head before reading.
_PRUNED_MARKERS = (
    "missing trie node",
    "header not found",
    "state is not available",
    "state not available",
    "historical state",
    "pruned",
)

_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s'\"<>)]+", re.IGNORECASE)


def scrub(text: str) -> str:
    """Remove URLs from text bound for a log. For humans only, never the agent."""
    return _URL.sub("<url>", text)


def classify(exc: BaseException) -> ChainError:
    if isinstance(exc, ChainError):
        return exc
    if isinstance(exc, Web3RPCError):
        error = (exc.rpc_response or {}).get("error")
        error = error if isinstance(error, dict) else {}
        if any(marker in str(error.get("message", "")).lower() for marker in _PRUNED_MARKERS):
            return ChainError(
                "state_unavailable",
                "The RPC node no longer has state for this block. An archive-capable RPC is "
                "needed to read old blocks. Not falling back to the latest block.",
            )
        rpc_code = error.get("code")
        suffix = f" (JSON-RPC code {rpc_code})" if isinstance(rpc_code, int) else ""
        return ChainError("rpc_error", f"The RPC node returned an error{suffix}.")
    if isinstance(exc, OSError):
        # requests' ConnectionError, Timeout and HTTPError are all OSErrors.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int):
            return ChainError("rpc_error", f"The RPC node responded with HTTP {status}.")
        return ChainError("rpc_unreachable", "Could not reach the RPC node after retrying.")
    return ChainError("internal_error", "Unexpected internal error; see the log.")
