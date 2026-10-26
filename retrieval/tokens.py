"""Turning text into the words the keyword search counts.

The default tokenizer is identifier-aware, because the words that matter most in this domain are
code names. `tx.origin` is kept whole (so an exact match scores) and also split into `tx` and
`origin`; `_beforeTokenTransfer` gives itself plus `before`, `token`, `transfer`; `ERC1967` gives
itself, `erc` and `1967`. Ordinary words are lowercased and stop words dropped. No stemming:
that would need a library, and the quiz can say whether it is missed.
"""

import re
from collections.abc import Callable

Tokenizer = Callable[[str], list[str]]

_STOP_TEXT = """
a an and are as at be been being but by can could did do does for from had has have how if
in into is it its may might more most of on or other our over should so some such than
that the their them then there these they this those to too was we were what when where
which who whom why will with would you your"""
STOP_WORDS = frozenset(_STOP_TEXT.split())

# A run of letters, digits and underscores, optionally joined by dots (tx.origin, msg.sender).
_RAW = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
# The pieces inside an identifier: ACRONYMS, Capitalised or lowercase words, and digit runs.
_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_PLAIN = re.compile(r"[a-z0-9]+")


def _keep(word: str) -> bool:
    if word in STOP_WORDS or len(word) < 2:
        return False
    return not (word.isdigit() and len(word) < 3)  # a stray "20" says nothing


def plain_tokens(text: str) -> list[str]:
    """Lowercase words and numbers only: the baseline the identifier-aware one is compared to."""
    return [w for w in _PLAIN.findall(text.lower()) if _keep(w)]


def identifier_tokens(text: str) -> list[str]:
    out: list[str] = []
    for raw in _RAW.findall(text):
        whole = raw.lower()
        parts = [p.lower() for p in _PART.findall(raw)]
        if parts == [whole]:  # an ordinary word
            if _keep(whole):
                out.append(whole)
            continue
        out.append(whole)  # the identifier itself, so an exact name scores
        out.extend(p for p in parts if _keep(p))
    return out


TOKENIZERS: dict[str, Tokenizer] = {"identifier": identifier_tokens, "plain": plain_tokens}
