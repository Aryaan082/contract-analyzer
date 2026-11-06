"""Step 2 of the funnel: a flag a deterministic check can confirm (no model, no simulation).

A rule has two patterns and both must match: one on the reviewer's sentence (what the flag claims)
and one on the function's code (the construct that makes the claim true). The credit is `check`, a
weaker word than `sandbox`: it says the construct the flag names is there, not that it can be
exploited. This is a starting set; a rule is added only when its construct IS the claim.
"""

import re
from dataclasses import dataclass

from verify.types import Candidate

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_NUMBER = re.compile(r"^\s*\d+\|\s?", re.M)  # the line numbers `verify.source` puts in front


@dataclass(frozen=True)
class Rule:
    id: str
    claim: re.Pattern[str]  # what the flag's sentence says
    construct: re.Pattern[str]  # what the code has


RULES = (
    Rule(
        "tx-origin-auth",
        re.compile(r"tx\.origin", re.I),
        re.compile(r"tx\.origin\s*[=!]=|[=!]=\s*tx\.origin|require\s*\([^;]*tx\.origin"),
    ),
    # `addr.transfer(x)` and `addr.send(x)` forward 2300 gas and take ONE argument; an ERC-20
    # `token.transfer(to, x)` takes two and is not this.
    Rule(
        "transfer-gas-stipend",
        re.compile(r"\b(transfer|send)\s*\(|2300|gas stipend", re.I),
        re.compile(r"\.\s*(?:transfer|send)\s*\(\s*[^,()]+\)"),
    ),
)  # fmt: skip


def code_without_comments(code: str) -> str:
    return _BLOCK_COMMENT.sub("", _LINE_COMMENT.sub("", _NUMBER.sub("", code)))


def mechanical_rule(candidate: Candidate, code: str) -> str | None:
    """The id of the first rule whose two patterns both match, else None."""
    body = code_without_comments(code)
    for rule in RULES:
        if rule.claim.search(candidate.note) and rule.construct.search(body):
            return rule.id
    return None
