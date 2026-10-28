"""What the collectors read from the chain: event logs, bytecode, a token's supply at a block.

`ChainData` is the small interface the rest of the package uses, so the scans can be tested with a
scripted fake. `LiveChain` is the real one. Logs come through Etherscan's log endpoint, because the
node plan allows `eth_getLogs` over only 10 blocks; code and `eth_call` come from the node. Both are
read-only. Errors are turned into `ChainError` messages written in `chain/`, so no URL or key can
end up in an exception text.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from web3 import Web3

from chain.config import chain_url
from chain.errors import ChainError, classify
from chain.explorer import ExplorerClient
from chain.rpc import build_provider
from evals.collect.rules import ZERO

PAGE = 1000  # Etherscan returns at most 1,000 logs a page and 10,000 in all for one query
ETHEREUM = 1
TOTAL_SUPPLY = "0x18160ddd"


@dataclass(frozen=True)
class RawLog:
    block: int
    tx: str
    topics: tuple[str, ...]
    data: str  # 0x-prefixed hex

    def word(self, index: int) -> int:
        """The index-th 32-byte word of the data, as an integer."""
        body = self.data[2:]
        piece = body[index * 64 : (index + 1) * 64]
        return int(piece, 16) if len(piece) == 64 else 0

    def topic_address(self, index: int) -> str:
        """An indexed address argument, lower case, from topic `index` (topic 0 is the event)."""
        return "0x" + self.topics[index][-40:].lower() if index < len(self.topics) else ZERO

    def data_address(self, index: int) -> str:
        return "0x" + format(self.word(index), "064x")[-40:]


class ChainData(Protocol):
    def logs(
        self,
        address: str,
        topic0: str,
        start: int,
        end: int,
        *,
        limit: int,
        topic1: str | None = None,
    ) -> tuple[list[RawLog], bool]:
        """The logs of `address` with this first topic in [start, end], oldest first, at most
        `limit` of them. The flag is True when the limit cut the list short."""
        ...

    def code(self, address: str) -> bytes: ...

    def total_supply(self, token: str, block: int) -> int | None: ...


def address_topic(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


class LiveChain:
    def __init__(self, explorer: ExplorerClient, w3: Web3) -> None:
        self._explorer, self._w3 = explorer, w3

    @classmethod
    def from_env(cls, env: Mapping[str, str], chain: str = "ethereum") -> "LiveChain":
        explorer = ExplorerClient.from_env(env)
        if explorer is None:
            raise ChainError("explorer_error", "ETHERSCAN_API_KEY is not set.")
        return cls(explorer, Web3(build_provider(chain_url(chain, env))))

    def logs(
        self,
        address: str,
        topic0: str,
        start: int,
        end: int,
        *,
        limit: int,
        topic1: str | None = None,
    ) -> tuple[list[RawLog], bool]:
        out: list[RawLog] = []
        page = 1
        while len(out) < limit and page * PAGE <= 10_000:
            query = {
                "fromBlock": str(start),
                "toBlock": str(end),
                "address": address,
                "topic0": topic0,
                "page": str(page),
                "offset": str(min(PAGE, limit - len(out))),
            }
            if topic1 is not None:
                query.update(topic1=topic1, topic0_1_opr="and")
            rows = self._explorer.get(ETHEREUM, "logs", "getLogs", **query)
            if not rows:
                return out, False
            for row in rows:
                out.append(
                    RawLog(
                        block=int(row["blockNumber"], 16),
                        tx=row["transactionHash"],
                        topics=tuple(t.lower() for t in row["topics"]),
                        data=row["data"],
                    )
                )
            if len(rows) < int(query["offset"]):
                return out, False
            page += 1
        return out[:limit], len(out) >= limit

    def code(self, address: str) -> bytes:
        try:
            return bytes(self._w3.eth.get_code(Web3.to_checksum_address(address)))
        except Exception as exc:
            raise classify(exc) from None

    def total_supply(self, token: str, block: int) -> int | None:
        try:
            out = self._w3.eth.call(
                {"to": Web3.to_checksum_address(token), "data": TOTAL_SUPPLY}, block
            )
        except Exception:
            return None  # a revert or a pruned block: unknown, not an error
        return int.from_bytes(bytes(out), "big") if len(out) == 32 else None
