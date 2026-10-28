"""The label rules as numbers, read from `evals/datasets/label_rules.toml`, and the fixed lists the
rules point at (routers, event names). The file's SHA-256 records that the rules came first."""

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3

from references.abi import selector_of
from references.risk_patterns import RISKY_SIGNATURES

DATASETS = Path(__file__).resolve().parent.parent / "datasets"
RULES_PATH = DATASETS / "label_rules.toml"
HASH_PATH = DATASETS / "label_rules.sha256"

ZERO = "0x" + "00" * 20
DEAD = "0x000000000000000000000000000000000000dead"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
UNISWAP_V2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"

# Contracts that stand in for many wallets when they buy or sell. Counting one of them as "a buyer"
# would shrink the crowd to one, so they are left out of both counts. Not every aggregator is here:
# a missed one only makes a token look less crowded, which pushes it to "unclear" (left out), not
# into a class. The first three come from chain/dex.py; the rest are from memory and unchecked.
ROUTERS = frozenset(
    a.lower()
    for a in (
        "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # Uniswap V2 Router02
        "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",  # SushiSwap router
        "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",  # Uniswap SwapRouter02
        "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD",  # Universal Router
        "0x66a9893cC07D91D95644AEDD05D03f95e1dBA8Af",  # Universal Router (newer)
        "0x1111111254EEB25477B68fb85Ed929f73A960582",  # 1inch v5
        "0x111111125421cA6dc452d289314280a0f8842A65",  # 1inch v6
        "0xDef1C0ded9bec7F1a1670819833240f027b25EfF",  # 0x exchange proxy
        "0x9008D19f58AAbD9eD0D60971565AA8510560ab41",  # CoW settlement
        "0x000000000022D473030F116dDEE9F6B43aC78BA3",  # Permit2
    )
)

# Event signatures that mean "this address was blacklisted". A fixed list: an event with another
# name is not seen, which makes the blacklist rule under-count, never over-count.
BLACKLIST_EVENTS = (
    "Blacklisted(address)",
    "Blacklisted(address,bool)",
    "Blacklist(address,bool)",
    "BlacklistUpdated(address,bool)",
    "BlackListUpdated(address,bool)",
    "BlacklistAdded(address)",
    "AddedBlackList(address)",
    "AddedBlacklist(address)",
    "SetBlacklist(address,bool)",
    "Blocklisted(address)",
)

TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").to_0x_hex()
SYNC_TOPIC = Web3.keccak(text="Sync(uint112,uint112)").to_0x_hex()
MINT_TOPIC = Web3.keccak(text="Mint(address,uint256,uint256)").to_0x_hex()
BURN_TOPIC = Web3.keccak(text="Burn(address,uint256,uint256,address)").to_0x_hex()
PAIR_CREATED_TOPIC = Web3.keccak(text="PairCreated(address,address,address,uint256)").to_0x_hex()
BLACKLIST_TOPICS = tuple(Web3.keccak(text=s).to_0x_hex() for s in BLACKLIST_EVENTS)

# Selectors of the functions the supply and blacklist rules need to see in the bytecode.
MINT_SELECTORS = frozenset(selector_of(s) for s in RISKY_SIGNATURES["mint"])
BLACKLIST_SELECTORS = frozenset(selector_of(s) for s in RISKY_SIGNATURES["blacklist"])
ERC20_SELECTORS = frozenset(
    selector_of(s) for s in ("transfer(address,uint256)", "balanceOf(address)")
)


@dataclass(frozen=True)
class Rules:
    version: int
    pinned_after: int
    liquidity_within: int
    window: int
    latest_end: int
    first_launch: int
    last_launch: int
    min_buyers: int
    stuck_max_share: float
    healthy_min_sellers: int
    healthy_min_share: float
    late_blocks: int
    healthy_min_late_sellers: int
    inflation_share: float
    needs_mint_selector: bool
    blacklist_min_events: int
    removed_share: float
    famous_blocks: int
    famous_senders: int
    windows: tuple[tuple[int, int], ...]
    max_transfer_logs: int
    max_family_scanned: int
    target_new_flagged: int
    target_new_benign: int
    sample_salt: str
    heldout_share: float
    tool_use_size: int
    pin_before_attack: int
    incident_first: str
    incident_last: str

    @classmethod
    def from_toml(cls, data: dict) -> "Rules":
        try:
            window, trading, supply = data["window"], data["trading"], data["supply"]
            scan, split = data["scan"], data["split"]
            return cls(
                version=data["version"],
                pinned_after=window["pinned_after_first_liquidity_blocks"],
                liquidity_within=window["pair_must_have_liquidity_within_blocks"],
                window=window["outcome_window_blocks"],
                latest_end=window["latest_allowed_end_block"],
                first_launch=window["first_launch_block"],
                last_launch=window["last_launch_block"],
                min_buyers=trading["min_buyers"],
                stuck_max_share=trading["stuck_max_seller_share"],
                healthy_min_sellers=trading["healthy_min_sellers"],
                healthy_min_share=trading["healthy_min_seller_share"],
                late_blocks=trading["late_blocks"],
                healthy_min_late_sellers=trading["healthy_min_late_sellers"],
                inflation_share=supply["inflation_share"],
                needs_mint_selector=supply["needs_mint_selector"],
                blacklist_min_events=data["blacklist"]["min_events"],
                removed_share=data["liquidity"]["removed_share"],
                famous_blocks=data["famous"]["sample_blocks"],
                famous_senders=data["famous"]["min_senders"],
                windows=tuple((int(a), int(b)) for a, b in scan["windows"]),
                max_transfer_logs=scan["max_transfer_logs"],
                max_family_scanned=scan["max_family_scanned"],
                target_new_flagged=scan["target_new_flagged"],
                target_new_benign=scan["target_new_benign"],
                sample_salt=scan["sample_salt"],
                heldout_share=split["heldout_share"],
                tool_use_size=split["tool_use_set_size"],
                pin_before_attack=data["incidents"]["pin_blocks_before_attack"],
                incident_first=data["incidents"]["first_date"],
                incident_last=data["incidents"]["last_date"],
            )
        except KeyError as err:
            raise ValueError(f"label_rules.toml is missing {err}") from None


def rules_hash(path: Path = RULES_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rules(path: Path = RULES_PATH) -> Rules:
    return Rules.from_toml(tomllib.loads(path.read_text()))
