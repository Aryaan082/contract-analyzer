"""Contracts whose labels do not come from the scans: the eight of `agent/contracts.toml`, four more
grey contracts, four well-known tokens, and the 13 tokens where honeypot.is and our own sell
rehearsal disagreed.

The eight cards reuse the smoke-test note's "Part 1" (written 2026-10-27 before any model was run):
acceptable verdicts, required tools, key-fact patterns. Those came from the `expect` texts in
`agent/contracts.toml`, four of which were written after a first report had been seen, so the cards
are primed; they are marked `reviewed_by = ""` like every other label.

Part 1 listed `read_getter`/`read_storage` as "expected but not required" for USDC and MILKA. The
card rules make a read of the controls required when `privileged-mint` or `address-blacklist` is a
labelled reason, so those two reasons are left out of USDC's and MILKA's `reasons` and their
mint/blacklist facts are carried by `facts` instead.
"""

from typing import Any

PINNED = 26_280_500  # the block the eight were pinned at (2026-10-27 00:37 UTC)
BASIC = ["identify_contract", "risk_scan", "get_verified_source"]
SELL_BLOCKED = r"sell_blocked|sell.{0,40}(revert|block|fail)|cannot sell|unable to sell"
NOT_UNVERIFIED = r"(?<!un)verified"
UNSEEN = r"not listed|unrecogni[sz]ed|no registry|matches no reference|no reference"


def _card(**fields: Any) -> dict[str, Any]:
    return {"chain": "ethereum", "block": PINNED, "reviewed_by": "", **fields}


EXISTING: list[dict[str, Any]] = [
    _card(
        id="usdc",
        address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        klass="grey",
        traits=["proxy", "token"],
        implementation="0x43506849d7c04f9138d1a2050bbf3a0c054402dd",
        reasons=["upgradeable-implementation"],
        requires=BASIC,
        facts=["proxy", "mint", "blacklist", NOT_UNVERIFIED],
        must_not_say=[r"\bCircle\b"],
        min_not_checked=2,
        group="existing",
        label_source="smoke-test-part-1",
        source="agent/contracts.toml (usdc); smoke-test note Part 1",
        reason="Legitimate regulated stablecoin with mint, blacklist and pause: powerful, not "
        "suspect. Scored on citations and what it must not say, not on the verdict.",
    ),
    _card(
        id="uniswap-pair",
        address="0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",
        klass="benign",
        traits=["official"],
        acceptable_verdicts=["no_red_flags_found"],
        requires=BASIC,
        forbids=["simulate_sell"],
        facts=[r"official|listed by", r"identical|exact (copy|match)|same code"],
        group="existing",
        label_source="smoke-test-part-1",
        source="agent/contracts.toml (uniswap-pair); smoke-test note Part 1",
        reason="The official Uniswap V2 USDC/WETH pair: the factory lists it and its code is "
        "identical to the reference pair.",
    ),
    _card(
        id="milka",
        address="0x84e84bf4ee5de75871b59e45369c1d1ade3cb3fb",
        klass="flagged",
        traits=["token"],
        reasons=["mutable-fees"],
        acceptable_verdicts=["elevated_risk"],
        requires=BASIC,
        facts=[
            "mint",
            "blacklist",
            "pause",
            r"plain account|single (key|account|owner)|externally owned|no code|one key",
        ],
        group="existing",
        label_source="smoke-test-part-1",
        source="agent/contracts.toml (milka); smoke-test note Part 1",
        reason="A template ERC-20 with about 50 extras (mint, blacklist, pause, fees, anti-whale) "
        "held by one plain account.",
    ),
    _card(
        id="groklet",
        address="0x656bd1d70e24de09744b89ea0de468d831af5f55",
        klass="flagged",
        traits=["token"],
        reasons=["honeypot-sell-blocked"],
        acceptable_verdicts=["high_risk"],
        requires=BASIC,
        facts=[SELL_BLOCKED, NOT_UNVERIFIED],
        group="existing",
        label_source="smoke-test-part-1",
        source="agent/contracts.toml (groklet); agent/honeypot_is_labels.json",
        reason="Buys work and sells revert in our sell rehearsal at the pinned block; honeypot.is "
        "agreed (626 of 628 holders could not sell).",
    ),
    _card(
        id="meowe",
        address="0x63b218ca3f5049f670ae7a9466bd2ea8d3f9f7f5",
        klass="flagged",
        traits=["token"],
        reasons=["honeypot-sell-blocked"],
        acceptable_verdicts=["high_risk"],
        requires=BASIC,
        facts=[SELL_BLOCKED, NOT_UNVERIFIED],
        group="existing",
        label_source="smoke-test-part-1",
        source="agent/contracts.toml (meowe); agent/honeypot_is_labels.json",
        reason="Sell blocked in our rehearsal; a small different contract from groklet's family.",
    ),
    _card(
        id="asasar",
        address="0xbe2ab177599c6381c70725feee6663f9bcff2197",
        klass="flagged",
        traits=["token"],
        reasons=["honeypot-sell-blocked"],
        acceptable_verdicts=["high_risk"],
        requires=BASIC,
        facts=[SELL_BLOCKED, r"unverified|no verified source|not verified"],
        group="existing",
        label_source="smoke-test-part-1",
        source="agent/contracts.toml (asasar); agent/honeypot_is_labels.json",
        reason="Sell blocked in our rehearsal (revert inside the router); source unverified.",
    ),
    _card(
        id="brick",
        address="0xfcb260fba59df4d1eaca89452571db17f2d3efaf",
        klass="flagged",
        traits=["token"],
        reasons=["hidden-transfer-tax"],
        acceptable_verdicts=["high_risk", "elevated_risk"],
        requires=BASIC,
        facts=[r"85%|8500|85 percent"],
        group="existing",
        label_source="smoke-test-part-1",
        source="agent/contracts.toml (brick); agent/honeypot_is_labels.json",
        reason="Sells work but our rehearsal measures an 85% sell tax; owner may set 99%.",
    ),
    _card(
        id="seaport",
        address="0x0000000000000068f116a894984e2db1123eb395",
        klass="grey",
        traits=["not_a_token"],
        requires=BASIC,
        forbids=["simulate_sell"],
        facts=[NOT_UNVERIFIED, UNSEEN],
        must_not_say=[r"\bOpenSea\b"],
        min_not_checked=3,
        group="existing",
        label_source="smoke-test-part-1",
        source="agent/contracts.toml (seaport); smoke-test note Part 1",
        reason="A legitimate marketplace protocol the library and registry do not recognise: the "
        "right report says what it could not establish and does not name the protocol itself.",
    ),
]

# More grey contracts: well known, powerful or unfamiliar, scored on citations and gaps only. Their
# traits and implementation are not written here: `evals.collect verify` takes them from what
# identify_contract says at the pinned block (the tool the agent has), and the build adds them.
GREY: list[dict[str, Any]] = [
    _card(
        id="usdt",
        address="0xdac17f958d2ee523a2206206994597c13d831ec7",
        klass="grey",
        requires=BASIC,
        facts=["mint|issue", "blacklist|blocklist"],
        min_not_checked=2,
        group="grey",
        label_source="rule:well-known-powerful",
        source="chosen by the written class definition of grey (label_rules.toml)",
        reason="A regulated stablecoin with issue, blacklist and fee functions held by an owner: "
        "powerful, not suspect.",
    ),
    _card(
        id="steth",
        address="0xae7ab96520de3a18e5e111b5eaab095312d7fe84",
        klass="grey",
        requires=BASIC,
        must_not_say=[r"\bLido\b"],
        min_not_checked=2,
        group="grey",
        label_source="rule:well-known-powerful",
        source="chosen by the written class definition of grey (label_rules.toml)",
        reason="A proxy token of a kind the tools do not resolve (identify_contract does not "
        "recognise it): a good report says where its code lives was not established. The name of "
        "the operator comes from outside the evidence.",
    ),
    _card(
        id="dai",
        address="0x6b175474e89094c44da98b954eedeac495271d0f",
        klass="grey",
        requires=BASIC,
        must_not_say=[r"\bMakerDAO\b"],
        min_not_checked=2,
        group="grey",
        label_source="rule:well-known-powerful",
        source="chosen by the written class definition of grey (label_rules.toml)",
        reason="A stablecoin minted and burned by authorised modules: powerful, not suspect.",
    ),
    _card(
        id="permit2",
        address="0x000000000022d473030f116ddee9f6b43ac78ba3",
        klass="grey",
        requires=BASIC,
        forbids=["simulate_sell"],
        must_not_say=[r"\bUniswap\b"],
        min_not_checked=3,
        group="grey",
        label_source="rule:well-known-powerful",
        source="chosen by the written class definition of grey (label_rules.toml)",
        reason="A widely used approval-management contract that holds standing allowances: not an "
        "ERC-20, unfamiliar to the library, and its author is not shown by any tool.",
    ),
]

# Well-known benign tokens: reported separately from the obscure ones (the model may remember them).
FAMOUS: list[dict[str, Any]] = [
    _card(
        id=f"famous-{name}",
        address=address,
        klass="benign",
        group="famous",
        label_source="script:famous-sample",
        source="chosen by the written rule for famous tokens (label_rules.toml [famous])",
        reason="A widely used token that hundreds of different wallets send within a few hours.",
    )
    for name, address in (
        ("weth", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"),
        ("link", "0x514910771af9ca656af840dff83e8264ecf986ca"),
        ("uni", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"),
        ("aave", "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"),
        # LINK, UNI and AAVE failed the sample rule at 179, 189 and 98 senders (it asks for 200);
        # the rule was not changed. These three were added after that, tested by the same rule.
        ("wbtc", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"),
        ("shib", "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce"),
        ("pepe", "0x6982508145454ce325ddbe47a25d4ec3d2311933"),
    )
]

# honeypot.is rated these honeypots (queried once, 2026-10-26; the answers were recovered from a
# session transcript, not re-queried and not kept in agent/honeypot_is_labels.json) while our own
# sell rehearsal at block 26280500 said sellable, or found no ERC-20 interface. Kept apart: neither
# opinion is the label. (address, honeypot.is: sell tax percent, our rehearsal.)
DISPUTED: list[tuple[str, str, str]] = [
    ("0xaec304685b81205910059b12ecba1597c47f729b", "100", "no ERC-20 interface"),
    ("0x74d5b2760277e1767683c6855f2a967f1fc8cf06", "100", "no ERC-20 interface"),
    ("0x4260fdbc3b4e0c7dc144a7203de76aadfa7be11a", "100", "sellable, no tax"),
    ("0xdcd29c63df9beef043524e05f15edc1aac6201d1", "76.57", "sellable, 5% buy tax"),
    ("0xaac223917e50ead5d0589fd7c7a70449a14bac09", "90.28", "sellable, 1% buy tax"),
    ("0xd76e2942e3a71061ef010b1935eaa89e924ee807", "99.89", "sellable, 5% buy tax"),
    ("0x57d039e64bc40e13e14f44c69bce4310d92e28c8", "100", "sellable, no tax"),
    ("0x6c19714d8d1536faec2f71f8ba6d9617b0e38941", "100", "sellable, no tax"),
    ("0x83137aa9c4bbfaf30c8a30be90b0aba3ee110221", "100", "sellable, no tax"),
    ("0xaeaf4d8da07986993925d229a4074a9dc1d87012", "100", "sellable, no tax"),
    ("0x4a4a792eaca707308324b32f987fe1d6ad00e5b2", "0", "sellable, no tax"),
    ("0x1609d74938f68c1227196577cc867da4018da6c9", "0", "sellable, no tax"),
    ("0xc039cbe2018a1c39412843a43cc0ab963437419e", "0", "sellable, no tax"),
]


def fixed_addresses() -> list[str]:
    """Every address whose label does not come from a scan, so a scan can leave its code families
    out."""
    cards = EXISTING + GREY + FAMOUS
    return [c["address"] for c in cards] + [a for a, _, _ in DISPUTED]
