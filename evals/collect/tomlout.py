"""Writing cards as TOML. Strings go through `json.dumps`, which gives a valid TOML basic string
(backslashes and quotes escaped, non-ASCII as \\u escapes), so a name or a pattern cannot break the
file or smuggle in another table."""

import json
from typing import Any

ORDER = (
    "id",
    "chain",
    "address",
    "block",
    "klass",
    "split",
    "group",
    "reasons",
    "traits",
    "implementation",
    "acceptable_verdicts",
    "requires",
    "forbids",
    "facts",
    "must_not_say",
    "min_not_checked",
    "label_source",
    "reviewed_by",
    "tool_use",
    "family_size",
    "code_hash",
    "source",
    "reason",
)
ALWAYS = {"id", "chain", "address", "klass", "split", "group", "label_source", "reviewed_by"}


def value(item: Any) -> str:
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, int):
        return str(item)
    if isinstance(item, str):
        return json.dumps(item)
    if isinstance(item, (list, tuple)):
        return "[" + ", ".join(value(x) for x in item) + "]"
    raise TypeError(f"cannot write {type(item).__name__} as TOML")


def _left_out(key: str, item: Any) -> bool:
    """True for a field at its default, which is not written (the loader fills it in)."""
    if key in ALWAYS or key == "block":
        return item is None
    if key == "min_not_checked":
        return item == 0
    if key == "family_size":
        return item == 1
    return item in (None, "", [], (), False)


def card_table(card: dict[str, Any]) -> str:
    lines = ["[[contract]]"]
    for key in ORDER:
        if key in card and not _left_out(key, card[key]):
            name = "class" if key == "klass" else key
            lines.append(f"{name} = {value(card[key])}")
    return "\n".join(lines)


def cards_file(header: str, cards: list[dict[str, Any]]) -> str:
    comment = "".join(f"# {line}\n" if line else "#\n" for line in header.splitlines())
    return comment + "\n" + "\n\n".join(card_table(c) for c in cards) + "\n"
