"""simulate_sell: could an ordinary wallet buy this token and then sell it back?

A rehearsal, not a trade. Foundry forks the chain at the pinned block into its own local EVM and
runs a buy, an approve and a sell as an invented wallet, through the exchange's real router
(chain/sandbox/ holds the Solidity, chain/sandbox.py runs it). The token's own code and storage are
the real ones, so a rule that keys off the pool address the token remembers behaves as it would
live. Nothing is sent to a real chain and no key is involved.

Which pool it trades in, from what exists at that block:
- a pool with liquidity (the most liquid one across the Uniswap V2-style exchanges and Uniswap V3 we
  know, judged by the wrapped-native token it holds): trade as it is;
- a V2 pool that exists but is empty: add liquidity to it, then trade;
- no pool at all: create one on V2 through the OFFICIAL factory, add liquidity, then trade.
Never a copy of the factory: a token's rules are keyed to its pool's address, and a pool at some
other address would hide them. The official factory gives a new pool the address any real one
would have. V3 pools are only traded in as they are; liquidity is never added to V3.

Taxes are read from what moved: the buy tax is the share of the pool's output that never reached
the wallet, the sell tax the share of the sold tokens that never reached the pool. A V3 pool
refuses a token that skims a cut when it is sent in, so there a transfer tax shows as a failed sell.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from web3 import Web3

from chain.dex import DEXES, UNISWAP_V3, V3_FEE_TIERS, Dex, V3Deployment
from chain.errors import ChainError
from chain.models import ToolResult, normalize_address
from chain.rpc import RpcClient
from chain.sandbox import (
    Mode,
    RehearsalParams,
    RehearsalResult,
    SandboxFailed,
    SandboxUnavailable,
    run_rehearsal,
)

log = logging.getLogger(__name__)

SOURCE = "foundry-fork"
DEFAULT_TRADE_WEI = 10**16  # 0.01 ETH
MAX_TRADE_WEI = 10**18
SEED_NATIVE_WEI = 10 * 10**18  # liquidity added to an empty or missing pool

# Made up: nobody holds a key for either. Foundry derives the same addresses from the same phrases.
SIM_WALLET = Web3.to_checksum_address(Web3.keccak(text="contract-analyzer:sell-simulation")[12:])
SIM_PROVIDER = Web3.to_checksum_address(
    Web3.keccak(text="contract-analyzer:liquidity-provider")[12:]
)
HIGH_TAX_BPS = 1000  # 10%: noted, not a verdict
THIN_POOL_FACTOR = 20  # a pool holding fewer than this many trades' worth of ETH is "thin"
LEFTOVER_NOTE_BPS = 100  # tokens still held after selling everything, above this share
DEFAULT_GAS_PRICE = 20 * 10**9

_codec = Web3().codec

ERROR_STRING = bytes.fromhex("08c379a0")  # Error(string)
PANIC = bytes.fromhex("4e487b71")  # Panic(uint256)

# Messages the Uniswap contracts themselves revert with. Only text from this fixed list is ever
# shown; anything else a contract says is reduced to its length. A token can revert with any of
# these strings too, so they are hints about where it failed, not proof of who failed.
KNOWN_MESSAGES = frozenset(
    {
        "UniswapV2: TRANSFER_FAILED",
        "UniswapV2: K",
        "UniswapV2: INSUFFICIENT_LIQUIDITY",
        "UniswapV2: INSUFFICIENT_OUTPUT_AMOUNT",
        "UniswapV2: INSUFFICIENT_INPUT_AMOUNT",
        "UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT",
        "UniswapV2Router: EXPIRED",
        "UniswapV2Router: INVALID_PATH",
        "TransferHelper: TRANSFER_FROM_FAILED",
        "TransferHelper: TRANSFER_FAILED",
        "TransferHelper: APPROVE_FAILED",
        "TransferHelper: ETH_TRANSFER_FAILED",
        # Uniswap V3: IIA is the pool saying it received less than it was owed.
        "IIA",
        "STF",
        "ST",
        "SA",
        "TF",
        "AS",
        "LOK",
        "SPL",
        "Too little received",
    }
)

LIMITATIONS = (
    "A pass shows what happens now, at this block. The owner can still change taxes, block a "
    "wallet or stop trading after a purchase; the bytecode checks cover that side.",
    "Only Uniswap-V2-style exchanges and Uniswap V3 (the ones listed in chain/dex.py) are looked "
    "at. A token whose liquidity sits elsewhere (Aerodrome, Uniswap V4, Curve) is rehearsed in a "
    "V2 pool of our own making, which may not be where it applies its rules, so a clean result "
    "there counts for less.",
    "When no pool had liquidity, the rehearsal fills one in and so assumes trading is open. A "
    "token that is not yet open for trading will refuse the buy here too.",
    "On Uniswap V3 a transfer tax cannot be measured: the pool refuses a token that skims a cut, "
    "so such a token shows as a failed sell, not as a percentage.",
    "A token can detect that it is being simulated (gas price, caller type, block data). The "
    "rehearsal uses a plain wallet and a realistic gas price, which defeats the common tricks "
    "but not all of them.",
    "Cooldowns, trading windows and per-wallet limits can look like a block.",
    "Taxes are measured on one small trade and ignore the pool's own 0.3% fee.",
)

Reason = Literal[
    "round_trip_ok",
    "buy_reverted",
    "buy_returned_no_tokens",
    "approve_reverted",
    "sell_reverted",
    "could_not_seed_liquidity",
    "chain_not_supported",
    "token_not_contract",
    "token_is_wrapped_native",
    "router_unavailable",
    "foundry_unavailable",
    "sandbox_failed",
]


class Failure(BaseModel, frozen=True):
    """How a step failed, without any text the contract chose."""

    step: Literal["seed", "buy", "approve", "sell"]
    kind: Literal["empty", "error_string", "panic", "custom_error", "other"]
    known_message: str | None  # only from KNOWN_MESSAGES
    message_length: int | None  # of an Error(string) message that is not in the known set
    selector: str | None  # of a custom error: its 4 bytes, not a name
    panic_code: int | None


class Pool(BaseModel, frozen=True):
    dex: str
    venue: Literal["v2", "v3"]
    fee_tier: int | None  # v3 only, in hundredths of a basis point (3000 is 0.3%)
    router: str
    pair: str  # the pool's address, on either venue
    # existing: traded in as it was. seeded: it existed but was empty, so liquidity was added.
    # created: it did not exist, so it was created through the official factory, then filled.
    source: Literal["existing", "seeded", "created"]
    wrapped_native_reserve_wei: int  # when the trade started
    liquidity_added_by: Literal["none", "owner", "invented"]
    pools_found: int  # pools for this token across the exchanges looked at


class SellSimulation(BaseModel, frozen=True):
    verdict: Literal["sellable", "sell_blocked", "buy_blocked", "inconclusive"]
    reason: Reason
    trade_wei: int
    pool: Pool | None
    tokens_received: int | None
    wrapped_native_back_wei: int | None
    buy_tax_bps: int | None
    sell_tax_bps: int | None
    round_trip_loss_bps: int | None  # everything lost on the way in and out, pool fees included
    tokens_left_after_sell: int | None
    failure: Failure | None
    notes: tuple[str, ...]
    limitations: tuple[str, ...] = LIMITATIONS


Runner = Callable[[str, int, RehearsalParams], RehearsalResult]


def classify_failure(step: Literal["seed", "buy", "approve", "sell"], data: bytes) -> Failure:
    """Reduce revert data to a kind, never forwarding text the contract chose."""
    known = length = selector = panic = None
    if not data:
        kind = "empty"
    elif data[:4] == ERROR_STRING:
        try:
            (text,) = _codec.decode(["string"], data[4:])
            kind, length = "error_string", len(text)
            known = text if text in KNOWN_MESSAGES else None
        except Exception:
            kind = "other"
    elif data[:4] == PANIC and len(data) >= 36:
        kind, panic = "panic", int.from_bytes(data[4:36], "big")
    elif len(data) >= 4:
        kind, selector = "custom_error", "0x" + data[:4].hex()
    else:
        kind = "other"
    return Failure(
        step=step,
        kind=kind,
        known_message=known,
        message_length=None if known else length,
        selector=selector,
        panic_code=panic,
    )


def _word(data: bytes, index: int = 0) -> int | None:
    chunk = data[32 * index : 32 * index + 32]
    return int.from_bytes(chunk, "big") if len(chunk) == 32 else None


def _address_word(data: bytes) -> str | None:
    value = _word(data)
    return None if value is None else Web3.to_checksum_address(value.to_bytes(32, "big")[12:])


def _bps(part: int, whole: int) -> int:
    """`part` as basis points of `whole`, rounded, never negative."""
    return max(0, round(10_000 * part / whole)) if whole > 0 else 0


def _selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


@dataclass(frozen=True, slots=True)
class _Found:
    """One exchange's view of the token: where it trades and, if there is a pool, how deep it is.
    On V3 there is one of these per fee tier that has a pool."""

    venue: Literal["v2", "v3"]
    name: str
    router: str
    factory: str
    wrapped_native: str
    pair: str | None
    native_reserve: int
    token_reserve: int
    fee: int = 0  # v3 only


def _look_up(client: RpcClient, token: str, dex: Dex) -> _Found | None:
    def found(pair: str | None, native: int = 0, held: int = 0) -> _Found:
        return _Found("v2", dex.name, dex.router, factory, dex.wrapped_native, pair, native, held)

    factory_call = client.call(dex.router, _selector("factory()"))
    factory = _address_word(factory_call.data) if factory_call.ok else None
    if factory is None:
        return None
    ask = _selector("getPair(address,address)") + _codec.encode(
        ["address", "address"], [token, dex.wrapped_native]
    )
    pair_call = client.call(factory, ask)
    pair = _address_word(pair_call.data) if pair_call.ok else None
    if pair is None or int(pair, 16) == 0:
        return found(None)
    reserves = client.call(pair, _selector("getReserves()"))
    r0, r1 = _word(reserves.data, 0), _word(reserves.data, 1)
    if not reserves.ok or r0 is None or r1 is None:
        return found(pair)
    token_is_0 = int(token, 16) < int(dex.wrapped_native, 16)
    native, held = (r1, r0) if token_is_0 else (r0, r1)
    return found(pair, native, held)


def _balance_of(client: RpcClient, token: str, holder: str) -> int:
    outcome = client.call(
        token, _selector("balanceOf(address)") + _codec.encode(["address"], [holder])
    )
    return (_word(outcome.data) or 0) if outcome.ok else 0


def _look_up_v3(client: RpcClient, token: str, v3: V3Deployment) -> list[_Found]:
    """The token's V3 pools against the wrapped-native token, one per fee tier that has one. A pool
    with nothing at the current price (`liquidity()` is 0) counts as empty."""
    pools = []
    for fee in V3_FEE_TIERS:
        ask = _selector("getPool(address,address,uint24)") + _codec.encode(
            ["address", "address", "uint24"], [token, v3.wrapped_native, fee]
        )
        outcome = client.call(v3.factory, ask)
        pool = _address_word(outcome.data) if outcome.ok else None
        if pool is None or int(pool, 16) == 0:
            continue
        live = client.call(pool, _selector("liquidity()"))
        active = (_word(live.data) or 0) > 0 if live.ok else False
        native = _balance_of(client, v3.wrapped_native, pool) if active else 0
        held = _balance_of(client, token, pool) if active else 0
        pools.append(
            _Found(
                "v3",
                v3.name,
                v3.swap_router,
                v3.factory,
                v3.wrapped_native,
                pool,
                native,
                held,
                fee,
            )
        )
    return pools


def _choose(found: list[_Found]) -> tuple[Mode, _Found]:
    """Trade in the most liquid existing pool, V2 or V3; else fill an empty V2 pool; else make a V2
    pool. Nothing is added to V3."""
    liquid = [f for f in found if f.pair and f.native_reserve > 0 and f.token_reserve > 0]
    if liquid:
        return "live", max(liquid, key=lambda f: f.native_reserve)
    v2 = [f for f in found if f.venue == "v2"]
    empty = [f for f in v2 if f.pair]
    if empty:
        return "seed", empty[0]
    return "create", v2[0]


def _blank(trade_wei: int, reason: Reason, pool: Pool | None = None, **fields) -> SellSimulation:
    base = dict.fromkeys(
        (
            "tokens_received",
            "wrapped_native_back_wei",
            "buy_tax_bps",
            "sell_tax_bps",
            "round_trip_loss_bps",
            "tokens_left_after_sell",
            "failure",
        )
    )
    verdict = fields.pop("verdict", "inconclusive")
    notes = fields.pop("notes", ())
    return SellSimulation(
        verdict=verdict,
        reason=reason,
        trade_wei=trade_wei,
        pool=pool,
        notes=notes,
        **{**base, **fields},
    )


def _outcome(
    trade_wei: int,
    mode: Mode,
    found: _Found,
    pools_found: int,
    result: RehearsalResult,
    *,
    empty_v3: bool = False,
) -> SellSimulation:
    pool = Pool(
        dex=found.name,
        venue=found.venue,
        fee_tier=found.fee if found.venue == "v3" else None,
        router=found.router,
        pair=result.pair,
        source={"live": "existing", "seed": "seeded", "create": "created"}[mode],
        wrapped_native_reserve_wei=result.native_reserve,
        liquidity_added_by=result.provider,
        pools_found=pools_found,
    )
    notes = []
    if mode != "live":
        notes.append(
            "No pool with liquidity existed, so one was "
            + ("filled" if mode == "seed" else "created and filled")
            + f" in the sandbox on {found.name}. The result assumes trading is open."
        )
        if empty_v3:
            notes.append(
                "A Uniswap V3 pool exists for this token but holds no liquidity at its price, so "
                "it was not used."
            )
    if not result.ok:
        step = result.step
        if step == "seed":
            return _blank(
                trade_wei,
                "could_not_seed_liquidity",
                pool,
                failure=classify_failure("seed", result.revert),
                notes=(*notes, "Liquidity could not be added, so the token was not rehearsed."),
            )
        if step in ("buy", "buy_empty"):
            if mode != "live":
                notes.append("The token may simply not be open for trading yet.")
            return _blank(
                trade_wei,
                "buy_reverted" if step == "buy" else "buy_returned_no_tokens",
                pool,
                verdict="buy_blocked",
                failure=classify_failure("buy", result.revert) if step == "buy" else None,
                notes=tuple(notes),
            )
        # approve or sell failed: the buy worked, so what it showed is kept.
        failure = classify_failure("approve" if step == "approve" else "sell", result.revert)
        if found.venue == "v3" and step == "sell" and failure.known_message == "IIA":
            notes.append(
                "The Uniswap V3 pool received less than it was owed: the token takes a cut when "
                "it is sent to the pool, which V3 refuses. This is how a transfer tax shows on V3."
            )
        return _blank(
            trade_wei,
            "approve_reverted" if step == "approve" else "sell_reverted",
            pool,
            verdict="sell_blocked",
            failure=failure,
            notes=tuple(notes),
            tokens_received=result.received,
            buy_tax_bps=_bps(result.pool_gave - result.received, result.pool_gave),
        )

    buy_tax = _bps(result.pool_gave - result.received, result.pool_gave)
    sell_tax = _bps(result.received - result.arrived, result.received)
    if buy_tax >= HIGH_TAX_BPS:
        notes.append(f"Buy tax {buy_tax / 100:.1f}% is above 10%.")
    if sell_tax >= HIGH_TAX_BPS:
        notes.append(f"Sell tax {sell_tax / 100:.1f}% is above 10%: little is left to sell.")
    if mode == "live" and result.native_reserve < THIN_POOL_FACTOR * trade_wei:
        notes.append("The pool is thin for this trade size, so the trade moves the price a lot.")
    if _bps(result.left, result.received) > LEFTOVER_NOTE_BPS:
        notes.append("Tokens were left over after selling everything: sells may be limited.")
    return _blank(
        trade_wei,
        "round_trip_ok",
        pool,
        verdict="sellable",
        tokens_received=result.received,
        wrapped_native_back_wei=result.native_back,
        buy_tax_bps=buy_tax,
        sell_tax_bps=sell_tax,
        round_trip_loss_bps=_bps(trade_wei - result.native_back, trade_wei),
        tokens_left_after_sell=result.left,
        notes=tuple(notes),
    )


def _run(client: RpcClient, token: str, trade_wei: int, runner: Runner) -> SellSimulation:
    if not 0 < trade_wei <= MAX_TRADE_WEI:
        raise ChainError(
            "invalid_amount", f"The trade size must be above 0 and at most {MAX_TRADE_WEI} wei."
        )
    client.pin()  # chain id and block now, so every answer carries both
    dexes = DEXES.get(client.chain_id)
    if not dexes:
        return _blank(
            trade_wei,
            "chain_not_supported",
            notes=(f"No exchange is configured for chain id {client.chain_id}.",),
        )
    if token.lower() == dexes[0].wrapped_native.lower():
        return _blank(trade_wei, "token_is_wrapped_native")
    if not client.get_code(token):
        return _blank(trade_wei, "token_not_contract")
    if client.get_code(SIM_WALLET) or client.get_code(SIM_PROVIDER):
        raise ChainError("internal_error", "A rehearsal account unexpectedly has code.")
    found = [f for f in (_look_up(client, token, d) for d in dexes) if f is not None]
    if not found:
        return _blank(trade_wei, "router_unavailable")
    v3 = UNISWAP_V3.get(client.chain_id)
    if v3 is not None:
        found += _look_up_v3(client, token, v3)
    mode, chosen = _choose(found)
    pools_found = sum(1 for f in found if f.pair)

    fork_url = client.fork_url
    if fork_url is None:
        return _blank(trade_wei, "sandbox_failed", notes=("No RPC URL is available to fork from.",))
    info = client.block_info()
    params = RehearsalParams(
        token=token,
        router=chosen.router,
        factory=chosen.factory,
        wrapped_native=chosen.wrapped_native,
        mode=mode,
        trade_wei=trade_wei,
        seed_wei=SEED_NATIVE_WEI,
        gas_price=2 * info.base_fee + 10**9 if info.base_fee else DEFAULT_GAS_PRICE,
        venue=chosen.venue,
        fee=chosen.fee,
    )
    try:
        result = runner(fork_url, client.block, params)
    except SandboxUnavailable:
        return _blank(
            trade_wei, "foundry_unavailable", notes=("Foundry (forge) is not installed here.",)
        )
    except SandboxFailed:
        return _blank(trade_wei, "sandbox_failed")
    return _outcome(
        trade_wei,
        mode,
        chosen,
        pools_found,
        result,
        empty_v3=mode != "live" and any(f.venue == "v3" and f.pair for f in found),
    )


def simulate_sell(
    client: RpcClient,
    token: str,
    trade_wei: int = DEFAULT_TRADE_WEI,
    *,
    runner: Runner | None = None,
) -> ToolResult[SellSimulation]:
    """The tool. Provenance names the chain, the token and the block everything was read at."""
    address = None
    try:
        address = normalize_address(token)
        data = _run(client, address, trade_wei, runner or run_rehearsal)
    except ChainError as err:
        return ToolResult[SellSimulation](
            provenance=client.provenance(address, source=SOURCE), data=err.to_tool_error()
        )
    except Exception:
        log.exception("simulate_sell failed")
        error = ChainError("internal_error", "Unexpected internal error; see the log.")
        return ToolResult[SellSimulation](
            provenance=client.provenance(address, source=SOURCE), data=error.to_tool_error()
        )
    return ToolResult[SellSimulation](
        provenance=client.provenance(address, source=SOURCE), data=data
    )
