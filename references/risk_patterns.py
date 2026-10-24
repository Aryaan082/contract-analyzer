"""What counts as a "worth a look" function. One place, so it can be reviewed and unified.

NOTE for later: Component 1 (`chain/`, selector lookup) keeps its own starter list of risky names.
`chain/` cannot import `references/`, so the shared version should live in `chain/` as plain data
and be imported from here. Until then the two lists need to be kept in step by hand.

Two ways to recognise a function, both deliberately narrow:

1. RISKY_SIGNATURES: exact signatures. The selector of each is computed, so a target selector can
   be recognised with no name database at all.
2. kinds_for_name: word patterns over a name, for names that are only suggestions. Words are taken
   from the name (camelCase and underscores split), and ambiguous words need a partner: plain
   `withdraw` is how WETH works, so it only counts next to owner, admin, dev and the like. A name
   that merely CONTAINS "owner" somewhere (a junk collision name for `transfer`) proves nothing.
"""

import re

from references.abi import selector_of
from references.models import SignalKind

# Signatures people actually use for each capability. None of these says WHO may call the
# function; that is not visible in bytecode.
RISKY_SIGNATURES: dict[SignalKind, tuple[str, ...]] = {
    "mint": (
        "mint(address,uint256)",
        "mint(uint256)",
        "safeMint(address)",
        "safeMint(address,uint256)",
        "safeMint(address,string)",
        "issue(uint256)",  # Tether's mint
    ),
    "blacklist": (
        "blacklist(address)",
        "blacklist(address,bool)",
        "setBlacklist(address,bool)",
        "addToBlacklist(address)",
        "removeFromBlacklist(address)",
        "blacklisted(address)",
        "isBlacklisted(address)",
        "blacklistAddress(address)",
        "blocklist(address)",
        "addToBlocklist(address)",
        # Tether's spellings.
        "addBlackList(address)",
        "removeBlackList(address)",
        "isBlackListed(address)",
        "destroyBlackFunds(address)",
    ),
    "fee_setter": (
        "setFee(uint256)",
        "setFees(uint256,uint256)",
        "setTax(uint256)",
        "setTaxFee(uint256)",
        "setBuyFee(uint256)",
        "setSellFee(uint256)",
        "setBuyTax(uint256)",
        "setSellTax(uint256)",
        "setParams(uint256,uint256)",  # Tether's fee and fee cap
    ),
    "pause": (
        "pause()",
        "unpause()",
        "setPaused(bool)",
        "togglePause()",
    ),
    "owner_withdraw": (
        "rescueTokens(address,uint256)",
        "recoverERC20(address,uint256)",
        "recoverToken(address,uint256)",
        "sweep(address)",
        "drain()",
        "ownerWithdraw(uint256)",
        "withdrawStuckTokens(address)",
        "clearStuckBalance()",
    ),
    "upgrade": (
        "upgradeTo(address)",
        "upgradeToAndCall(address,bytes)",
        "upgradeAndCall(address,address,bytes)",
        "upgrade(address,address)",
        "setImplementation(address)",
        "changeAdmin(address)",
        "deprecate(address)",  # Tether's upgrade: forwards calls to a new contract
    ),
    "destroy": (
        "kill()",
        "destroy()",
        "selfDestruct()",
    ),
}

# selector -> kind, built from the signatures above.
RISKY_SELECTORS: dict[str, tuple[SignalKind, str]] = {
    selector_of(signature): (kind, signature)
    for kind, signatures in RISKY_SIGNATURES.items()
    for signature in signatures
}

KIND_ORDER: tuple[SignalKind, ...] = (
    "mint",
    "blacklist",
    "fee_setter",
    "pause",
    "owner_withdraw",
    "upgrade",
    "destroy",
    "delegatecall_opcode",
    "selfdestruct_opcode",
)

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")

_BLACKLIST_WORDS = ("blacklist", "blocklist")
_FEE_VERBS = {"set", "update", "change"}
_FEE_NOUNS = {"fee", "fees", "tax", "taxes"}
_ALWAYS_OWNER_WITHDRAW = {"rescue", "recover", "sweep", "drain"}
_WITHDRAW_WORDS = {"withdraw", "withdrawal"}
_PRIVILEGED_WORDS = {"owner", "admin", "dev", "team", "marketing"}
_DESTROY_WORDS = {"selfdestruct", "suicide", "kill", "destroy"}


def words(name: str) -> list[str]:
    """Lowercase words of a function name: `addToBlackList(address)` -> add, to, black, list."""
    return [w.lower() for w in _WORD.findall(name.partition("(")[0])]


def kinds_for_name(name: str) -> list[SignalKind]:
    """Which capabilities a function NAME suggests. Only a hint: the name may be a guess."""
    found = words(name)
    word_set = set(found)
    joined = "".join(found)
    kinds: list[SignalKind] = []
    if "mint" in word_set:
        kinds.append("mint")
    if any(w in joined for w in _BLACKLIST_WORDS):
        kinds.append("blacklist")
    if word_set & _FEE_VERBS and word_set & _FEE_NOUNS:
        kinds.append("fee_setter")
    if word_set & {"pause", "unpause"} or ("paused" in word_set and word_set & {"set", "toggle"}):
        kinds.append("pause")
    if word_set & _ALWAYS_OWNER_WITHDRAW or (
        word_set & _WITHDRAW_WORDS and word_set & _PRIVILEGED_WORDS
    ):
        kinds.append("owner_withdraw")
    if (
        "upgrade" in word_set
        or word_set >= {"set", "implementation"}
        or word_set >= {"change", "admin"}
    ):
        kinds.append("upgrade")
    if word_set & _DESTROY_WORDS:
        kinds.append("destroy")
    return kinds
