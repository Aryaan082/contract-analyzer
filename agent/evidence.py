"""The evidence registry: what the agent may cite, and nothing else.

After every chain or reference tool runs, the runner records the result under a new ID, `T1`,
`T2`, ... (tool, arguments, provenance, data). Knowledge results (taxonomy entries and audit
findings) are recorded under their own stable IDs (`taxonomy:privileged-mint`,
`aave-v3-tob-2021-11:TOB-AAVE-002`), and only when the model was actually shown them in this run.
The citation validator checks every ID in a report against this registry, so a claim can point
only at something the runner saw happen, and the report prints each ID as a real citation: chain,
address, block and source for a tool result; document, finding and page for knowledge.

Source review adds a third kind: `S1`, `S2`, ... are closed records of places a reviewer model
flagged in a contract's source (agent/review_stage.py). They carry `data` (exactly what the writer
was shown) and `record` (the full span, for the validator and the screen, never shown to a model).
"""

import json
import re
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel

from chain.models import Provenance

TOOL_ID = re.compile(r"^T[1-9][0-9]*$")
SOURCE_ID = re.compile(r"^S[1-9][0-9]*$")


class Evidence(BaseModel, frozen=True):
    id: str
    source: Literal["tool", "knowledge", "source"]
    ok: bool  # False for a tool that failed: it records that, and supports no observation
    citation: str  # one line a reader can check
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    provenance: Provenance | None = None
    data: Any = None  # tool: the result as JSON; knowledge: the text the model was shown
    # source only: the full SourceSpan as JSON (quote, note, path). Not part of `text()` and never
    # given to a model; the validator reads its severity and `confirmed_by`, the screen the rest.
    record: Any = None

    def text(self) -> str:
        """Everything this evidence says, lower-cased, for the validator's grounding check."""
        return json.dumps(
            {"provenance": self.provenance, "arguments": self.arguments, "data": self.data},
            default=lambda o: o.model_dump() if isinstance(o, BaseModel) else str(o),
            ensure_ascii=False,
        ).lower()


def tool_citation(tool: str, arguments: dict[str, Any], provenance: Provenance | None) -> str:
    shown = ", ".join(f"{k}={v}" for k, v in arguments.items())
    where = ""
    if provenance is not None:
        where = f" on {provenance.chain}"
        if provenance.address:
            where += f", address {provenance.address}"
        if provenance.block is not None:
            where += f", block {provenance.block}"
        where += f", source {provenance.source}"
    return f"{tool}({shown}){where}"


class EvidenceRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}
        self._tool_count = 0

    def add_tool(
        self,
        tool: str,
        arguments: dict[str, Any],
        provenance: Provenance | None,
        data: Any,
        *,
        ok: bool = True,
    ) -> Evidence:
        self._tool_count += 1
        item = Evidence(
            id=f"T{self._tool_count}",
            source="tool",
            ok=ok,
            citation=tool_citation(tool, arguments, provenance),
            tool=tool,
            arguments=arguments,
            provenance=provenance,
            data=data,
        )
        self._items[item.id] = item
        return item

    def add_knowledge(self, record_id: str, citation: str, text: str) -> Evidence:
        """Record a taxonomy entry or audit finding the model has been shown. Showing the same
        one twice records it once."""
        existing = self._items.get(record_id)
        if existing is not None:
            return existing
        item = Evidence(id=record_id, source="knowledge", ok=True, citation=citation, data=text)
        self._items[record_id] = item
        return item

    def add_source(
        self,
        span_id: str,
        citation: str,
        shown: dict[str, Any],
        record: dict[str, Any],
        provenance: Provenance | None,
    ) -> Evidence:
        """Record a source span the writer has been shown. `shown` is what it saw, `record` the full
        span. Recording the same id twice keeps the first."""
        if not SOURCE_ID.match(span_id):
            raise ValueError(f"{span_id!r} is not a source evidence id")
        existing = self._items.get(span_id)
        if existing is not None:
            return existing
        item = Evidence(
            id=span_id,
            source="source",
            ok=True,
            citation=citation,
            tool="review_source",
            provenance=provenance,
            data=shown,
            record=record,
        )
        self._items[span_id] = item
        return item

    def sources(self) -> list[Evidence]:
        return [e for e in self if e.source == "source"]

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._items

    def __iter__(self) -> Iterator[Evidence]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def tool_results(self, tool: str | None = None) -> list[Evidence]:
        return [e for e in self if e.source == "tool" and (tool is None or e.tool == tool) and e.ok]
