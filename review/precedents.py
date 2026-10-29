"""Attaching audit findings to a reviewed span without trusting a model or a tag (R3).

Three sources, all deterministic, in this order of trust (decision 21):

1. **The hand-checked precedent map** (`knowledge/precedents.toml`): for each taxonomy entry, up to
   three finding ids, each `close` or `related`, or the entry marked `none`. An entry the map lists
   uses only what the map says.
2. **Tags**, only for an entry the map does not list: findings whose `taxonomy` tag names the entry,
   always `related`. A tag is a keyword rule's guess, never a statement that the finding is close.
3. **The fork check**, for `same_code_family`: a finding whose text names at least two distinct,
   non-generic identifiers that this contract declares. It says nothing about patterns; it spots a
   fork of audited code.

The model names none of this. A span carries ids; `review.credit` builds everything shown.
"""

import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from knowledge.findings import Finding, FindingsFile
from knowledge.paths import KNOWLEDGE_DIR
from review.lexer import Lexed
from review.types import MAX_PRECEDENTS, Closeness, PrecedentRef

PRECEDENT_MAP_PATH = KNOWLEDGE_DIR / "precedents.toml"

# Names too common to say anything about a family of code (checked once on four unrelated contracts:
# `onlyOwner` and `safeTransferFrom` were the only overlaps).
GENERIC_NAMES = frozenset(
    {
        "onlyOwner", "safeTransfer", "safeTransferFrom", "safeApprove", "transferFrom",
        "balanceOf", "totalSupply", "allowance", "increaseAllowance", "decreaseAllowance",
        "initialize", "initializer", "transferOwnership", "renounceOwnership", "msgSender",
        "nonReentrant", "whenNotPaused", "whenPaused", "supportsInterface", "tokenURI",
        "ownerOf", "isApprovedForAll", "setApprovalForAll", "getApproved", "permit",
        "DOMAIN_SEPARATOR", "nonces", "decimals", "implementation", "upgradeTo",
        "upgradeToAndCall", "proxiableUUID", "onlyRole", "hasRole", "grantRole", "revokeRole",
        "balanceOfAt", "totalSupplyAt", "mintTo", "burnFrom",
    }
)  # fmt: skip
MIN_FORK_NAMES = 2
_WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{3,}")
_CODE_LIKE = re.compile(r"[a-z][A-Z]|_[A-Za-z]|[A-Za-z]_|[A-Za-z][0-9]")


class PrecedentMapError(ValueError):
    """The map file is unreadable or breaks a rule; the message says which."""


class MapRow(BaseModel, frozen=True):
    close: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    none: bool = False


class PrecedentMap(BaseModel, frozen=True):
    """Entry slug -> row. An empty map is "nobody has checked yet": tags apply to every entry."""

    rows: dict[str, MapRow] = {}

    def row(self, entry_id: str) -> MapRow | None:
        return self.rows.get(entry_id.removeprefix("taxonomy:"))


def load_map(path: Path | None = None) -> PrecedentMap:
    """The map, or an empty one when there is no file. A file that exists but is wrong raises."""
    target = path or PRECEDENT_MAP_PATH
    if not target.exists():
        return PrecedentMap()
    try:
        data = tomllib.loads(target.read_text())
        rows = {slug: MapRow.model_validate(row) for slug, row in data.get("entries", {}).items()}
    except (tomllib.TOMLDecodeError, ValidationError, AttributeError) as err:
        raise PrecedentMapError(f"{target.name}: {err}") from None
    return PrecedentMap(rows=rows)


def check_map(
    pmap: PrecedentMap,
    entry_slugs: Iterable[str],
    finding_ids: Iterable[str],
    *,
    complete: bool = False,
) -> list[str]:
    """Problems with a map, in plain words. `complete` also wants every entry listed or marked
    `none` (the rule for the map Aryaan approves)."""
    slugs, ids = set(entry_slugs), set(finding_ids)
    problems = []
    for slug, row in pmap.rows.items():
        if slug not in slugs:
            problems.append(f"{slug}: not a taxonomy entry")
        listed = [*row.close, *row.related]
        if row.none and listed:
            problems.append(f"{slug}: marked none but lists findings")
        if len(listed) > MAX_PRECEDENTS:
            problems.append(f"{slug}: lists {len(listed)} findings, the most is {MAX_PRECEDENTS}")
        if len(set(listed)) != len(listed):
            problems.append(f"{slug}: lists a finding twice")
        problems += [f"{slug}: unknown finding {i}" for i in listed if i not in ids]
    if complete:
        problems += [f"{s}: neither listed nor marked none" for s in sorted(slugs - set(pmap.rows))]
    return problems


# ---- the fork check -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ForkHit:
    finding_id: str
    names: frozenset[str]  # the identifiers both the finding and the contract carry


def finding_identifiers(finding: Finding) -> frozenset[str]:
    """Words of the finding's text that look like code identifiers (camelCase, snake_case)."""
    text = " ".join(
        [
            finding.title,
            finding.description,
            finding.recommendation,
            finding.affected_contract or "",
        ]
    )
    return frozenset(w for w in _WORD.findall(text) if _CODE_LIKE.search(w))


def declared_names(lexed: Mapping[str, Lexed]) -> frozenset[str]:
    """Every name the contract declares: definitions and their members."""
    names: set[str] = set()
    for result in lexed.values():
        for d in result.definitions:
            names.add(d.name)
            names.update(m.name for m in d.members if m.name)
    return frozenset(names)


def fork_check(names: frozenset[str], files: Sequence[FindingsFile]) -> list[ForkHit]:
    """Findings that name at least `MIN_FORK_NAMES` distinct non-generic identifiers the contract
    declares. Silent on unrelated code; a poor pattern matcher and a fair fork detector."""
    hits = []
    for file in files:
        for finding in file.findings:
            shared = (finding_identifiers(finding) & names) - GENERIC_NAMES
            if len(shared) >= MIN_FORK_NAMES:
                hits.append(ForkHit(finding_id=finding.id, names=frozenset(shared)))
    return sorted(hits, key=lambda h: h.finding_id)


# ---- attaching ----------------------------------------------------------------------------------


def _tagged(entry_id: str, files: Sequence[FindingsFile]) -> list[str]:
    slug = entry_id.removeprefix("taxonomy:")
    return sorted(f.id for file in files for f in file.findings if slug in f.taxonomy)


def attach(
    entry_id: str,
    pmap: PrecedentMap,
    files: Sequence[FindingsFile],
    *,
    forks: Sequence[ForkHit] = (),
    span_text: str = "",
    function: str | None = None,
) -> list[PrecedentRef]:
    """Precedents for one span, at most three. Fork hits come first, and only when one of the names
    the finding shares with this contract is in the span's lines or is its function."""
    out: list[PrecedentRef] = []
    seen: set[str] = set()

    def add(finding_id: str, relation: str, closeness: Closeness) -> None:
        if finding_id not in seen and len(out) < MAX_PRECEDENTS:
            seen.add(finding_id)
            out.append(
                PrecedentRef(finding_id=finding_id, relation=relation, closeness=closeness)  # type: ignore[arg-type]
            )

    words = set(_WORD.findall(span_text)) | ({function} if function else set())
    for hit in forks:
        if hit.names & words:
            add(hit.finding_id, "same_code_family", "close")
    row = pmap.row(entry_id)
    if row is None:
        for finding_id in _tagged(entry_id, files):
            add(finding_id, "same_class", "related")
    elif not row.none:
        for finding_id in row.close:
            add(finding_id, "same_class", "close")
        for finding_id in row.related:
            add(finding_id, "same_class", "related")
    return out
