"""What a finding is, and how it is classified: the one place the reasoning verifier's definitions
live (Aryaan, 2026-11-06: "make it super clear what each category of bug is defined as").

The classification is Trail of Bits'. Every public report of theirs ends with an appendix that
defines the vulnerability categories (the "Type" of a finding), the severity levels and the
difficulty levels, and every finding in the report carries those three labels. The names below are
theirs; the one-line meanings are paraphrased, not copied. (Their reports are published under
CC-BY-SA-4.0; see `knowledge/README.md`.)

Why it matters for a verifier: ToB's difficulty scale has a level for "the attacker needs
privileged access to the system", and their severity scale has one for "no immediate risk, but
relevant to security best practice". So a flaw only a trusted role can trigger, or a missing
validation with no outsider exploit, is still a finding in their sense, with a high difficulty or a
low severity. A verifier that answers "only the owner can do that, so it is not a bug" is using a
narrower definition than the auditors whose findings it is checked against.
"""

# ruff: noqa: E501
# (the one-line meanings are data and are read as they stand, not wrapped)

CATEGORIES: dict[str, str] = {
    "Access Controls": "a check on who may act is missing or wrong, or a role can do more than it should",
    "Auditing and Logging": "actions or problems are not recorded (events, logs), so they cannot be monitored",
    "Authentication": "the code mistakes who the caller or the signer is",
    "Configuration": "a setting or parameter can be left unset, set unsafely or changed unsafely",
    "Cryptography": "a signature, hash or source of randomness is misused so that integrity or secrecy fails",
    "Data Exposure": "sensitive information can be read by someone who should not read it",
    "Data Validation": "an input, a returned value or stored data is trusted without checking it (bounds, zero, duplicates, units, return values)",
    "Denial of Service": "a function, or the system, can be made to stop working for its users",
    "Error Reporting": "a failure is hidden, misreported or not reported",
    "Patching": "an outdated or vulnerable library or dependency is used",
    "Session Management": "a user already identified is mistaken for another, or an old approval or signature can be reused",
    "Testing": "tests or test method are missing or too weak for what the code does",
    "Timing": "an ordering flaw: a race, a stale value between steps, an action that changes meaning when it happens",
    "Undefined Behavior": "the code or the language leaves a case unspecified (collisions, uninitialised state, unhandled edge cases)",
}  # fmt: skip

SEVERITIES: dict[str, str] = {
    "high": "could affect many users, with serious financial, legal or reputational consequences",
    "medium": "puts user funds or data at risk, with moderate financial or reputational consequences",
    "low": "a small risk, or one the project has not said it cares about",
    "informational": "no immediate risk, but relevant to security best practice",
    "undetermined": "the size of the risk cannot be judged from the code shown",
}

DIFFICULTIES: dict[str, str] = {
    "low": "well known: tools exist or a simple script does it",
    "medium": "the attacker must write an exploit or know the system in depth",
    "high": "the attacker needs privileged access to the system, complex technical knowledge, or other weaknesses first",
    "undetermined": "the effort cannot be judged from the code shown",
}  # fmt: skip

# What is not a finding. ToB lists such items apart from their findings, as code-quality notes
# that "do not have immediate or obvious security implications".
NOT_A_FINDING = (
    "a TODO or a comment that is out of date",
    "naming, style or layout",
    "gas use",
    "a pattern violated (for example checks-effects-interactions) where no state can end up wrong",
    "code that could be marked view, abstract or private",
    "behaviour the code's own comments state as intended, when the effect claimed is exactly the effect stated",
)


def definitions_text() -> str:
    """The block put into the verifier's instructions."""
    lines = ["TYPE (what kind of flaw it is; pick the closest one):"]
    lines += [f"- {name}: {meaning}." for name, meaning in CATEGORIES.items()]
    lines += ["", "SEVERITY (how bad the flaw is if it is real):"]
    lines += [f"- {name}: {meaning}." for name, meaning in SEVERITIES.items()]
    lines += ["", "DIFFICULTY (how hard it is to trigger):"]
    lines += [f"- {name}: {meaning}." for name, meaning in DIFFICULTIES.items()]
    lines += ["", "NOT A FINDING (note these as invalid):"]
    lines += [f"- {item}." for item in NOT_A_FINDING]
    return "\n".join(lines)
