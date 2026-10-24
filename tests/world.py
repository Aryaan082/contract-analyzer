"""A scripted chain for the registry and identify tests: contracts that answer chosen calls.

Stands in for RpcClient. A call to a function nobody registered reverts, as it would on a contract
that does not have it.
"""

from collections.abc import Callable

from web3 import Web3

from chain.models import Provenance
from chain.rpc import CallOutcome

codec = Web3().codec


def selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


def word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def address_word(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(address[2:])


def args_of(*pairs: tuple[str, object]) -> bytes:
    """ABI-encoded call arguments, as the tool sends them."""
    return codec.encode([t for t, _ in pairs], [v for _, v in pairs])


Answer = bytes | Callable[[bytes], bytes | None]


class World:
    def __init__(self, chain_id: int = 1) -> None:
        self.chain_id, self.block = chain_id, 100
        self.chain = "ethereum"
        self.codes: dict[str, bytes] = {}
        self.storage: dict[tuple[str, int], bytes] = {}
        self._answers: dict[tuple[str, bytes], Answer] = {}
        self.calls: list[tuple[str, bytes]] = []

    def on(self, address: str, signature: str, answer: Answer) -> None:
        """`answer` is the returned bytes, or a function of the encoded arguments that returns
        bytes, or None to revert."""
        self._answers[(address.lower(), selector(signature))] = answer

    def pin(self) -> int:
        return self.block

    def get_code(self, address: str) -> bytes:
        return self.codes.get(address.lower(), b"")

    def get_storage_at(self, address: str, slot: int) -> bytes:
        return self.storage.get((address.lower(), slot), bytes(32))

    def call(self, to: str, data: bytes) -> CallOutcome:
        self.calls.append((to, bytes(data)))
        answer = self._answers.get((to.lower(), bytes(data[:4])))
        if callable(answer):
            answer = answer(bytes(data[4:]))
        if answer is None:
            return CallOutcome(False, b"")
        return CallOutcome(True, answer)

    def provenance(self, address=None, *, source="rpc") -> Provenance:
        return Provenance(
            chain=self.chain,
            chain_id=self.chain_id,
            address=address,
            block=self.block,
            source=source,
        )
