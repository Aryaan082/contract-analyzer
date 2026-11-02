"""The shapes the verification funnel passes around."""

import hashlib
from dataclasses import dataclass, field

CREDIT_KINDS = ("report", "check")
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
    """Who vouches for a flag: an auditor's report (`ref` is its finding id) or a deterministic
    check (`ref` is the rule id)."""

    kind: str
    ref: str

    def __post_init__(self) -> None:
        if self.kind not in CREDIT_KINDS:
            raise ValueError(f"unknown credit kind {self.kind!r}; use one of {CREDIT_KINDS}")
