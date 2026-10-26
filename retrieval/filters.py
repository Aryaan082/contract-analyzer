"""Metadata filters, applied before ranking so the top results are always a full page.

A filter on a field a chunk does not have drops that chunk: `severity="high"` means audit
findings rated high, so no taxonomy entry comes back. `kind` matches an entry's `applies_to`
(an entry that applies to `any` always matches); findings have no kind and are dropped by it.
"""

from typing import Literal

from pydantic import BaseModel, Field

from retrieval.chunks import Chunk

_MONTH = r"^\d{4}-\d{2}$"


class Filters(BaseModel, frozen=True):
    record_type: Literal["taxonomy", "finding"] | None = None
    severity: str | None = None
    protocol: str | None = None
    group: str | None = None
    kind: str | None = None
    date_from: str | None = Field(default=None, pattern=_MONTH)  # "YYYY-MM", inclusive
    date_to: str | None = Field(default=None, pattern=_MONTH)  # "YYYY-MM", inclusive


def matches(chunk: Chunk, filters: Filters | None) -> bool:
    if filters is None:
        return True
    f = filters
    if f.record_type and chunk.record_type != f.record_type:
        return False
    if f.severity and chunk.severity != f.severity:
        return False
    if f.protocol and chunk.protocol != f.protocol:
        return False
    if f.group and chunk.group != f.group:
        return False
    if f.kind and not (chunk.record_type == "taxonomy" and {f.kind, "any"} & set(chunk.applies_to)):
        return False
    if f.date_from and not (chunk.date and chunk.date >= f.date_from):
        return False
    return not (f.date_to and not (chunk.date and chunk.date <= f.date_to))
