"""Crediting the audit a pattern resembles: the card, its link and its wording (R3).

A credit line or a link reaches the screen only through `credit_for(finding_id)`, which reads our
own records (`knowledge/findings/*.json`) and builds every field. The model names no finding at all:
ids are attached by `review.precedents`, the evidence record holds ids and no URL, and an unknown id
gives no card ("precedent unavailable"), never a guess.

What a card shows (decision 22): the finding's title, one verbatim sentence of its description (at
most 280 characters), the auditor and report, the page, the licence with its link, and a link to the
report's GitHub page for the pinned commit. The page anchor is not honoured there, so the page
number is printed beside the link. No code listings, no exploit scenario, no recommendation.

The header sentences are fixed templates (5.7.4). They never say a contract "has the bug", "is
vulnerable", "was audited", or that anything was "confirmed" or "found in this contract"; a test
checks every rendered string against `FORBIDDEN_PHRASES`.
"""

import re
from collections.abc import Sequence
from functools import cache

from knowledge.findings import Finding, FindingsFile, load_findings
from knowledge.taxonomy import TaxonomyEntry, load_taxonomy
from review.types import MAX_EXCERPT_CHARS, Credit, PrecedentCard, PrecedentRef

REFERENCE_HOSTS = frozenset(
    {
        "swcregistry.io",
        "docs.openzeppelin.com",
        "eips.ethereum.org",
        "docs.soliditylang.org",
        "docs.chain.link",
    }
)

CHANGES = (
    "Excerpt of the description. Code listings, exploit scenario and recommendations not shown."
)
FOOTER = "A model's reading, not proof. That audit did not cover this contract."
NO_PRECEDENT = "No audit finding in our collection illustrates this pattern"

HEADERS = {
    ("same_class", "close"): (
        "Resembles {title} (as found by {auditor} in {report}, finding {label}, p. {page})"
    ),
    ("same_class", "related"): (
        "Resembles {title} (a related finding by {auditor} in {report}, finding {label}, p. {page})"
    ),
    ("same_code_family", "close"): (
        "Resembles {title}; this code shares names with {report}, which {auditor} reviewed "
        "(finding {label}, p. {page}). Check whether this contract fixed it"
    ),
    ("same_code_family", "related"): (
        "Resembles {title}; this code shares names with {report}, which {auditor} reviewed "
        "(finding {label}, p. {page}). Check whether this contract fixed it"
    ),
}
SEVERITY_LINE = "rated {severity} by {auditor} in that report"

# Wording no template or rendered string may contain (5.7.4), compared case-insensitively.
FORBIDDEN_PHRASES = (
    "has the bug",
    "is vulnerable",
    "audited",
    "confirmed",
    "found in this contract",
    "the audit found",
)

_RAW = re.compile(
    r"^https://raw\.githubusercontent\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/([0-9a-f]{40})/"
    r"([A-Za-z0-9._/-]+\.pdf)$"
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
_LISTING_MARK = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)")
_SPACE = re.compile(r"\s+")


def blob_url(raw_url: str) -> str | None:
    """The report's GitHub page for the same commit, from the stored raw URL by a fixed rewrite."""
    match = _RAW.match(raw_url)
    if not match:
        return None
    repo, commit, path = match.groups()
    return f"https://github.com/{repo}/blob/{commit}/{path}"


def excerpt_of(description: str) -> str:
    """The first sentence of the description, verbatim, listing marks removed and whitespace
    folded. Cut at a word and marked with an ellipsis when it is over 280 characters."""
    first = next((p for p in description.split("\n\n") if p.strip()), "")
    first = _SPACE.sub(" ", _LISTING_MARK.sub("", first.strip())).strip()
    sentence = _SENTENCE_END.split(first, maxsplit=1)[0]
    if len(sentence) <= MAX_EXCERPT_CHARS:
        return sentence
    cut = sentence[: MAX_EXCERPT_CHARS - 1].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


@cache
def default_files() -> tuple[FindingsFile, ...]:
    return tuple(load_findings())


def _lookup(finding_id: str, files: Sequence[FindingsFile]) -> tuple[Finding, FindingsFile] | None:
    for file in files:
        for finding in file.findings:
            if finding.id == finding_id:
                return finding, file
    return None


def credit_for(finding_id: str, files: Sequence[FindingsFile] | None = None) -> Credit | None:
    """Everything a card says about one finding, or None if the id is not in our records or its
    link cannot be built. This is the only place a precedent link or credit line is made."""
    found = _lookup(finding_id, files if files is not None else default_files())
    if found is None:
        return None
    finding, file = found
    link = blob_url(finding.url)
    if link is None:
        return None
    return Credit(
        auditor=finding.auditor,
        report_title=f"{finding.protocol} security review",
        protocol=finding.protocol,
        date=finding.date,
        finding_label=finding.finding_id,
        severity_by_auditor=finding.severity,
        title=finding.title,
        page=finding.page,
        page_end=finding.page_end if finding.page_end != finding.page else None,
        excerpt=excerpt_of(finding.description),
        link=link,
        licence=file.source.licence,
        licence_url=file.source.licence_url,
        changes=CHANGES,
    )


def card_for(
    ref: PrecedentRef, files: Sequence[FindingsFile] | None = None
) -> PrecedentCard | None:
    credit = credit_for(ref.finding_id, files)
    if credit is None:
        return None
    return PrecedentCard(
        finding_id=ref.finding_id, relation=ref.relation, closeness=ref.closeness, credit=credit
    )


# ---- the wording --------------------------------------------------------------------------------


def header_for(entry_title: str, card: PrecedentCard | None) -> str:
    """The card's header sentence: a template filled from our records."""
    if card is None:
        return f"Resembles {entry_title}. {NO_PRECEDENT}"
    credit = card.credit
    return HEADERS[(card.relation, card.closeness)].format(
        title=entry_title,
        auditor=credit.auditor,
        report=credit.report_title,
        label=credit.finding_label,
        page=credit.page,
    )


def severity_line(card: PrecedentCard) -> str:
    return SEVERITY_LINE.format(
        severity=card.credit.severity_by_auditor, auditor=card.credit.auditor
    )


def rendered_strings(entry_title: str, card: PrecedentCard | None) -> list[str]:
    """Every sentence the renderer writes for one card, for the forbidden-phrase test."""
    out = [header_for(entry_title, card), FOOTER]
    if card is not None:
        out += [severity_line(card), card.credit.changes]
    return out


def forbidden_in(text: str) -> list[str]:
    lowered = text.lower()
    return [phrase for phrase in FORBIDDEN_PHRASES if phrase in lowered]


# ---- links --------------------------------------------------------------------------------------


def allowed_prefixes(files: Sequence[FindingsFile] | None = None) -> tuple[str, ...]:
    """The URL prefixes a response may carry: the pinned report folders and the documentation
    hosts of the taxonomy."""
    chosen = files if files is not None else default_files()
    reports = {f"https://github.com/{f.source.repository}/blob/{f.source.commit}/" for f in chosen}
    hosts = {f"https://{host}/" for host in REFERENCE_HOSTS}
    return tuple(sorted(reports | hosts))


def allowed_url(url: str, files: Sequence[FindingsFile] | None = None) -> bool:
    return any(url.startswith(prefix) for prefix in allowed_prefixes(files))


def entry_links(entry: TaxonomyEntry) -> tuple[tuple[str, str], ...]:
    """The "read more" links of a taxonomy entry, as (title, url): from our hand-written entry,
    never from model text, and only to the documentation hosts."""
    return tuple((r.title, r.url) for r in entry.references if allowed_url(r.url))


def links_for_entry(entry_id: str) -> tuple[tuple[str, str], ...]:
    slug = entry_id.removeprefix("taxonomy:")
    entry = load_taxonomy().get(slug)
    return entry_links(entry) if entry else ()
