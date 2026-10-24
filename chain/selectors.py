"""extract_selectors: the 4-byte function selectors a contract answers to, read from its bytecode.

Done with evmole, a maintained analyser, not a hand-written scan: a plain scan for PUSH4 finds every
4-byte constant in the code (error codes, selectors of calls the contract makes to others), while
evmole follows the function dispatcher. It returns IDs, not names; `lookup_selector` supplies names.

What it cannot see: functions reached by an unusual dispatcher, and the real functions of a proxy
(a proxy answers to few of its own; analyse the implementation too).
"""

import logging
import re
from importlib.metadata import version
from typing import Literal

import evmole
from pydantic import BaseModel
from web3 import Web3

from chain.errors import ChainError, scrub
from chain.models import Provenance, ToolResult

log = logging.getLogger(__name__)

MAX_CODE_BYTES = 100_000  # contracts are limited to 24,576 bytes; this leaves room

_HEX = re.compile(r"[0-9a-fA-F]*")
_SELECTOR = re.compile(r"[0-9a-f]{8}")
# Argument types as evmole writes them: `address,uint256`, `(uint256,bytes)[]`. It guesses them
# from untrusted code, so anything else is dropped rather than passed on.
_TYPES = re.compile(r"[A-Za-z0-9_,()\[\]]{0,400}")

Mutability = Literal["pure", "view", "nonpayable", "payable"]
_MUTABILITIES = {"pure", "view", "nonpayable", "payable"}


class SelectorInfo(BaseModel, frozen=True):
    selector: str  # 0x + 8 lowercase hex
    arguments: str | None  # e.g. "address,uint256"; a guess, None if it could not tell
    state_mutability: Mutability | None  # also a guess


class SelectorsData(BaseModel, frozen=True):
    code_hash: str
    code_size: int
    selectors: tuple[SelectorInfo, ...]  # sorted by selector


def parse_code(code: bytes | str) -> bytes:
    """Hex text (0x prefix optional) or bytes to non-empty bytes, or ChainError."""
    if isinstance(code, str):
        text = code.strip().removeprefix("0x").removeprefix("0X")
        if len(text) % 2 or not _HEX.fullmatch(text):
            raise ChainError(
                "invalid_bytecode", "Not valid hex: expected an even number of digits."
            )
        code = bytes.fromhex(text)
    if not code:
        raise ChainError(
            "invalid_bytecode",
            "The code is empty: an externally owned account, or a contract that was destroyed.",
        )
    if len(code) > MAX_CODE_BYTES:
        raise ChainError("invalid_bytecode", f"Code is longer than {MAX_CODE_BYTES} bytes.")
    return code


def extract_selectors(code: bytes) -> tuple[SelectorInfo, ...]:
    try:
        info = evmole.contract_info(code, selectors=True, arguments=True, state_mutability=True)
    except Exception as exc:
        log.warning("evmole failed: %s", scrub(repr(exc)))
        raise ChainError(
            "internal_error", "The bytecode analyser failed on this code; see the log."
        ) from None
    found = {}
    for function in info.functions or []:
        selector = str(function.selector).lower()
        if not _SELECTOR.fullmatch(selector):
            continue
        arguments = function.arguments
        mutability = function.state_mutability
        found["0x" + selector] = SelectorInfo(
            selector="0x" + selector,
            arguments=arguments
            if isinstance(arguments, str) and _TYPES.fullmatch(arguments)
            else None,
            state_mutability=mutability if mutability in _MUTABILITIES else None,
        )
    return tuple(found[s] for s in sorted(found))


def run_extract_selectors(code: bytes | str, provenance: Provenance) -> ToolResult[SelectorsData]:
    """The tool: `provenance` is the `get_code` result's (the code's chain, address and block);
    `source` is replaced with the analyser and its version."""
    stamped = provenance.model_copy(update={"source": f"evmole@{version('evmole')}"})
    try:
        raw = parse_code(code)
        data = SelectorsData(
            code_hash=Web3.to_hex(Web3.keccak(raw)),
            code_size=len(raw),
            selectors=extract_selectors(raw),
        )
    except ChainError as err:
        return ToolResult[SelectorsData](provenance=stamped, data=err.to_tool_error())
    return ToolResult[SelectorsData](provenance=stamped, data=data)
