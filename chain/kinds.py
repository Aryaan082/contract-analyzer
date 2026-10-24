"""Which well-known interfaces a contract's function selectors add up to.

A selector set is evidence about shape, not about behaviour: a contract that has all six ERC20
functions speaks ERC20, and says nothing about whether the functions do what the standard says.
Kinds are a deterministic lookup (the signatures below are ours, hashed here), so they are facts the
agent can cite. They are also what decides which behavioural check fits: the sell rehearsal only
makes sense for something shaped like a token.

A proxy answers to very few functions of its own, so classify the implementation's selectors too.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel
from web3 import Web3


@dataclass(frozen=True, slots=True)
class KindSpec:
    name: str
    signatures: tuple[str, ...]  # every one must be present for a complete match


KINDS: tuple[KindSpec, ...] = (
    KindSpec(
        "erc20",
        (
            "totalSupply()",
            "balanceOf(address)",
            "transfer(address,uint256)",
            "allowance(address,address)",
            "approve(address,uint256)",
            "transferFrom(address,address,uint256)",
        ),
    ),
    KindSpec(
        "erc721",
        (
            "balanceOf(address)",
            "ownerOf(uint256)",
            "safeTransferFrom(address,address,uint256)",
            "transferFrom(address,address,uint256)",
            "approve(address,uint256)",
            "setApprovalForAll(address,bool)",
            "getApproved(uint256)",
            "isApprovedForAll(address,address)",
        ),
    ),
    KindSpec(
        "erc1155",
        (
            "balanceOf(address,uint256)",
            "balanceOfBatch(address[],uint256[])",
            "setApprovalForAll(address,bool)",
            "isApprovedForAll(address,address)",
            "safeTransferFrom(address,address,uint256,uint256,bytes)",
            "safeBatchTransferFrom(address,address,uint256[],uint256[],bytes)",
        ),
    ),
    KindSpec(
        "erc4626",
        (
            "asset()",
            "totalAssets()",
            "convertToShares(uint256)",
            "convertToAssets(uint256)",
            "deposit(uint256,address)",
            "redeem(uint256,address,address)",
        ),
    ),
    KindSpec(
        "uniswap_v2_pair",
        (
            "token0()",
            "token1()",
            "getReserves()",
            "factory()",
            "swap(uint256,uint256,address,bytes)",
            "mint(address)",
            "burn(address)",
            "sync()",
            "skim(address)",
        ),
    ),
    KindSpec(
        "uniswap_v2_router",
        (
            "factory()",
            "WETH()",
            "addLiquidity(address,address,uint256,uint256,uint256,uint256,address,uint256)",
            "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)",
            "swapExactETHForTokens(uint256,address[],address,uint256)",
        ),
    ),
    KindSpec(
        "uniswap_v3_pool",
        (
            "slot0()",
            "liquidity()",
            "token0()",
            "token1()",
            "fee()",
            "tickSpacing()",
            "swap(address,bool,int256,uint160,bytes)",
            "flash(address,uint256,uint256,bytes)",
        ),
    ),
    KindSpec(
        "aave_pool",
        (
            "supply(address,uint256,address,uint16)",
            "withdraw(address,uint256,address)",
            "borrow(address,uint256,uint256,uint16,address)",
            "repay(address,uint256,uint256,address)",
            "liquidationCall(address,address,address,uint256,bool)",
            "getUserAccountData(address)",
        ),
    ),
    KindSpec(
        "aave_token",  # an aToken or a debt token
        (
            "UNDERLYING_ASSET_ADDRESS()",
            "POOL()",
            "scaledBalanceOf(address)",
            "scaledTotalSupply()",
        ),
    ),
    KindSpec(
        "compound_v2_ctoken",
        (
            "exchangeRateStored()",
            "exchangeRateCurrent()",
            "redeem(uint256)",
            "redeemUnderlying(uint256)",
            "borrow(uint256)",
            "repayBorrow(uint256)",
            "comptroller()",
            "interestRateModel()",
        ),
    ),
    KindSpec(
        "compound_v3_comet",
        (
            "baseToken()",
            "numAssets()",
            "getAssetInfo(uint8)",
            "supply(address,uint256)",
            "withdraw(address,uint256)",
            "absorb(address,address[])",
            "buyCollateral(address,uint256,uint256,address)",
        ),
    ),
)

NEAR_MISS_SHARE = 0.7  # of a kind's functions, at least this share (and 3) for a near miss


def selector_of(signature: str) -> str:
    return "0x" + Web3.keccak(text=signature)[:4].hex()


class KindMatch(BaseModel, frozen=True):
    kind: str
    matched: int
    total: int
    missing: tuple[str, ...]  # signatures from the standard, so safe to show


def _match(spec: KindSpec, present: set[str]) -> KindMatch:
    missing = tuple(s for s in spec.signatures if selector_of(s) not in present)
    return KindMatch(
        kind=spec.name,
        matched=len(spec.signatures) - len(missing),
        total=len(spec.signatures),
        missing=missing,
    )


def match_kinds(selectors: Iterable[str]) -> tuple[tuple[KindMatch, ...], tuple[KindMatch, ...]]:
    """(complete matches, near misses). A near miss has most of a kind's functions but not all: a
    token that left out `allowance` is still worth a look as an ERC20, and an interface copied
    badly is a signal in itself."""
    present = {s.lower() for s in selectors}
    complete, near = [], []
    for spec in KINDS:
        found = _match(spec, present)
        if found.matched == found.total:
            complete.append(found)
        elif found.matched >= max(3, math.ceil(NEAR_MISS_SHARE * found.total)):
            near.append(found)
    return tuple(complete), tuple(near)
