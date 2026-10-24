"""Which exchanges `simulate_sell` and `identify` know, per chain.

Keyed by chain id (what the node reports), not by the name in `.env`, which is only a label.
All entries were checked on-chain on 2026-10-23:
- V2: the router has code, `router.factory()` returns the factory below (thousands of pairs) and
  `router.WETH()` returns the wrapped-native token.
- V3: `SwapRouter02.factory()` returns the V3 factory, `SwapRouter02.WETH9()` the wrapped-native
  token, and the factory's `getPool` returns pools whose own `factory()` points back at it.
Check any new entry the same way before adding it. V2 entries must share Uniswap V2's router and
pair interface.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Dex:
    name: str
    router: str  # Router02, or a fork with the same interface
    wrapped_native: str
    factory: str


@dataclass(frozen=True, slots=True)
class V3Deployment:
    name: str
    factory: str
    swap_router: str  # SwapRouter02
    wrapped_native: str


DEXES: dict[int, tuple[Dex, ...]] = {
    1: (
        Dex(
            name="uniswap-v2",
            router="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
            wrapped_native="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            factory="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        ),
        Dex(
            name="sushiswap-v2",
            router="0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
            wrapped_native="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            factory="0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
        ),
    ),
    8453: (
        Dex(
            name="uniswap-v2",
            router="0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
            wrapped_native="0x4200000000000000000000000000000000000006",
            factory="0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6",
        ),
        Dex(
            name="sushiswap-v2",
            router="0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891",
            wrapped_native="0x4200000000000000000000000000000000000006",
            factory="0x71524B4f93c58fcbF659783284E38825f0622859",
        ),
    ),
}

UNISWAP_V3: dict[int, V3Deployment] = {
    1: V3Deployment(
        name="uniswap-v3",
        factory="0x1F98431c8aD98523631AE4a59f267346ea31F984",
        swap_router="0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
        wrapped_native="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    ),
    8453: V3Deployment(
        name="uniswap-v3",
        factory="0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        swap_router="0x2626664c2603336E57B271c5C0b26F421741e481",
        wrapped_native="0x4200000000000000000000000000000000000006",
    ),
}

V3_FEE_TIERS = (100, 500, 3000, 10000)  # the tiers Uniswap V3 launched with, in hundredths of a bip
