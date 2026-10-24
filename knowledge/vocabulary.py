"""The words the tools emit, read from the tools themselves.

A taxonomy entry's signals are only useful if they are words a tool can actually say. They are
built here from the tools' own type definitions, not copied, so renaming a verdict or a signal kind
in `chain/` or `references/` makes the taxonomy test fail instead of silently going stale.

Signal forms:  `<source>.<field>=<value>`  for a fixed vocabulary, and  `<source>.<field>`  for a
field the tools report without fixing a value (a tax in basis points, the near-miss list).
"""

from typing import get_args

from chain.identify import Identity, Implementation, Summary
from chain.kinds import KINDS
from chain.registry import Status
from chain.simulate import SellSimulation
from references.models import MatchQuality, SignalKind


def _literal(model, field: str) -> frozenset[str]:
    return frozenset(get_args(model.model_fields[field].annotation))


def categorical_signals() -> dict[str, frozenset[str]]:
    """`<source>.<field>` -> the values a tool can give it."""
    return {
        "identify.summary": frozenset(get_args(Summary)),
        "identify.kind": frozenset(spec.name for spec in KINDS),
        "identify.proxy": _literal(Implementation, "found_by"),
        "registry.status": frozenset(get_args(Status)),
        "risk.kind": frozenset(get_args(SignalKind)),
        "match.quality": frozenset(get_args(MatchQuality)),
        "sell.verdict": _literal(SellSimulation, "verdict"),
    }


def field_signals() -> frozenset[str]:
    """Signals that name a reported field rather than a value."""
    required = {
        "identify.near_misses": "near_misses" in Identity.model_fields,
        "sell.buy_tax_bps": "buy_tax_bps" in SellSimulation.model_fields,
        "sell.sell_tax_bps": "sell_tax_bps" in SellSimulation.model_fields,
        "sell.round_trip_loss_bps": "round_trip_loss_bps" in SellSimulation.model_fields,
    }
    return frozenset(name for name, exists in required.items() if exists)


def identify_kinds() -> frozenset[str]:
    return frozenset(spec.name for spec in KINDS)


def unknown_signals(signals: tuple[str, ...]) -> list[str]:
    """Signals that no tool can emit."""
    categorical, fields = categorical_signals(), field_signals()
    bad = []
    for signal in signals:
        name, _, value = signal.partition("=")
        if value:
            if value not in categorical.get(name, frozenset()):
                bad.append(signal)
        elif name not in fields:
            bad.append(signal)
    return bad
