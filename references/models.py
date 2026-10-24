"""Data shapes: the stored reference records and what the tools return."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

Selector = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-f]{8}$")]


# --- stored records (one JSON file each in references/data/) ---------------------------------


class Function(BaseModel, frozen=True):
    selector: Selector
    signature: str


class CompiledOrigin(BaseModel, frozen=True):
    """Built from source by `python -m references.dev.build`."""

    type: Literal["compiled"] = "compiled"
    repository: str
    tag: str
    commit: str
    wrapper: str  # our file that makes the (often abstract) library contract concrete
    solc: str
    optimizer_runs: int
    evm_version: str
    forge: str


class DeployedOrigin(BaseModel, frozen=True):
    """Fetched from a chain by `python -m references add`."""

    type: Literal["deployed"] = "deployed"
    chain: str
    chain_id: int
    address: str
    block: int
    abi_source: str


class SpecOrigin(BaseModel, frozen=True):
    """Bytes written out in a standard's text."""

    type: Literal["spec"] = "spec"
    standard: str
    url: str


Origin = Annotated[CompiledOrigin | DeployedOrigin | SpecOrigin, Field(discriminator="type")]


class Reference(BaseModel, frozen=True):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    family: str  # free-form grouping label, e.g. "erc20"
    # "contract": a whole program with code, compared by function list and by code.
    # "module": a reusable piece (Ownable, Pausable) that only supplies its function list, to name
    # the extras of a contract that includes it.
    kind: Literal["contract", "module"]
    origin: Origin
    source_url: str
    functions: tuple[Function, ...]
    code: str | None = None  # 0x + hex, runtime bytecode as built or as found on chain
    code_hash: str | None = None  # keccak256 of the code
    # (start, length) spans that legitimately differ between copies of the same program:
    # immutables, an address embedded by a clone factory. Known only for programs we build.
    variable_ranges: tuple[tuple[int, int], ...] = ()
    notes: str = ""

    @model_validator(mode="after")
    def _code_matches_kind(self) -> Self:
        has_code = self.code is not None and self.code_hash is not None
        if self.kind == "contract" and not has_code:
            raise ValueError("a contract needs code and code_hash")
        if self.kind == "module" and (self.code is not None or self.code_hash is not None):
            raise ValueError("a module has no code")
        return self


# --- results ---------------------------------------------------------------------------------


class NameCandidate(BaseModel, frozen=True):
    """A possible signature for a selector, from outside the library (a selector database).
    A candidate, never a fact: anyone can create a function name that hashes to a chosen selector.
    """

    name: str
    filtered: bool = False  # the database marked it as spam
    verified: bool = False  # the name was seen in a verified contract


class SelectorOverlap(BaseModel, frozen=True):
    shared: int
    reference_total: int
    target_total: int
    jaccard: float  # shared / (everything either has)
    # Share of the reference's functions that the target has: 1.0 means the target implements all
    # of it, whatever else it adds. None when the reference has no functions (a proxy).
    reference_coverage: float | None
    extras: int  # target has, reference lacks
    missing: int  # reference has, target lacks


CodeRelation = Literal[
    "identical",
    "identical_ignoring_metadata",
    "identical_ignoring_variable_bytes",
    "different",
]


class CodeComparison(BaseModel, frozen=True):
    relation: CodeRelation
    # Constants (event topics, error selectors, strings) of the reference that also appear in the
    # target, as a share of the reference's, and the same as a Jaccard score. A rough hint only:
    # a different compiler or optimizer moves these scores a lot (see references/README.md).
    reference_constants_found: float | None
    constants_jaccard: float | None
    reference_solc: str | None
    target_solc: str | None


class Candidate(BaseModel, frozen=True):
    reference_id: str
    name: str
    family: str
    origin: Origin
    selectors: SelectorOverlap
    code: CodeComparison | None


MatchQuality = Literal["exact", "close", "partial", "none"]


class TargetSummary(BaseModel, frozen=True):
    selector_count: int
    code_size: int | None
    code_hash: str | None
    solc: str | None
    metadata_found: bool | None


class MatchResult(BaseModel, frozen=True):
    target: TargetSummary
    quality: MatchQuality
    candidates: tuple[Candidate, ...]
    notes: tuple[str, ...]


class KnownAs(BaseModel, frozen=True):
    reference_id: str
    signature: str


class DiffRow(BaseModel, frozen=True):
    selector: Selector
    status: Literal["extra", "missing", "shared"]
    reference_signature: str | None  # from the reference's own ABI
    also_in: tuple[KnownAs, ...]  # other library entries that have this selector (trusted names)
    candidates: tuple[NameCandidate, ...]  # outside suggestions, checked to hash to the selector


class DiffResult(BaseModel, frozen=True):
    reference_id: str
    extras: tuple[DiffRow, ...]
    missing: tuple[DiffRow, ...]
    shared: tuple[DiffRow, ...]
    unnamed_extras: tuple[Selector, ...]  # extras no library entry or candidate can name
    names_dropped: int  # candidates rejected as malformed, duplicate or not hashing to the selector


class OpcodeScan(BaseModel, frozen=True):
    delegatecall_count: int
    delegatecall_offsets: tuple[int, ...]
    selfdestruct_count: int
    selfdestruct_offsets: tuple[int, ...]
    metadata_skipped: bool
    truncated_push: bool
    bytes_scanned: int


SignalKind = Literal[
    "mint",
    "blacklist",
    "fee_setter",
    "pause",
    "owner_withdraw",
    "upgrade",
    "destroy",
    "delegatecall_opcode",
    "selfdestruct_opcode",
]

# How the evidence was obtained, strongest first:
# reference_name: the library knows this selector's signature from source.
# selector_match: the selector equals the hash of a signature on the built-in risky list.
# candidate_name: only an outside selector database suggests the name.
# weak_candidate_name: only a spam-filtered suggestion.
# opcode_scan: found by walking the code.
Strength = Literal[
    "reference_name", "selector_match", "candidate_name", "weak_candidate_name", "opcode_scan"
]


class Signal(BaseModel, frozen=True):
    kind: SignalKind
    strength: Strength
    selector: Selector | None
    name: str | None
    offsets: tuple[int, ...] = ()
    in_reference: bool | None  # also true of the matched reference; None if nothing to compare
    statement: str


class RiskReport(BaseModel, frozen=True):
    signals: tuple[Signal, ...]
    unnamed_selectors: tuple[Selector, ...]
    names_supplied: bool
    names_dropped: int
    opcode_scan: OpcodeScan | None
    compared_with: str | None  # reference id behind each signal's `in_reference`
    limitations: tuple[str, ...]


class LibraryStats(BaseModel, frozen=True):
    digest: str
    contracts: int
    modules: int
    compiled: int
    deployed: int
    spec: int
    functions: int
    distinct_selectors: int
