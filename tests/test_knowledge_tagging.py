"""Tagging findings with taxonomy entries by plain rules."""

import re

import pytest

from knowledge.findings import FindingsFile
from knowledge.tagging import Rule, TaggingError, coverage, load_rules, retag, tags_for
from knowledge.taxonomy import load_taxonomy
from tests.test_knowledge_findings import make_file, make_finding

SLUGS = set(load_taxonomy())


def write_rules(tmp_path, text):
    path = tmp_path / "rules.toml"
    path.write_text(text)
    return path


def test_the_committed_rules_name_real_entries_and_compile():
    rules = load_rules()
    assert rules
    assert {r.taxonomy for r in rules} <= SLUGS


def test_rules_match_titles_text_and_categories():
    reentrancy = Rule("reentrancy", (re.compile("re-?entran", re.I),), (), ())
    in_text = Rule("tx-origin-auth", (), (re.compile(r"tx\.origin"),), ())
    by_category = Rule("denial-of-service", (), (), ("Denial of Service",))
    finding = make_finding(title="Reentrancy in withdraw")
    assert reentrancy.matches(finding)
    assert not reentrancy.matches(make_finding(title="Something else"))
    assert in_text.matches(
        make_finding(description="It checks tx.origin before it pays out funds.")
    )
    assert not in_text.matches(make_finding(recommendation="Never use tx.origin for anything."))
    assert by_category.matches(make_finding(category="Denial of Service"))
    assert not by_category.matches(make_finding(category="Timing"))


def test_tags_are_sorted_and_unique():
    rules = load_rules()
    finding = make_finding(title="Lack of slippage protection on liquidity buffer increase")
    tags = tags_for(finding, rules)
    assert tags == tuple(sorted(set(tags))) and "front-running-and-slippage" in tags


def test_a_few_real_titles_get_the_tag_a_reader_would_give():
    rules = load_rules()
    expected = {
        "Reentrancy on pool initialization allows users to re-initialize pools": {
            "reentrancy",
            "unprotected-initializer",
        },
        "Anyone can destroy Morpho's implementation": {
            "missing-access-control",
            "selfdestruct-and-destroy",
            "upgradeable-implementation",
        },
        "Lack of return value checks during token transfers": {"unchecked-call-result"},
        "Risk of loss of precision in division operations": {"arithmetic-and-rounding"},
        "Lack of two-step process for contract ownership changes": {"single-key-admin"},
    }
    for title, tags in expected.items():
        assert set(tags_for(make_finding(title=title), rules)) == tags, title


def test_titles_that_only_mention_a_word_are_not_tagged():
    rules = load_rules()
    for title in (
        "permitBatchAndCall will revert when non-payable functions are called with value",
        "setAllowedSlippage and setMinRatio functions are unreachable",
        "Incorrect liquidation event documentation",
        "Solidity compiler optimizations can be problematic",
    ):
        assert tags_for(make_finding(title=title), rules) == (), title


def test_retagging_replaces_tags_and_changes_nothing_else():
    rules = load_rules()
    tagged = make_finding(title="Reentrancy in withdraw", taxonomy=("tx-origin-auth",))
    other = make_finding(id="test-tob-2024-01:TOB-T-2", finding_id="TOB-T-2", title="Plain title")
    file = make_file([tagged, other])
    [result], report = retag([file], rules)
    assert isinstance(result, FindingsFile)
    assert result.findings[0].taxonomy == ("reentrancy",)
    assert result.findings[0].model_copy(update={"taxonomy": ()}) == tagged.model_copy(
        update={"taxonomy": ()}
    )
    assert result.findings[1].taxonomy == ()
    assert (report.total, report.tagged, report.untagged) == (2, 1, [other.id])
    assert report.per_tag["reentrancy"] == 1


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[[rule]]\ntaxonomy = "no-such-entry"\ntitle = ["x"]', "not a taxonomy entry"),
        ('[[rule]]\ntaxonomy = "reentrancy"\ntitle = ["("]', "bad pattern"),
        ('[[rule]]\ntaxonomy = "reentrancy"', "no patterns"),
    ],
)
def test_bad_rules_are_refused(tmp_path, text, message):
    with pytest.raises(TaggingError, match=message):
        load_rules(write_rules(tmp_path, text), SLUGS)


def test_coverage_counts_kept_findings_per_entry_and_shows_the_gaps():
    first = make_finding(taxonomy=("reentrancy",))
    second = make_finding(
        id="test-tob-2024-01:TOB-T-2",
        finding_id="TOB-T-2",
        taxonomy=("reentrancy", "logic-errors", "not-in-the-list"),
    )
    counts = coverage(
        [make_file([first, second])], ["reentrancy", "logic-errors", "flash-loan-attacks"]
    )
    assert counts == {"reentrancy": 2, "logic-errors": 1, "flash-loan-attacks": 0}


def test_every_entry_appears_in_the_committed_coverage():
    from knowledge.findings import load_findings

    counts = coverage(load_findings(), list(SLUGS))
    assert set(counts) == SLUGS and all(isinstance(n, int) and n >= 0 for n in counts.values())
