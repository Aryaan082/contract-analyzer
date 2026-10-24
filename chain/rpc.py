"""Read-only JSON-RPC client pinned to one block.

Every run resolves `latest` once to a block number and uses that number for every read, so all
evidence from one analysis comes from the same chain state and can be cited by block.
"""

import logging
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass

from web3 import HTTPProvider, Web3
from web3.exceptions import ContractLogicError
from web3.providers.base import BaseProvider

from chain.config import chain_url, configured_chains
from chain.errors import ChainError, classify, scrub
from chain.models import Provenance, normalize_address

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10


def build_provider(url: str) -> BaseProvider:
    # web3's HTTPProvider already retries connection errors, timeouts and HTTP errors on
    # read-only methods (5 tries, backoff 0.125s doubling). JSON-RPC errors, such as a pruned
    # block, arrive as a response and are not retried. Its default timeout is 30s; across five
    # retries that is too long to wait on a dead node.
    return HTTPProvider(url, request_kwargs={"timeout": REQUEST_TIMEOUT_SECONDS})


@dataclass(frozen=True, slots=True)
class BlockInfo:
    number: int
    timestamp: int
    base_fee: int  # wei; 0 on chains without a base fee


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """An `eth_call`. A revert is an outcome, not an error: `ok` is False and `data` holds the
    revert data (empty for a bare revert). Revert text is chosen by the contract, so it is never
    shown to the agent as is."""

    ok: bool
    data: bytes


def _revert_bytes(err: ContractLogicError) -> bytes:
    data = getattr(err, "data", None)
    if isinstance(data, str) and data.startswith("0x"):
        try:
            return bytes.fromhex(data[2:])
        except ValueError:
            pass
    return b""


@contextmanager
def _guard() -> Iterator[None]:
    """Turn anything raised by web3 into a ChainError with a message we wrote."""
    try:
        yield
    except Exception as exc:
        err = classify(exc)
        level = logging.WARNING if err.code == "internal_error" else logging.DEBUG
        log.log(level, "rpc call failed (%s): %s", err.code, scrub(repr(exc)))
        raise err from None


class RpcClient:
    def __init__(self, chain: str, w3: Web3, block: int | None = None) -> None:
        if block is not None and block < 0:
            raise ChainError("invalid_block", "Block number must not be negative.")
        self.chain = chain
        self._w3 = w3
        self._requested_block = block
        self._chain_id: int | None = None
        self._block: int | None = None
        self._fork_url: str | None = None

    @classmethod
    def connect(cls, chain: str, env: Mapping[str, str], block: int | None = None) -> "RpcClient":
        url = chain_url(chain, env)
        client = cls(chain, Web3(build_provider(url)), block)
        client._fork_url = url
        return client

    @property
    def fork_url(self) -> str | None:
        """The node's URL, for Foundry to fork from. It holds the provider's API key: hand it only
        to the sandbox's child process, never to a log, a command line or the agent."""
        return self._fork_url

    @property
    def chain_id(self) -> int:
        """The id the node reports about itself, fetched once."""
        if self._chain_id is None:
            with _guard():
                self._chain_id = self._w3.eth.chain_id
        return self._chain_id

    @property
    def block(self) -> int:
        """The pinned block. `latest` is resolved once; an explicit block must exist."""
        if self._block is None:
            with _guard():
                head = self._w3.eth.block_number
            if self._requested_block is None:
                self._block = head
            elif self._requested_block > head:
                raise ChainError(
                    "invalid_block",
                    f"Block {self._requested_block} is after the chain head ({head}).",
                )
            else:
                self._block = self._requested_block
        return self._block

    def pin(self) -> int:
        """Resolve chain id and block now and return the block. Every read calls this first."""
        _ = self.chain_id
        return self.block

    def provenance(self, address: str | None = None, *, source: str = "rpc") -> Provenance:
        """Provenance from what is already known. Makes no network call, so it is safe to use
        when building a failure result."""
        return Provenance(
            chain=self.chain,
            chain_id=self._chain_id,
            address=address,
            block=self._block,
            source=source,
        )

    def get_code(self, address: str) -> bytes:
        address = normalize_address(address)
        block = self.pin()
        with _guard():
            return bytes(self._w3.eth.get_code(address, block))

    def get_storage_at(self, address: str, slot: int) -> bytes:
        address = normalize_address(address)
        block = self.pin()
        with _guard():
            return bytes(self._w3.eth.get_storage_at(address, slot, block))

    def block_info(self) -> BlockInfo:
        block = self.pin()
        with _guard():
            header = self._w3.eth.get_block(block)
        return BlockInfo(
            number=block,
            timestamp=int(header["timestamp"]),
            base_fee=int(header.get("baseFeePerGas") or 0),
        )

    def call(self, to: str, data: bytes) -> CallOutcome:
        """A read-only `eth_call` at the pinned block. It runs on the node and changes nothing."""
        to = normalize_address(to)
        block = self.pin()
        with _guard():
            try:
                return CallOutcome(True, bytes(self._w3.eth.call({"to": to, "data": data}, block)))
            except ContractLogicError as err:
                return CallOutcome(False, _revert_bytes(err))


def probe_chains(env: Mapping[str, str]) -> dict[str, tuple[int | None, ChainError | None]]:
    """Ask every configured chain's node for its chain id, in parallel. Maps name to
    (chain_id, None) on success or (None, error) on failure."""

    def probe(name: str) -> tuple[int | None, ChainError | None]:
        try:
            return RpcClient.connect(name, env).chain_id, None
        except ChainError as err:
            return None, err

    names = list(configured_chains(env))
    if not names:
        return {}
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        return dict(zip(names, pool.map(probe, names), strict=True))
