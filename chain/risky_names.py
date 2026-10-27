"""Which capabilities a function NAME suggests (mint, blacklist, fee setter, pause, ...).

Plain data and word rules, no network. `lookup_selector` uses it to flag a risky alternative name
for a selector. `references/risk_patterns.py` keeps an equal copy of the rules (`chain/` cannot
import `references/`); `tests/test_risky_names.py` fails if the two ever disagree.

Names are only suggestions: several names can share one selector. Words are taken from the name
(camelCase and underscores split), and ambiguous words need a partner: plain `withdraw` is how WETH
works, so it only counts next to owner, admin, dev and the like. A name that merely CONTAINS
"owner" somewhere proves nothing.
"""

import re
from typing import Literal

RiskKind = Literal[
    "mint", "blacklist", "fee_setter", "pause", "owner_withdraw", "upgrade", "destroy"
]

# Bump when the rules below change, so cached labels are recomputed from the stored names.
LABELS_VERSION = 1

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


def kinds_for_name(name: str) -> list[RiskKind]:
    """Which capabilities a function NAME suggests. Only a hint: the name may be a guess."""
    found = words(name)
    word_set = set(found)
    joined = "".join(found)
    kinds: list[RiskKind] = []
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
