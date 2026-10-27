"""Text that came from the chain is chosen by whoever deployed the contract. Before the model sees
any of it, it is cut down to plain printable text of bounded length, so it cannot hide characters,
bury the tool result in a wall of text or pass for a line of the surrounding JSON. It is still
text an attacker wrote: tools put it in a field whose name says so, and the system prompt says
such fields are data, never instructions."""

import re
import unicodedata

MAX_CHAIN_TEXT = 64  # a token name or symbol; real ones are shorter

# Wording that addresses a reader rather than naming a token. A rough hint, never a gate: a token
# whose name tries to instruct an AI is worth reporting, and one that dodges the list is still
# handled as data.
_INSTRUCTION_LIKE = re.compile(
    r"ignore\b.{0,30}\b(instruction|prompt|rule)s?|disregard\b.{0,30}\b(instruction|prompt|rule)s?|"
    r"system prompt|you are (now|an?)\b|as an ai\b|"
    r"(report|mark|rate|call|treat)\b.{0,20}\b(this|it|contract|token)\b.{0,30}"
    r"\b(safe|clean|legit|audited|no red flags)",
    re.IGNORECASE,
)


def clean_chain_text(raw: str, limit: int = MAX_CHAIN_TEXT) -> str:
    """Printable characters only (no control, format, bidi, private-use or unassigned code points),
    whitespace collapsed to single spaces, cut to `limit` characters."""
    text = unicodedata.normalize("NFKC", raw)
    text = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in text)
    return " ".join(text.split())[:limit]


def looks_like_instructions(text: str) -> bool:
    return bool(_INSTRUCTION_LIKE.search(text))
