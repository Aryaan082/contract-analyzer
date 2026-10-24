"""Ask the protocols themselves whether they list an address.

Every protocol here keeps an on-chain record of its own contracts: Uniswap's factories know their
pairs and pools, Aave's addresses provider knows its pool and the pool knows its tokens, Compound's
Comptroller lists its markets and its Configurator its Comet markets. The anchors (a factory, a
provider) are hard-coded and were checked on-chain; everything else is asked of them at the pinned
block. That makes "is this really Aave's pool?" a lookup, not a judgement, and it catches the
lookalike that copies a protocol's interface but was never listed by it.

Contracts are probed by calling their view functions rather than by reading their code, so proxies
(Aave's pool and tokens are proxies) are handled with no extra step.

Statuses:
- official: the protocol's own record lists this address.
- claims_official_but_not_listed: it points at an official anchor (factory, provider, Comptroller)
  as its own, but that anchor does not list it. This is the lookalike signal.
- unlisted: it has the interface but belongs to nothing we anchor to (a fork, another market).
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from web3 import Web3

from chain.dex import DEXES, UNISWAP_V3
from chain.models import normalize_address
from chain.rpc import RpcClient

Registry = Literal["uniswap_v2", "uniswap_v3", "aave_v3", "compound_v2", "compound_v3"]
Status = Literal["official", "claims_official_but_not_listed", "unlisted"]

ZERO = "0x" + "00" * 20
_codec = Web3().codec


@dataclass(frozen=True, slots=True)
class ChainRegistries:
    aave_v3_provider: str | None  # PoolAddressesProvider
    compound_v2_comptroller: str | None
    compound_v3_configurator: str | None


# Checked on-chain 2026-10-23: each provider's getPool() returns a pool whose getReserveData lists
# tokens that answer POOL() with that pool; the Comptroller's getAllMarkets() lists cTokens whose
# comptroller() points back; the Configurator's getConfiguration(comet) returns the Comet's
# governor and base token and an empty record for an address it does not know.
REGISTRIES: dict[int, ChainRegistries] = {
    1: ChainRegistries(
        aave_v3_provider="0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e",
        compound_v2_comptroller="0x3d9819210A31b4961b30EF54bE2aeD79B9c9Cd3B",
        compound_v3_configurator="0x316f9708bB98af7dA9c68C1C3b5e79039cD336E3",
    ),
    8453: ChainRegistries(
        aave_v3_provider="0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D",
        compound_v2_comptroller=None,
        compound_v3_configurator="0x45939657d1CA34A8FA39A924B71D28Fe8431e581",
    ),
}


class Finding(BaseModel, frozen=True):
    registry: Registry
    role: str  # pair, pool, router, factory, a_token, variable_debt_token, market, comet, ...
    status: Status
    detail: str  # written here, never copied from the contract
    related: dict[str, str]  # addresses the contract named, such as its factory and tokens


def _selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


def _ask(
    client: RpcClient, to: str, signature: str, types: tuple[str, ...] = (), values: tuple = ()
) -> bytes | None:
    """The data of a successful call that returned at least one word, else None."""
    data = _selector(signature) + (_codec.encode(list(types), list(values)) if types else b"")
    outcome = client.call(to, data)
    return outcome.data if outcome.ok and len(outcome.data) >= 32 else None


def _address(data: bytes | None, index: int = 0) -> str | None:
    word = None if data is None else data[32 * index : 32 * index + 32]
    if word is None or len(word) < 32 or word[:12] != bytes(12):
        return None
    return Web3.to_checksum_address(word[12:])


def _ask_address(client: RpcClient, to: str, signature: str, *args: str) -> str | None:
    types = ("address",) * len(args)
    return _address(_ask(client, to, signature, types, args))


def _same(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.lower() == b.lower()


def _infrastructure(chain_id: int, address: str) -> list[Finding]:
    """The anchors themselves: Uniswap's factories and routers."""
    found = []
    for dex in DEXES.get(chain_id, ()):
        for role, known in (("factory", dex.factory), ("router", dex.router)):
            if _same(address, known):
                found.append(
                    Finding(
                        registry="uniswap_v2",
                        role=role,
                        status="official",
                        detail=f"The {dex.name} {role} on this chain.",
                        related={},
                    )
                )
    v3 = UNISWAP_V3.get(chain_id)
    if v3:
        for role, known in (("factory", v3.factory), ("router", v3.swap_router)):
            if _same(address, known):
                found.append(
                    Finding(
                        registry="uniswap_v3",
                        role=role,
                        status="official",
                        detail=f"The {v3.name} {role} on this chain.",
                        related={},
                    )
                )
    return found


def _uniswap_v2(client: RpcClient, address: str) -> list[Finding]:
    reserves = _ask(client, address, "getReserves()")
    if reserves is None or len(reserves) < 64:
        return []
    token0 = _ask_address(client, address, "token0()")
    token1 = _ask_address(client, address, "token1()")
    factory = _ask_address(client, address, "factory()")
    if token0 is None or token1 is None or factory is None:
        return []
    related = {"token0": token0, "token1": token1, "factory": factory}
    known = {dex.factory.lower(): dex.name for dex in DEXES.get(client.chain_id, ())}
    if factory.lower() not in known:
        return [
            Finding(
                registry="uniswap_v2",
                role="pair",
                status="unlisted",
                detail="It has the Uniswap V2 pair interface, but its factory is not one we know.",
                related=related,
            )
        ]
    listed = _ask_address(client, factory, "getPair(address,address)", token0, token1)
    if _same(listed, address):
        return [
            Finding(
                registry="uniswap_v2",
                role="pair",
                status="official",
                detail=f"The {known[factory.lower()]} factory lists this pair for its two tokens.",
                related=related,
            )
        ]
    return [
        Finding(
            registry="uniswap_v2",
            role="pair",
            status="claims_official_but_not_listed",
            detail=(
                f"It names the {known[factory.lower()]} factory as its own, but that factory lists "
                + ("no pair" if listed in (None, ZERO) else f"a different pair ({listed})")
                + " for these two tokens."
            ),
            related=related,
        )
    ]


def _uniswap_v3(client: RpcClient, address: str) -> list[Finding]:
    slot0 = _ask(client, address, "slot0()")
    if slot0 is None or len(slot0) < 64:
        return []
    token0 = _ask_address(client, address, "token0()")
    token1 = _ask_address(client, address, "token1()")
    factory = _ask_address(client, address, "factory()")
    fee_word = _ask(client, address, "fee()")
    if token0 is None or token1 is None or factory is None or fee_word is None:
        return []
    fee = int.from_bytes(fee_word[:32], "big")
    related = {"token0": token0, "token1": token1, "factory": factory, "fee": str(fee)}
    v3 = UNISWAP_V3.get(client.chain_id)
    if v3 is None or not _same(factory, v3.factory):
        return [
            Finding(
                registry="uniswap_v3",
                role="pool",
                status="unlisted",
                detail="It has the Uniswap V3 pool interface, but its factory is not one we know.",
                related=related,
            )
        ]
    ask = _codec.encode(["address", "address", "uint24"], [token0, token1, fee])
    outcome = client.call(v3.factory, _selector("getPool(address,address,uint24)") + ask)
    listed = _address(outcome.data) if outcome.ok else None
    if _same(listed, address):
        return [
            Finding(
                registry="uniswap_v3",
                role="pool",
                status="official",
                detail="The Uniswap V3 factory lists this pool for its two tokens and fee tier.",
                related=related,
            )
        ]
    return [
        Finding(
            registry="uniswap_v3",
            role="pool",
            status="claims_official_but_not_listed",
            detail=(
                "It names the Uniswap V3 factory as its own, but that factory lists "
                + ("no pool" if listed in (None, ZERO) else f"a different pool ({listed})")
                + " for these two tokens and this fee tier."
            ),
            related=related,
        )
    ]


def _aave_v3(client: RpcClient, address: str, provider: str) -> list[Finding]:
    found = []
    official_pool = _ask_address(client, provider, "getPool()")
    if _same(address, provider):
        return [
            Finding(
                registry="aave_v3",
                role="addresses_provider",
                status="official",
                detail="Aave V3's PoolAddressesProvider on this chain.",
                related={"pool": official_pool or ""},
            )
        ]
    if _same(address, official_pool):
        return [
            Finding(
                registry="aave_v3",
                role="pool",
                status="official",
                detail="The pool Aave V3's PoolAddressesProvider points at.",
                related={"addresses_provider": provider},
            )
        ]

    claimed_provider = _ask_address(client, address, "ADDRESSES_PROVIDER()")
    if claimed_provider is not None:
        official = _same(claimed_provider, provider)
        found.append(
            Finding(
                registry="aave_v3",
                role="pool",
                status="claims_official_but_not_listed" if official else "unlisted",
                detail=(
                    "It names Aave V3's PoolAddressesProvider as its own, but the provider's pool "
                    "is a different address."
                    if official
                    else "It answers like an Aave V3 pool of another market or a fork."
                ),
                related={
                    "addresses_provider": claimed_provider,
                    "official_pool": official_pool or "",
                },
            )
        )

    pool = _ask_address(client, address, "POOL()")
    underlying = _ask_address(client, address, "UNDERLYING_ASSET_ADDRESS()")
    if pool is not None and underlying is not None:
        related = {"pool": pool, "underlying": underlying}
        if not _same(pool, official_pool):
            found.append(
                Finding(
                    registry="aave_v3",
                    role="token",
                    status="unlisted",
                    detail="It answers like an Aave token of a pool we do not anchor to.",
                    related=related,
                )
            )
            return found
        reserve = _ask(client, pool, "getReserveData(address)", ("address",), (underlying,))
        roles = {8: "a_token", 9: "stable_debt_token", 10: "variable_debt_token"}
        listed = (
            {index: _address(reserve, index) for index in roles}
            if reserve is not None and len(reserve) >= 11 * 32
            else {}
        )
        role = next((roles[i] for i, a in listed.items() if _same(a, address)), None)
        found.append(
            Finding(
                registry="aave_v3",
                role=role or "token",
                status="official" if role else "claims_official_but_not_listed",
                detail=(
                    f"Aave V3's pool lists it as the {role.replace('_', ' ')} of its underlying."
                    if role
                    else "It names Aave V3's pool as its own, but the pool does not list it as a "
                    "token of that asset."
                ),
                related=related,
            )
        )
    return found


def _compound_v2(client: RpcClient, address: str, comptroller: str) -> list[Finding]:
    if _same(address, comptroller):
        return [
            Finding(
                registry="compound_v2",
                role="comptroller",
                status="official",
                detail="Compound V2's Comptroller.",
                related={},
            )
        ]
    claimed = _ask_address(client, address, "comptroller()")
    if claimed is None or _ask(client, address, "exchangeRateStored()") is None:
        return []
    related = {"comptroller": claimed}
    if not _same(claimed, comptroller):
        return [
            Finding(
                registry="compound_v2",
                role="market",
                status="unlisted",
                detail="It answers like a Compound V2 market of another Comptroller or a fork.",
                related=related,
            )
        ]
    listing = _ask(client, comptroller, "getAllMarkets()")
    try:
        (markets,) = _codec.decode(["address[]"], listing) if listing else ([],)
    except Exception:
        markets = []
    listed = any(_same(m, address) for m in markets)
    return [
        Finding(
            registry="compound_v2",
            role="market",
            status="official" if listed else "claims_official_but_not_listed",
            detail=(
                "Compound V2's Comptroller lists it as a market."
                if listed
                else "It names Compound V2's Comptroller as its own, but the Comptroller does not "
                "list it as a market."
            ),
            related=related,
        )
    ]


def _compound_v3(client: RpcClient, address: str, configurator: str) -> list[Finding]:
    if _same(address, configurator):
        return [
            Finding(
                registry="compound_v3",
                role="configurator",
                status="official",
                detail="Compound III's Configurator on this chain.",
                related={},
            )
        ]
    base = _ask_address(client, address, "baseToken()")
    if base is None or _ask(client, address, "numAssets()") is None:
        return []
    record = _ask(client, configurator, "getConfiguration(address)", ("address",), (address,))
    governor = _address(record, 1) if record and len(record) >= 4 * 32 else None
    listed_base = _address(record, 3) if record and len(record) >= 4 * 32 else None
    listed = governor not in (None, ZERO) and _same(listed_base, base)
    return [
        Finding(
            registry="compound_v3",
            role="comet",
            status="official" if listed else "unlisted",
            detail=(
                "Compound III's Configurator has a record for it with the same base token."
                if listed
                else "It has the Compound III market interface, but the Configurator has no record "
                "of it."
            ),
            related={"base_token": base},
        )
    ]


def check_registries(client: RpcClient, address: str) -> list[Finding]:
    """What each protocol's own record says about `address`, at the client's pinned block."""
    address = normalize_address(address)
    chain_id = client.chain_id
    found = _infrastructure(chain_id, address)
    found += _uniswap_v2(client, address)
    found += _uniswap_v3(client, address)
    known = REGISTRIES.get(chain_id)
    if known is not None:
        if known.aave_v3_provider:
            found += _aave_v3(client, address, known.aave_v3_provider)
        if known.compound_v2_comptroller:
            found += _compound_v2(client, address, known.compound_v2_comptroller)
        if known.compound_v3_configurator:
            found += _compound_v3(client, address, known.compound_v3_configurator)
    return found
