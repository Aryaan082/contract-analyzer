"""The core functions wrapped in the envelope every tool returns: `{provenance, data}`.

`provenance` is the TARGET's: chain, address and block of the code that was analysed, passed in
from the `get_code` result. `provenance.source` names the library that produced the answer,
`reference-library@<12 hex of its digest>`, so a citation pins which set of references was used.
Each match candidate also carries the reference's own origin (compiled from which release, or
fetched from which chain, address and block).

Seam to Component 1: `extract_selectors` is wired in (give code and no selectors, and they are
read from the code). `get_code` and `lookup_selector` are not built yet; when they exist, a small
adapter feeds their results in (code hex, `{selector: [NameCandidate]}`) and forwards `get_code`'s
provenance. Nothing else in `references/` needs to change.
"""

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence

from pydantic import BaseModel

from chain.errors import ChainError
from chain.models import Provenance, ToolResult
from chain.selectors import extract_selectors
from references.abi import normalize_selector, parse_hex
from references.diff import diff
from references.library import Library, LibraryError, UnknownReference, get_library
from references.match import DEFAULT_LIMIT, Target, match
from references.models import DiffResult, MatchResult, NameCandidate, RiskReport
from references.risk import assess

log = logging.getLogger(__name__)

MAX_CODE_BYTES = 100_000  # contracts are limited to 24,576 bytes; this leaves room
MAX_SELECTORS = 2_000

Names = Mapping[str, Sequence[NameCandidate | str]]


def library_source(library: Library) -> str:
    return f"reference-library@{library.digest[:12]}"


def _provenance(base: Provenance | None, source: str) -> Provenance:
    if base is None:
        return Provenance(chain="unknown", chain_id=None, address=None, block=None, source=source)
    return base.model_copy(update={"source": source})


def parse_selectors(raw: Iterable[str]) -> frozenset[str]:
    selectors = []
    for item in raw:
        try:
            selectors.append(normalize_selector(item))
        except ValueError as err:
            raise ChainError("invalid_selector", str(err)) from None
        if len(selectors) > MAX_SELECTORS:
            raise ChainError("invalid_selector", f"More than {MAX_SELECTORS} selectors given.")
    return frozenset(selectors)


def parse_code(code_hex: str | None) -> bytes | None:
    if code_hex is None:
        return None
    try:
        code = parse_hex(code_hex)
    except ValueError as err:
        raise ChainError("invalid_bytecode", str(err)) from None
    if not code:
        raise ChainError(
            "invalid_bytecode",
            "The code is empty: an externally owned account, or a contract that was destroyed.",
        )
    if len(code) > MAX_CODE_BYTES:
        raise ChainError("invalid_bytecode", f"Code is longer than {MAX_CODE_BYTES} bytes.")
    return code


def build_target(selectors: Iterable[str] | None, code_hex: str | None) -> Target:
    """The selectors given, or, when none are given, those read from the code with evmole."""
    code = parse_code(code_hex)
    if selectors is not None:
        return Target(parse_selectors(selectors), code)
    if code is None:
        raise ChainError("invalid_selector", "Give selectors, or code to read them from.")
    return Target(frozenset(info.selector for info in extract_selectors(code)), code)


def _run[T: BaseModel](
    result_type: type[T],
    compute: Callable[[Library], T],
    provenance: Provenance | None,
    library: Library | None,
) -> ToolResult[T]:
    try:
        library = library or get_library()
    except LibraryError as err:
        error = ChainError("library_invalid", str(err)[:400])
        return ToolResult[result_type](
            provenance=_provenance(provenance, "reference-library@unavailable"),
            data=error.to_tool_error(),
        )
    prov = _provenance(provenance, library_source(library))
    try:
        data = compute(library)
    except ChainError as err:
        return ToolResult[result_type](provenance=prov, data=err.to_tool_error())
    except UnknownReference as err:
        error = ChainError("unknown_reference", str(err))
        return ToolResult[result_type](provenance=prov, data=error.to_tool_error())
    except Exception:
        log.exception("reference tool failed")
        error = ChainError("internal_error", "Unexpected internal error; see the log.")
        return ToolResult[result_type](provenance=prov, data=error.to_tool_error())
    return ToolResult[result_type](provenance=prov, data=data)


def run_match(
    selectors: Iterable[str] | None = None,
    code_hex: str | None = None,
    *,
    provenance: Provenance | None = None,
    limit: int = DEFAULT_LIMIT,
    library: Library | None = None,
) -> ToolResult[MatchResult]:
    def compute(lib: Library) -> MatchResult:
        return match(build_target(selectors, code_hex), lib, limit=limit)

    return _run(MatchResult, compute, provenance, library)


def run_diff(
    selectors: Iterable[str] | None,
    reference_id: str,
    *,
    code_hex: str | None = None,
    names: Names | None = None,
    provenance: Provenance | None = None,
    library: Library | None = None,
) -> ToolResult[DiffResult]:
    def compute(lib: Library) -> DiffResult:
        return diff(build_target(selectors, code_hex).selectors, reference_id, lib, names)

    return _run(DiffResult, compute, provenance, library)


def run_risk(
    selectors: Iterable[str] | None = None,
    code_hex: str | None = None,
    *,
    names: Names | None = None,
    provenance: Provenance | None = None,
    library: Library | None = None,
) -> ToolResult[RiskReport]:
    """Match first (so each signal can say whether the best reference has it too), then assess."""

    def compute(lib: Library) -> RiskReport:
        target = build_target(selectors, code_hex)
        return assess(
            target.selectors,
            lib,
            names=names,
            code=target.code,
            match_result=match(target, lib),
        )

    return _run(RiskReport, compute, provenance, library)
