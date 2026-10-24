"""Tagging findings with the taxonomy entries they illustrate, by plain rules.

The rules are in `tagging_rules.toml`. This module loads them, checks that each names a real
taxonomy entry and a valid pattern, and applies them. Tags are our annotation, stored beside the
auditor's text but never mixed into it; re-running the tagger replaces the tags and touches nothing
else, so a bad rule can be fixed without re-reading a PDF.
"""

import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from knowledge.findings import Finding, FindingsFile, load_findings, save_findings
from knowledge.paths import KNOWLEDGE_DIR
from knowledge.taxonomy import load_taxonomy

RULES_PATH = KNOWLEDGE_DIR / "tagging_rules.toml"


class TaggingError(Exception):
    """The rules file is not valid. The message says which rule."""


@dataclass(frozen=True, slots=True)
class Rule:
    taxonomy: str
    title: tuple[re.Pattern[str], ...]
    text: tuple[re.Pattern[str], ...]
    category: tuple[str, ...]

    def matches(self, finding: Finding) -> bool:
        both = f"{finding.title}\n{finding.description}"
        return (
            (finding.category or "") in self.category
            or any(p.search(finding.title) for p in self.title)
            or any(p.search(both) for p in self.text)
        )


def load_rules(path: Path | None = None, known_slugs: set[str] | None = None) -> list[Rule]:
    raw = tomllib.loads((path or RULES_PATH).read_text())
    slugs = known_slugs if known_slugs is not None else set(load_taxonomy())
    rules = []
    for number, item in enumerate(raw.get("rule", []), start=1):
        slug = item.get("taxonomy", "")
        if slug not in slugs:
            raise TaggingError(f"rule {number}: {slug!r} is not a taxonomy entry")
        try:
            title = tuple(re.compile(p, re.I) for p in item.get("title", []))
            text = tuple(re.compile(p, re.I) for p in item.get("text", []))
        except re.error as err:
            raise TaggingError(f"rule {number} ({slug}): bad pattern: {err}") from None
        category = tuple(item.get("category", []))
        if not (title or text or category):
            raise TaggingError(f"rule {number} ({slug}): no patterns")
        rules.append(Rule(slug, title, text, category))
    return rules


def tags_for(finding: Finding, rules: list[Rule]) -> tuple[str, ...]:
    found = {rule.taxonomy for rule in rules if rule.matches(finding)}
    return tuple(sorted(found))


@dataclass(slots=True)
class TagReport:
    total: int
    tagged: int
    per_tag: Counter[str]
    untagged: list[str]


def retag(files: list[FindingsFile], rules: list[Rule]) -> tuple[list[FindingsFile], TagReport]:
    retagged, per_tag, untagged, total = [], Counter(), [], 0
    for file in files:
        findings = []
        for finding in file.findings:
            tags = tags_for(finding, rules)
            per_tag.update(tags)
            total += 1
            if not tags:
                untagged.append(finding.id)
            findings.append(finding.model_copy(update={"taxonomy": tags}))
        retagged.append(file.model_copy(update={"findings": tuple(findings)}))
    return retagged, TagReport(total, total - len(untagged), per_tag, untagged)


def run_tagging(directory: Path | None = None) -> TagReport:
    """Re-tag every findings file in place."""
    files = load_findings(directory)
    retagged, report = retag(files, load_rules())
    for file in retagged:
        save_findings(file, directory)
    return report


def coverage(files: list[FindingsFile], slugs: list[str]) -> dict[str, int]:
    """How many kept findings are tagged with each taxonomy entry. An entry at zero has no audit
    example yet, which is worth knowing when choosing the next reports to add."""
    counts = dict.fromkeys(slugs, 0)
    for file in files:
        for finding in file.findings:
            for tag in finding.taxonomy:
                if tag in counts:
                    counts[tag] += 1
    return counts
