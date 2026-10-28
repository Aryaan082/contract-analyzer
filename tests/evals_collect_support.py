"""A scripted chain for the collection tests: logs, bytecode and supplies are plain dictionaries."""

from evals.collect.chaindata import RawLog, address_topic
from evals.collect.rules import TRANSFER_TOPIC, ZERO

PAIR = "0x" + "9a" * 20
TOKEN = "0x" + "7e" * 20
CREATOR = "0x" + "c1" * 20
LP = "0x" + "11" * 20

# PUSH4 of transfer(address,uint256) and balanceOf(address): enough to look like an ERC-20.
ERC20_CODE = bytes.fromhex("63a9059cbb" + "6370a08231" + "00")
MINT_CODE = ERC20_CODE + bytes.fromhex("6340c10f19")
BLACKLIST_CODE = ERC20_CODE + bytes.fromhex("63f9f92be4")  # blacklist(address): see rules.py


def wallet(number: int) -> str:
    return "0x" + format(number, "040x")


def raw(block: int, topics: list[str], data: str = "0x" + "00" * 32, tx: str = "") -> RawLog:
    return RawLog(block, tx or f"0x{block:064x}", tuple(topics), data)


def transfer(block: int, sender: str, receiver: str, value: int = 1, tx: str = "") -> RawLog:
    return raw(
        block,
        [TRANSFER_TOPIC, address_topic(sender), address_topic(receiver)],
        "0x" + format(value, "064x"),
        tx,
    )


class FakeChain:
    def __init__(self) -> None:
        self.table: dict[tuple[str, str], list[RawLog]] = {}
        self.codes: dict[str, bytes] = {}
        self.supplies: dict[tuple[str, int], int] = {}
        self.calls: list[tuple[str, str]] = []

    def add(self, address: str, topic0: str, *logs: RawLog) -> None:
        self.table.setdefault((address.lower(), topic0), []).extend(logs)

    def logs(self, address, topic0, start, end, *, limit, topic1=None):
        self.calls.append((address.lower(), topic0))
        rows = [
            row
            for row in self.table.get((address.lower(), topic0), [])
            if start <= row.block <= end and (topic1 is None or row.topics[1] == topic1)
        ]
        rows.sort(key=lambda r: r.block)
        return rows[:limit], len(rows) > limit

    def code(self, address: str) -> bytes:
        return self.codes.get(address.lower(), b"")

    def total_supply(self, token: str, block: int) -> int | None:
        return self.supplies.get((token.lower(), block))


def trading(chain: FakeChain, buyers: int, sellers: int, *, start: int = 1_001, pair: str = PAIR):
    """Buyers receive from the pair, sellers send to it; wallets are numbered from 1000."""
    block = start
    for number in range(buyers):
        chain.add(TOKEN, TRANSFER_TOPIC, transfer(block, pair, wallet(1000 + number)))
        block += 1
    for number in range(sellers):
        chain.add(TOKEN, TRANSFER_TOPIC, transfer(block, wallet(1000 + number), pair))
        block += 1


def creator_mint(chain: FakeChain) -> None:
    chain.add(TOKEN, TRANSFER_TOPIC, transfer(10, ZERO, CREATOR, 10**18))
