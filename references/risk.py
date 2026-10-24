"""Risk heuristics: which capabilities a contract appears to have, and how we know.

What this can say, from bytecode alone: "a function with selector X exists, and X equals the hash
of mint(address,uint256)". What it cannot say: who may call it, or what it does. So every signal
carries the strength of its evidence instead of a conclusion, and there is no severity here; that
judgement belongs to the report that weighs all the evidence.

For each selector the name is taken from the strongest source available, and only that one:
1. the library (a reference's ABI, from source): trusted;
2. the built-in risky-signature list, matched by selector;
3. outside candidates, which are suggestions.
Because a library name settles the question, junk collision names offered for `transfer`
(`0xa9059cbb`) are never even looked at.
"""

from collections.abc import Mapping, Sequence

from references.bytecode import scan_opcodes
from references.library import Library
from references.models import (
    MatchResult,
    NameCandidate,
    OpcodeScan,
    RiskReport,
    Signal,
    SignalKind,
    Strength,
)
from references.names import clean_candidates
from references.risk_patterns import KIND_ORDER, RISKY_SELECTORS, kinds_for_name

LIMITATIONS = (
    "A selector is a 4-byte ID. Finding one shows a function with that ID exists; it does not show "
    "who may call it or what it does.",
    "A name for a selector is a candidate: anyone can create a function name that hashes to a "
    "chosen selector.",
    "Opcode hits come from a linear scan and can come from data bytes: evidence, not proof.",
    "No signals does not mean the contract is safe.",
)


def _statement(
    kind: SignalKind, strength: Strength, selector: str | None, name: str | None, extra: str
) -> str:
    if strength == "reference_name":
        text = f"Function {selector} is {name}, a name the reference library knows from source."
    elif strength == "selector_match":
        text = f"Selector {selector} equals {name}; a function with that ID is present."
    elif strength == "candidate_name":
        text = f"Selector {selector} has the candidate name {name} in a selector database; a hint."
    else:
        text = f"Selector {selector} has only a spam-filtered candidate name {name}; weak evidence."
    return f"{text} Capability: {kind}.{extra}"


def _signals_for_selector(
    selector: str,
    library: Library,
    compared: frozenset[str] | None,
    compared_id: str | None,
    candidates: Sequence[NameCandidate],
) -> tuple[list[Signal], bool]:
    """Signals for one selector, and whether any source could name it at all."""
    in_reference = None if compared is None else selector in compared
    suffix = " Also present in the matched reference." if in_reference else ""

    def signal(kind: SignalKind, strength: Strength, name: str) -> Signal:
        return Signal(
            kind=kind,
            strength=strength,
            selector=selector,
            name=name,
            in_reference=in_reference,
            statement=_statement(kind, strength, selector, name, suffix),
        )

    known = library.names_for(selector)
    if known:
        # Prefer the matched reference's own spelling, else any library entry's.
        _, signature = next((k for k in known if k[0] == compared_id), known[0])
        kinds = kinds_for_name(signature)
        if selector in RISKY_SELECTORS and RISKY_SELECTORS[selector][0] not in kinds:
            kinds.append(RISKY_SELECTORS[selector][0])
        return [signal(k, "reference_name", signature) for k in kinds], True
    if selector in RISKY_SELECTORS:
        kind, signature = RISKY_SELECTORS[selector]
        return [signal(kind, "selector_match", signature)], True
    best: dict[SignalKind, Signal] = {}
    for candidate in candidates:
        strength: Strength = "weak_candidate_name" if candidate.filtered else "candidate_name"
        for kind in kinds_for_name(candidate.name):
            if kind not in best or best[kind].strength == "weak_candidate_name":
                best[kind] = signal(kind, strength, candidate.name)
    return list(best.values()), bool(candidates)


def _opcode_signals(code: bytes, compared_code: bytes | None) -> tuple[list[Signal], OpcodeScan]:
    scan = scan_opcodes(code)
    reference_scan = scan_opcodes(compared_code) if compared_code is not None else None
    signals = []
    for kind, label, count, offsets, ref_count in (
        (
            "delegatecall_opcode",
            "DELEGATECALL",
            scan.delegatecall_count,
            scan.delegatecall_offsets,
            reference_scan.delegatecall_count if reference_scan else None,
        ),
        (
            "selfdestruct_opcode",
            "SELFDESTRUCT",
            scan.selfdestruct_count,
            scan.selfdestruct_offsets,
            reference_scan.selfdestruct_count if reference_scan else None,
        ),
    ):
        if not count:
            continue
        in_reference = None if ref_count is None else ref_count > 0
        suffix = " Also present in the matched reference." if in_reference else ""
        signals.append(
            Signal(
                kind=kind,
                strength="opcode_scan",
                selector=None,
                name=label,
                offsets=offsets,
                in_reference=in_reference,
                statement=(
                    f"The code has {count} {label} instruction(s) outside PUSH data and the "
                    f"metadata tail, first at offset {offsets[0]}. A linear scan: evidence that "
                    f"it may be reachable, not proof.{suffix}"
                ),
            )
        )
    return signals, scan


def assess(
    selectors: frozenset[str],
    library: Library,
    *,
    names: Mapping[str, Sequence[NameCandidate | str]] | None = None,
    code: bytes | None = None,
    match_result: MatchResult | None = None,
) -> RiskReport:
    """Heuristic signals for a target.

    `names` maps selectors to outside candidate names (leave None if no lookup was done). With a
    `match_result`, each signal also says whether the best-matching reference has the same thing,
    since a capability a known contract is built with (Tether's blacklist) is a different finding
    from one a contract added.
    """
    compared_id = None
    if match_result is not None and match_result.quality != "none" and match_result.candidates:
        compared_id = match_result.candidates[0].reference_id
    compared = library.get(compared_id) if compared_id else None
    cleaned, dropped = clean_candidates(names, selectors)

    signals: list[Signal] = []
    unnamed = []
    for selector in sorted(selectors):
        found, named = _signals_for_selector(
            selector,
            library,
            compared.selectors if compared else None,
            compared_id,
            cleaned.get(selector, ()),
        )
        signals += found
        if not named:
            unnamed.append(selector)

    scan = None
    if code is not None:
        opcode_signals, scan = _opcode_signals(code, compared.code if compared else None)
        signals += opcode_signals

    signals.sort(key=lambda s: (KIND_ORDER.index(s.kind), s.selector or "", s.strength))
    return RiskReport(
        signals=tuple(signals),
        unnamed_selectors=tuple(unnamed) if names is not None else (),
        names_supplied=names is not None,
        names_dropped=dropped,
        opcode_scan=scan,
        compared_with=compared_id,
        limitations=LIMITATIONS,
    )
