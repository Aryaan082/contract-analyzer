"""The judge policy (policy.toml) as code: which judges make up a mode, the rule that a judge never
grades an agent from its own company, and the label a result carries (quotable or not).

Nothing here calls a model. `evals.pipeline` uses it before any money is spent, `evals.generate`
again before a judge starts, and `evals.score` when it labels the faithfulness block.
"""

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.judge.rubric import JUDGE_DIR

POLICY_PATH = JUDGE_DIR / "policy.toml"


@dataclass(frozen=True)
class Policy:
    modes: dict[str, tuple[str, ...]]
    extra_allowed: tuple[str, ...]
    groups: tuple[tuple[str, ...], ...]
    min_companies: int
    quotable_label: str
    single_label: str
    rubric_approved: bool
    rubric_notice: str

    @property
    def known_judges(self) -> set[str]:
        return {j for judges in self.modes.values() for j in judges} | set(self.extra_allowed)


def load_policy(path: Path = POLICY_PATH) -> Policy:
    data = tomllib.loads(path.read_text())
    try:
        return Policy(
            modes={name: tuple(judges) for name, judges in data["modes"].items()},
            extra_allowed=tuple(data["extra"]["allowed"]),
            groups=tuple(tuple(g) for g in data["company_groups"]),
            min_companies=int(data["quote"]["min_companies"]),
            quotable_label=data["quote"]["quotable_label"],
            single_label=data["quote"]["single_label"],
            rubric_approved=bool(data["rubric"]["approved"]),
            rubric_notice=data["rubric"]["notice"],
        )
    except KeyError as err:
        raise ValueError(f"policy.toml is missing {err}") from None


def company(slug: str, policy: Policy) -> str:
    """The company a model slug belongs to: its prefix, folded into its group's first name."""
    prefix = slug.split("/", 1)[0].strip().lower()
    for group in policy.groups:
        if prefix in group:
            return group[0]
    return prefix


def resolve_judges(
    mode: str | None, extras: Sequence[str], policy: Policy
) -> tuple[tuple[str, ...], str | None]:
    """The judges a command asked for, and the mode's name. Raises ValueError (a plain message) for
    an unknown mode or a judge the policy does not list. No mode and no extras: no judge."""
    if mode is not None and mode not in policy.modes:
        raise ValueError(f"unknown judge mode {mode!r}; the modes are {', '.join(policy.modes)}")
    for slug in extras:
        if slug not in policy.known_judges:
            raise ValueError(
                f"judge {slug!r} is not in the judge policy (policy.toml): the modes use "
                f"{', '.join(sorted({j for m in policy.modes.values() for j in m}))}; the optional "
                f"extra is {', '.join(policy.extra_allowed)}"
            )
    judges = list(policy.modes[mode]) if mode else []
    judges += [s for s in extras if s not in judges]
    return tuple(judges), mode


def self_grading(judges: Sequence[str], agent_slug: str, policy: Policy) -> list[str]:
    """One plain refusal for each judge that is from the agent's own company."""
    agent = company(agent_slug, policy)
    return [
        f"refused: {judge} is from the same company as the agent ({agent_slug}); a judge never "
        "grades an agent from its own company (policy.toml)"
        for judge in judges
        if company(judge, policy) == agent
    ]


def label(judges: Sequence[str], policy: Policy) -> tuple[str, bool]:
    """(the label a result carries, whether it may be quoted): two companies can be quoted."""
    companies = {company(j, policy) for j in judges}
    if len(companies) >= policy.min_companies:
        return policy.quotable_label, True
    return policy.single_label, False


def policy_block(
    judges: Sequence[str],
    *,
    mode: str | None = None,
    rubric_approved: bool | None = None,
    recorded: dict[str, Any] | None = None,
    policy: Policy | None = None,
) -> dict[str, Any]:
    """What goes into a results file about how it was judged. `recorded` is what a run-set's
    manifest kept from the day it was made (the mode, the label, the rubric flag and its notice): if
    given it is used as it is, so approving the rubric or rewording a label later does not change a
    committed result. Otherwise the policy is asked now."""
    if recorded and "label" in recorded:
        return {
            "mode": recorded.get("mode") or "custom",
            "judges": list(judges),
            "label": recorded["label"],
            "quotable": recorded["quotable"],
            "rubric_approved": recorded["rubric_approved"],
            "rubric_notice": None if recorded["rubric_approved"] else recorded.get("rubric_notice"),
        }
    policy = policy or load_policy()
    text, quotable = label(judges, policy)
    approved = policy.rubric_approved if rubric_approved is None else rubric_approved
    return {
        "mode": mode or "custom",
        "judges": list(judges),
        "label": text,
        "quotable": quotable,
        "rubric_approved": approved,
        "rubric_notice": None if approved else policy.rubric_notice,
    }
