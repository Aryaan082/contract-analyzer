"""Candidate tokens, measured from chain history, and the label the written rules give them.

Three steps, each pure given a `ChainData`:
- `discover`: tokens paired with WETH on Uniswap V2, created in the scan windows;
- `inspect`: can the token be a test case at all (code, ERC-20 shape, a pool with liquidity, a full
  30 days of outcome) and which code family is it;
- `measure`: how many different wallets bought and sold after the pinned block, whether the first
  liquidity provider took the WETH out, whether the supply grew through a mint function, whether a
  blacklist event was emitted.

`measure` records counts, not labels. `decide` turns the counts into a label using
`label_rules.toml`, so the same measurements can be re-labelled offline and the rules cannot be
tuned unseen.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from evals.collect.chaindata import ChainData, RawLog, address_topic
from evals.collect.rules import (
    BLACKLIST_SELECTORS,
    BLACKLIST_TOPICS,
    BURN_TOPIC,
    DEAD,
    ERC20_SELECTORS,
    MINT_SELECTORS,
    MINT_TOPIC,
    PAIR_CREATED_TOPIC,
    ROUTERS,
    SYNC_TOPIC,
    TRANSFER_TOPIC,
    UNISWAP_V2_FACTORY,
    WETH,
    ZERO,
    Rules,
)
from references.bytecode import push4_values, split_metadata

CLONE_PREFIX = bytes.fromhex("363d3d37")  # EIP-1167 minimal proxy
EIP1967_IMPLEMENTATION_SLOT = bytes.fromhex(
    "360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)
WINDOW_SLICE = 5_000  # blocks per log request while discovering


@dataclass(frozen=True)
class Candidate:
    token: str
    pair: str
    created: int
    token_is_token0: bool
    tx: str


def discover(data: ChainData, rules: Rules) -> list[Candidate]:
    """Every token paired with WETH on Uniswap V2 whose pair was created in a scan window."""
    found: dict[str, Candidate] = {}
    for first, last in rules.windows:
        for start in range(first, last, WINDOW_SLICE):
            end = min(start + WINDOW_SLICE - 1, last)
            logs, cut = data.logs(UNISWAP_V2_FACTORY, PAIR_CREATED_TOPIC, start, end, limit=10_000)
            if cut:
                raise RuntimeError(f"too many pairs in blocks {start}-{end}; use smaller slices")
            for log in logs:
                token0, token1, pair = (
                    log.topic_address(1),
                    log.topic_address(2),
                    log.data_address(0),
                )
                if WETH not in (token0, token1):
                    continue
                token = token1 if token0 == WETH else token0
                found.setdefault(token, Candidate(token, pair, log.block, token1 == WETH, log.tx))
    return sorted(found.values(), key=lambda c: (c.created, c.token))


def family_key(code: bytes) -> str:
    """SHA-256 of the runtime code with the compiler's metadata tail removed: copies built from the
    same source with the same compiler share it, whatever the metadata hash says."""
    body, _ = split_metadata(code)
    return hashlib.sha256(body).hexdigest()


def split_key(chain: str, address: str) -> str:
    return hashlib.sha256(f"{chain}:{address.lower()}".encode()).hexdigest()


@dataclass(frozen=True)
class Shape:
    erc20: bool
    proxy_like: bool
    has_mint: bool
    has_blacklist: bool


def shape_of(code: bytes) -> Shape:
    selectors = push4_values(code)
    return Shape(
        erc20=selectors >= ERC20_SELECTORS,
        proxy_like=code.startswith(CLONE_PREFIX) or EIP1967_IMPLEMENTATION_SLOT in code,
        has_mint=bool(MINT_SELECTORS & selectors),
        has_blacklist=bool(BLACKLIST_SELECTORS & selectors),
    )


@dataclass
class Inspection:
    token: str
    pair: str
    created: int
    token_is_token0: bool
    status: str  # "ok" or why the token cannot be a test case
    first_liquidity: int | None = None
    pinned: int | None = None
    code_hash: str = ""
    has_mint: bool = False
    has_blacklist: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def inspect(
    data: ChainData, rules: Rules, cand: Candidate, existing_hashes: Iterable[str] = ()
) -> Inspection:
    out = Inspection(cand.token, cand.pair, cand.created, cand.token_is_token0, "ok")
    code = data.code(cand.token)
    if not code:
        out.status = "no_code"
        return out
    shape = shape_of(code)
    out.code_hash = family_key(code)
    out.has_mint, out.has_blacklist = shape.has_mint, shape.has_blacklist
    if not shape.erc20:
        out.status = "not_erc20"
    elif shape.proxy_like:
        out.status = "proxy_or_clone"
    elif out.code_hash in set(existing_hashes):
        out.status = "family_of_existing_card"
    else:
        logs, _ = data.logs(
            cand.pair, SYNC_TOPIC, cand.created, cand.created + rules.liquidity_within, limit=1
        )
        if not logs:
            out.status = "no_liquidity"
        else:
            out.first_liquidity = logs[0].block
            out.pinned = logs[0].block + rules.pinned_after
            if out.pinned + rules.window > rules.latest_end:
                out.status = "window_incomplete"
    return out


@dataclass
class Measurement:
    token: str
    pair: str
    pinned: int
    code_hash: str
    has_mint: bool
    has_blacklist: bool
    buyers: int = 0
    sellers: int = 0
    transfers_read: int = 0
    truncated: bool = False
    creator: str | None = None
    lp_owner: str | None = None
    late_sellers: int | None = None  # different wallets that sold in the last `late_blocks`
    removed_share: float | None = None  # None: not worked out (too little trading)
    minted_share: float | None = None  # None: no mint function, or no supply to compare with
    blacklist_events: int | None = None  # None: no blacklist function
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def count_trading(
    logs: Iterable[RawLog], pair: str, token: str, ignore: Iterable[str | None]
) -> tuple[int, int]:
    """(distinct buyers, distinct sellers) among these token transfers. A buyer received tokens
    from the pair; a seller sent tokens to the pair. Left out: the pair, the token, the zero and
    burn addresses, routers, and whatever is in `ignore` (the creator, the liquidity provider)."""
    skip = {a for a in ignore if a} | {pair, token, ZERO, DEAD} | ROUTERS
    buyers, sellers = set(), set()
    for log in logs:
        sender, receiver = log.topic_address(1), log.topic_address(2)
        if sender == pair and receiver not in skip:
            buyers.add(receiver)
        elif receiver == pair and sender not in skip:
            sellers.add(sender)
    return len(buyers), len(sellers)


def _weth_word(token_is_token0: bool) -> int:
    return 1 if token_is_token0 else 0  # WETH is the other side of the pair


def removed_share(
    data: ChainData, rules: Rules, insp: Inspection, end: int
) -> tuple[str | None, float]:
    """(the first liquidity provider, the share of all WETH ever added as liquidity that this
    account took back out by sending its liquidity shares to the pair, after the pinned block)."""
    assert insp.pinned is not None
    shares, _ = data.logs(insp.pair, TRANSFER_TOPIC, insp.created, end, limit=2_000)
    owner = next(
        (
            s.topic_address(2)
            for s in shares
            if s.topic_address(1) == ZERO and s.topic_address(2) not in (ZERO, insp.pair)
        ),
        None,
    )
    if owner is None:
        return None, 0.0
    back = {
        s.tx
        for s in shares
        if s.block > insp.pinned and s.topic_address(1) == owner and s.topic_address(2) == insp.pair
    }
    word = _weth_word(insp.token_is_token0)
    mints, _ = data.logs(insp.pair, MINT_TOPIC, insp.created, end, limit=1_000)
    added = sum(m.word(word) for m in mints)
    if not back or not added:
        return owner, 0.0
    burns, _ = data.logs(insp.pair, BURN_TOPIC, insp.pinned + 1, end, limit=1_000)
    taken = sum(b.word(word) for b in burns if b.tx in back)
    return owner, taken / added


def late_sellers(
    data: ChainData, rules: Rules, insp: Inspection, ignore: Iterable[str | None]
) -> int:
    """Different wallets that sold in the last `late_blocks` of the outcome window: a token that
    traded and then died, or was trapped, has none."""
    assert insp.pinned is not None
    end = insp.pinned + rules.window
    logs, _ = data.logs(
        insp.token, TRANSFER_TOPIC, end - rules.late_blocks + 1, end, limit=rules.max_transfer_logs
    )
    return count_trading(logs, insp.pair, insp.token, ignore)[1]


def measure(data: ChainData, rules: Rules, insp: Inspection) -> Measurement:
    assert insp.pinned is not None
    start, end = insp.pinned + 1, insp.pinned + rules.window
    m = Measurement(
        insp.token, insp.pair, insp.pinned, insp.code_hash, insp.has_mint, insp.has_blacklist
    )
    first_mint, _ = data.logs(
        insp.token, TRANSFER_TOPIC, 0, insp.pinned, limit=1, topic1=address_topic(ZERO)
    )
    m.creator = first_mint[0].topic_address(2) if first_mint else None
    logs, m.truncated = data.logs(
        insp.token, TRANSFER_TOPIC, start, end, limit=rules.max_transfer_logs
    )
    m.transfers_read = len(logs)
    m.buyers, m.sellers = count_trading(logs, insp.pair, insp.token, [m.creator])
    if m.buyers < rules.min_buyers:
        return m  # too little trading to say anything: nothing more is measured
    m.lp_owner, m.removed_share = removed_share(data, rules, insp, end)
    m.buyers, m.sellers = count_trading(logs, insp.pair, insp.token, [m.creator, m.lp_owner])
    m.late_sellers = late_sellers(data, rules, insp, [m.creator, m.lp_owner])
    if insp.has_mint:
        mints, _ = data.logs(
            insp.token, TRANSFER_TOPIC, start, end, limit=500, topic1=address_topic(ZERO)
        )
        supply = data.total_supply(insp.token, insp.pinned)
        minted = sum(x.word(0) for x in mints)
        m.minted_share = minted / supply if supply else None
        if not supply:
            m.notes.append("supply at the pinned block unknown")
    if insp.has_blacklist:
        m.blacklist_events = sum(
            len(data.logs(insp.token, topic, start, end, limit=5)[0]) for topic in BLACKLIST_TOPICS
        )
    return m


@dataclass(frozen=True)
class Decision:
    label: str  # flagged | benign | excluded
    reasons: tuple[str, ...]  # taxonomy ids for flagged
    why: str  # the rule that decided it, in words


def decide(rules: Rules, m: Measurement) -> Decision:
    if m.buyers < rules.min_buyers:
        return Decision("excluded", (), "too_little_trading")
    if m.removed_share is not None and m.removed_share >= rules.removed_share:
        return Decision("excluded", (), "pool_drained_by_funder")
    if m.sellers <= rules.stuck_max_share * m.buyers:
        return Decision("flagged", ("honeypot-sell-blocked",), "few_sellers")
    reasons = []
    if m.minted_share is not None and m.minted_share >= rules.inflation_share:
        reasons.append("privileged-mint")
    if (m.blacklist_events or 0) >= rules.blacklist_min_events:
        reasons.append("address-blacklist")
    if reasons:
        return Decision("flagged", tuple(reasons), "power_used")
    if m.sellers >= rules.healthy_min_sellers and m.sellers >= rules.healthy_min_share * m.buyers:
        if (m.late_sellers or 0) < rules.healthy_min_late_sellers:
            return Decision("excluded", (), "not_trading_late")
        return Decision("benign", (), "healthy_trading")
    return Decision("excluded", (), "unclear_seller_share")
