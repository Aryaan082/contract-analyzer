"""Which findings are kept: only those that can be exploited.

The agent should warn about problems someone can actually use, not about every observation an
auditor made, so two gates apply to what is written to `findings/`:

  1. severity: only findings the auditors rated critical, high or medium, or could not rate
     (undetermined), are considered;
  2. exploit: each of those has a decision in `exploit_review.toml`, a reader's judgement with a
     one-line reason, easy to argue with and change.

Everything else is not thrown away silently: it is listed in the file's `excluded` section with its
ID, title and reason, so the report's table can still be accounted for row by row. A finding that
passes the severity gate but has no decision fails the build, and so does a decision that matches
no finding, so the review file cannot go stale.
"""

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, StrictBool, ValidationError

from knowledge.findings import Exclusion, Finding
from knowledge.models import PlainText, Severity
from knowledge.paths import KNOWLEDGE_DIR

REVIEW_PATH = KNOWLEDGE_DIR / "exploit_review.toml"
# Severities that are reviewed for exploitability; anything below is excluded on severity alone.
# "undetermined" is in: the auditors could not rate it, so it is judged on what it describes.
REVIEWED_SEVERITIES: frozenset[Severity] = frozenset({"critical", "high", "medium", "undetermined"})


class SelectionError(Exception):
    """The review file cannot be read. Safe to print."""


class Review(BaseModel, frozen=True):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    exploit: StrictBool  # a TOML true or false, never the word "yes"
    why: PlainText = Field(min_length=10, max_length=300)


def load_reviews(path: Path | None = None) -> dict[str, Review]:
    try:
        raw = tomllib.loads((path or REVIEW_PATH).read_text())
        reviews = [Review.model_validate(item) for item in raw.get("review", [])]
    except (tomllib.TOMLDecodeError, ValidationError, OSError) as err:
        raise SelectionError(f"exploit review file: {err}") from None
    by_id = {r.id: r for r in reviews}
    if len(by_id) != len(reviews):
        raise SelectionError("exploit review file: a finding is decided twice")
    return by_id


def select(
    findings: list[Finding], reviews: dict[str, Review], report: str
) -> tuple[list[Finding], list[Exclusion], list[str]]:
    """(kept, excluded, problems) for one report's findings, in the report's order."""
    kept: list[Finding] = []
    excluded: list[Exclusion] = []
    problems: list[str] = []
    for finding in findings:
        if finding.severity not in REVIEWED_SEVERITIES:
            reason, why = "below_severity", None
        else:
            review = reviews.get(finding.id)
            if review is None:
                problems.append(f"{finding.id}: rated {finding.severity}, but has no decision")
                continue
            if review.exploit:
                kept.append(finding)
                continue
            reason, why = "not_an_exploit", review.why
        excluded.append(
            Exclusion(
                finding_id=finding.finding_id,
                severity=finding.severity,
                title=finding.title,
                reason=reason,  # type: ignore[arg-type]
                why=why,
            )
        )
    known = {f.id for f in findings}
    problems += [
        f"{rid}: a decision for a finding this report does not have"
        for rid in reviews
        if rid.startswith(f"{report}:") and rid not in known
    ]
    return kept, excluded, problems
