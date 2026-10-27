"""get_code: a contract's runtime bytecode, read at the pinned block and saved by hash.

The bytes are what everything else reads (selectors, proxy checks, reference matching), and the
keccak256 hash is how a report cites them. The code store (Postgres, chain/db.py) keeps every
bytecode we read, forever: a hash names exactly one byte string, and the code at an address and
block never changes. It is a record, not a cache, so nothing expires.

What the store buys, plainly:
- a record of exactly which bytes a cited analysis used, even if the contract is later destroyed
  or the node prunes the block;
- one stored copy per distinct bytecode (a thousand clones share one);
- a repeat read of the same address and a settled block skips the `eth_getCode` call. It cannot
  skip it for `latest`: only the node can say what the code is at a new block.

The (address, block) shortcut is used only for blocks at least 64 behind the head, deep enough
that a reorganisation cannot change them. The record is written for every block we read.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel
from web3 import Web3

from chain.models import ToolResult, normalize_address
from chain.rpc import RpcClient
from chain.tooling import run_tool

log = logging.getLogger(__name__)

SOURCE = "rpc"
FINALITY_DEPTH = 64


class CodeStore(Protocol):
    def find(self, chain_id: int, address: str, block: int) -> tuple[str, bytes] | None:
        """(code hash, code) saved for this exact place, or None. A broken store returns None."""

    def save(self, chain_id: int, address: str, block: int, code_hash: str, code: bytes) -> None:
        """Keep the code and where it was seen. A broken store drops the write."""


@dataclass(frozen=True, slots=True)
class CodeRead:
    code: bytes
    code_hash: str  # keccak256 as 0x hex; for no code at all, the hash of the empty string
    from_store: bool


def code_hash_of(code: bytes) -> str:
    return Web3.to_hex(Web3.keccak(code))


def read_code(client: RpcClient, address: str, store: CodeStore | None = None) -> CodeRead:
    """The bytes the other tools work on. Raises ChainError."""
    address = normalize_address(address)
    block = client.pin()
    chain_id = client.chain_id
    head = client.head
    settled = head is not None and head - block >= FINALITY_DEPTH
    if store is not None and settled:
        saved = store.find(chain_id, address, block)
        if saved is not None:
            saved_hash, saved_code = saved
            if code_hash_of(saved_code) == saved_hash:
                return CodeRead(saved_code, saved_hash, True)
            log.warning("saved code does not match its hash; reading from the chain instead")
    code = client.get_code(address)
    code_hash = code_hash_of(code)
    if store is not None:
        store.save(chain_id, address, block, code_hash, code)
    return CodeRead(code, code_hash, False)


class CodeData(BaseModel, frozen=True):
    has_code: bool
    code_hash: str  # keccak256; cite and compare code by this
    size: int  # bytes
    from_store: bool  # read from the code store instead of the node
    code: str | None  # 0x hex, only when asked for


def get_code(
    client: RpcClient,
    address: str,
    store: CodeStore | None = None,
    *,
    include_code: bool = False,
) -> ToolResult[CodeData]:
    """The tool. The bytes are left out unless asked for: up to 24,576 of them is a lot of hex
    for an agent to read, and the hash and size say what they are."""

    def work(checked: str) -> CodeData:
        read = read_code(client, checked, store)
        return CodeData(
            has_code=bool(read.code),
            code_hash=read.code_hash,
            size=len(read.code),
            from_store=read.from_store,
            code=Web3.to_hex(read.code) if include_code else None,
        )

    return run_tool(ToolResult[CodeData], client, address, SOURCE, work)
