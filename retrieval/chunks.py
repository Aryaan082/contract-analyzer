"""Chunks: one per taxonomy entry or kept finding, built from Component 3's loaders.

A chunk is one reference card, not a piece of the contract being analysed. Its `text` is what
gets embedded, reranked and shown; the recipe (recipes.toml) says which record fields go into it.
Everything in a chunk came from a hand-written file or a PDF, so it is untrusted data: whoever
puts it into a prompt must quote it (see search.render_quoted), never obey it.
"""

import hashlib
import re
import tomllib
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from knowledge.findings import Finding, all_findings
from knowledge.taxonomy import TaxonomyEntry, load_taxonomy
from retrieval.paths import RECIPES_CONFIG

RecordType = Literal["taxonomy", "finding"]

ENTRY_FIELDS = (
    "title",
    "summary",
    "description",
    "what_to_check",
    "look_for_in_code",
    "example_vulnerable",
    "example_safe",
)
FINDING_FIELDS = (
    "title",
    "category",
    "protocol",
    "difficulty",
    "affected_contract",
    "description",
    "recommendation",
)

# Component 3 leaves this marker where a code listing was cut; it carries no meaning.
_LISTING_MARKER = re.compile(r"\[code listing omitted\]\s*")


class RecipeError(Exception):
    """A recipe is missing or names a field that does not exist."""


class Recipe(BaseModel, frozen=True):
    name: str
    entry: tuple[str, ...]
    finding: tuple[str, ...]


def load_recipe(name: str = "default") -> Recipe:
    raw = tomllib.loads(RECIPES_CONFIG.read_text())
    if name not in raw:
        raise RecipeError(f"no recipe called {name!r}; have {sorted(raw)}")
    recipe = Recipe(name=name, **raw[name])
    for field in recipe.entry:
        if field not in ENTRY_FIELDS:
            raise RecipeError(f"recipe {name!r}: {field!r} is not a taxonomy entry field")
    for field in recipe.finding:
        if field not in FINDING_FIELDS:
            raise RecipeError(f"recipe {name!r}: {field!r} is not a finding field")
    return recipe


class Citation(BaseModel, frozen=True):
    """What the agent cites: the document, the record's ID and, for a finding, the page."""

    document: str
    id: str
    page: int | None = None
    url: str | None = None


class Chunk(BaseModel, frozen=True):
    id: str
    record_type: RecordType
    title: str
    text: str
    # Metadata for filters and display; never part of `text`.
    group: str | None = None
    applies_to: tuple[str, ...] = ()
    visible_in: tuple[str, ...] = ()
    severity: str | None = None
    protocol: str | None = None
    date: str | None = None
    report: str | None = None
    tags: tuple[str, ...] = Field(default=(), description="taxonomy tags on a finding")
    citation: Citation


def _compose(values: Sequence[str | None]) -> str:
    return "\n\n".join(_LISTING_MARKER.sub("", v).strip() for v in values if v and v.strip())


def _entry_chunk(entry: TaxonomyEntry, recipe: Recipe) -> Chunk:
    return Chunk(
        id=entry.id,
        record_type="taxonomy",
        title=entry.title,
        text=_compose([getattr(entry, f) for f in recipe.entry]),
        group=entry.group,
        applies_to=tuple(entry.applies_to),
        visible_in=tuple(entry.visible_in),
        citation=Citation(document="Vulnerability taxonomy (knowledge/taxonomy)", id=entry.id),
    )


def _finding_chunk(finding: Finding, recipe: Recipe) -> Chunk:
    return Chunk(
        id=finding.id,
        record_type="finding",
        title=finding.title,
        text=_compose([getattr(finding, f) for f in recipe.finding]),
        severity=finding.severity,
        protocol=finding.protocol,
        date=finding.date,
        report=finding.report,
        tags=tuple(finding.taxonomy),
        citation=Citation(
            document=f"{finding.auditor}, {finding.protocol} security review ({finding.date})",
            id=finding.finding_id,
            page=finding.page,
            url=finding.cite_url,
        ),
    )


def build_chunks(recipe: Recipe | str = "default") -> list[Chunk]:
    """Every taxonomy entry, then every kept finding, in a fixed order."""
    recipe = load_recipe(recipe) if isinstance(recipe, str) else recipe
    entries = [_entry_chunk(e, recipe) for _, e in sorted(load_taxonomy().items())]
    findings = [_finding_chunk(f, recipe) for f in all_findings()]
    return [*entries, *findings]


def fingerprint(chunks: Sequence[Chunk], *extra: str) -> str:
    """A hash of every chunk's ID and text (plus `extra`, such as the model name): when it
    changes, a saved index no longer matches the records."""
    digest = hashlib.sha256()
    for part in extra:
        digest.update(part.encode() + b"\x00")
    for chunk in chunks:
        digest.update(f"{chunk.id}\x00{chunk.text}\x00".encode())
    return digest.hexdigest()
