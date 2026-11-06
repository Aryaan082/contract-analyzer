"""The shapes the verification funnel passes around. No model text is stored in a verdict: the
reason is always one of our own fixed words."""

import hashlib
from dataclasses import dataclass, field

CREDIT_KINDS = ("report", "check", "sandbox")
STATUSES = ("confirmed", "not_confirmed", "unverifiable")
LAYERS = ("plain", "pipeline")  # the readers that raise flags; two agreeing ranks a flag up


@dataclass(frozen=True)
class Candidate:
    """A flag at a place. `note` is the reviewer's sentence: untrusted text, never obeyed."""

    report: str
    file: str  # the display path of the file in the bundle
    contract: str
    function: str
    start_line: int
    end_line: int
    note: str
    severity: str = ""
    layers: frozenset[str] = field(default_factory=frozenset)

    @property
    def place(self) -> tuple[str, str, str, str]:
        return (self.report, self.file, self.contract, self.function)

    @property
    def id(self) -> str:
        """Stable across runs: the place, the lines and the sentence."""
        raw = "\0".join([*self.place, str(self.start_line), str(self.end_line), self.note])
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Credit:
    """Who vouches for a flag: an auditor's report (`ref` is its finding id), a deterministic
    check (`ref` is the rule id) or a sandbox test (`ref` is the test's hash)."""

    kind: str
    ref: str

    def __post_init__(self) -> None:
        if self.kind not in CREDIT_KINDS:
            raise ValueError(f"unknown credit kind {self.kind!r}; use one of {CREDIT_KINDS}")


@dataclass(frozen=True)
class Verdict:
    """What the sandbox made of one flag. `not_confirmed` means no working test in the attempts
    allowed, not that the flag is false; `unverifiable` means no test could be run at all (does not
    compile, a tool is missing) or the model said a unit test cannot show it."""

    status: str
    reason: str  # fixed words of ours
    attempts: int = 0
    test_sha: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}; use one of {STATUSES}")

    def credit(self) -> Credit | None:
        return Credit("sandbox", self.test_sha) if self.status == "confirmed" else None
