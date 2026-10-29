"""Crediting the audit: the card is built from our records only, links are checked, and the wording
never claims more than a model's reading (decision 22, hostile fixtures H17 and H18)."""

import json
from pathlib import Path

import pytest

from knowledge.findings import load_findings
from knowledge.taxonomy import load_taxonomy
from review.credit import (
    CHANGES,
    FOOTER,
    FORBIDDEN_PHRASES,
    HEADERS,
    NO_PRECEDENT,
    SEVERITY_LINE,
    allowed_prefixes,
    allowed_url,
    blob_url,
    card_for,
    credit_for,
    entry_links,
    excerpt_of,
    forbidden_in,
    header_for,
    links_for_entry,
    rendered_strings,
    severity_line,
)
from review.types import MAX_EXCERPT_CHARS, PrecedentCard, PrecedentRef

GOLDEN = Path(__file__).parent / "fixtures" / "review" / "golden"
MORPHO_6 = "morpho-v1-tob-2022-06:TOB-MORPHO-6"
FILES = load_findings()
ALL_IDS = [f.id for file in FILES for f in file.findings]


def test_the_card_for_a_real_finding_is_exactly_the_golden_card():
    golden = json.loads((GOLDEN / "precedent_card.json").read_text())
    credit = credit_for(MORPHO_6)
    assert credit is not None
    assert credit.model_dump(mode="json") == golden["credit"]


def test_an_unknown_id_gives_no_card_never_a_guess():
    assert credit_for("no-such-report:TOB-NONE-1") is None
    assert credit_for("") is None
    ref = PrecedentRef(
        finding_id="no-such-report:TOB-NONE-1", relation="same_class", closeness="close"
    )
    assert card_for(ref) is None


def test_the_link_is_the_github_page_for_the_pinned_commit_with_the_page_printed():
    credit = credit_for(MORPHO_6)
    assert credit is not None
    assert credit.link.startswith("https://github.com/trailofbits/publications/blob/")
    assert "#" not in credit.link and "raw.githubusercontent" not in credit.link
    assert (credit.page, credit.page_end) == (23, 24)
    assert credit.licence == "CC-BY-SA-4.0" and credit.licence_url.endswith("/LICENSE")


def test_blob_url_is_a_fixed_rewrite_and_refuses_anything_else():
    raw = (
        "https://raw.githubusercontent.com/trailofbits/publications/" + "a" * 40 + "/reviews/X.pdf"
    )
    assert (
        blob_url(raw)
        == f"https://github.com/trailofbits/publications/blob/{'a' * 40}/reviews/X.pdf"
    )
    for bad in (
        "https://evil.example/x.pdf",
        "http://raw.githubusercontent.com/o/r/" + "a" * 40 + "/x.pdf",
        "https://raw.githubusercontent.com/o/r/main/x.pdf",  # not pinned to a commit
        "https://raw.githubusercontent.com/o/r/" + "a" * 40 + "/x.html",
    ):
        assert blob_url(bad) is None


def test_the_excerpt_is_the_first_sentence_without_listing_marks_and_within_the_limit():
    assert excerpt_of("First sentence here. Second one follows.\n\nMore.") == "First sentence here."
    assert excerpt_of("- A listed first sentence. Next.") == "A listed first sentence."
    assert excerpt_of("1. Numbered start with e.g. an abbreviation. Then more.").startswith(
        "Numbered"
    )
    long = "word " * 100 + "end. Next."
    cut = excerpt_of(long)
    assert len(cut) <= MAX_EXCERPT_CHARS and cut.endswith("…")
    assert excerpt_of("") == ""


@pytest.mark.parametrize("finding_id", ALL_IDS)
def test_every_finding_in_the_collection_gives_a_complete_allowed_card(finding_id):
    credit = credit_for(finding_id)
    assert credit is not None
    assert 0 < len(credit.excerpt) <= MAX_EXCERPT_CHARS
    assert "\n" not in credit.excerpt and credit.page >= 1
    assert allowed_url(credit.link) and allowed_url(credit.licence_url)
    assert credit.licence == "CC-BY-SA-4.0" and credit.changes == CHANGES
    assert credit.title and credit.finding_label and credit.report_title


def test_a_finding_whose_report_link_cannot_be_built_gives_no_card():
    broken = []
    for file in FILES[:1]:
        findings = tuple(
            f.model_copy(update={"url": "https://raw.githubusercontent.com/o/r/main/x.pdf"})
            for f in file.findings
        )
        broken.append(file.model_copy(update={"findings": findings}))
    finding = broken[0].findings[0]
    assert credit_for(finding.id, broken) is None


def test_only_the_pinned_report_folders_and_the_documentation_hosts_are_allowed():
    assert allowed_url(credit_for(MORPHO_6).link)
    for url in (
        "https://github.com/trailofbits/publications/blob/main/reviews/X.pdf",  # not the pin
        "https://github.com/evil/publications/blob/" + "a" * 40 + "/reviews/X.pdf",
        "https://evil.example/docs.openzeppelin.com/",
        "https://docs.openzeppelin.com.evil.example/x",
        "http://swcregistry.io/docs/SWC-105",
        "javascript:alert(1)",
        "",
    ):
        assert not allowed_url(url), url
    assert allowed_url("https://swcregistry.io/docs/SWC-105")
    assert all(p.startswith("https://") for p in allowed_prefixes())


def test_every_taxonomy_link_is_to_an_allowed_host():
    for slug, entry in load_taxonomy().items():
        assert len(entry_links(entry)) == len(entry.references), slug
    assert links_for_entry("taxonomy:missing-access-control")
    assert links_for_entry("taxonomy:not-an-entry") == ()


# ---- wording ------------------------------------------------------------------------------------


def test_no_template_contains_a_forbidden_phrase():
    templates = [*HEADERS.values(), FOOTER, NO_PRECEDENT, SEVERITY_LINE, CHANGES]
    for text in templates:
        assert forbidden_in(text) == [], text
    assert FOOTER == "A model's reading, not proof. That audit did not cover this contract."


@pytest.mark.parametrize("relation", ["same_class", "same_code_family"])
@pytest.mark.parametrize("closeness", ["close", "related"])
@pytest.mark.parametrize("finding_id", ALL_IDS)
def test_no_rendered_card_string_contains_a_forbidden_phrase(finding_id, relation, closeness):
    card = card_for(PrecedentRef(finding_id=finding_id, relation=relation, closeness=closeness))
    assert card is not None
    for text in rendered_strings("Privileged function without a proper caller check", card):
        assert forbidden_in(text) == [], text


def test_the_golden_card_renders_with_attribution_page_and_the_auditors_own_severity():
    card = PrecedentCard.model_validate_json((GOLDEN / "precedent_card.json").read_text())
    header = header_for("Missing access control", card)
    assert header == (
        "Resembles Missing access control (as found by Trail of Bits in Morpho Protocol V1 "
        "security review, finding TOB-MORPHO-6, p. 23)"
    )
    assert severity_line(card) == "rated high by Trail of Bits in that report"


def test_a_related_precedent_says_related_and_a_fork_says_check_whether_it_was_fixed():
    base = PrecedentCard.model_validate_json((GOLDEN / "precedent_card.json").read_text())
    related = base.model_copy(update={"closeness": "related"})
    assert "a related finding" in header_for("X", related) and "as found by" not in header_for(
        "X", related
    )
    fork = base.model_copy(update={"relation": "same_code_family"})
    assert "shares names" in header_for(
        "X", fork
    ) and "Check whether this contract fixed it" in header_for("X", fork)


def test_no_precedent_says_so_plainly():
    assert header_for("Reentrancy", None) == f"Resembles Reentrancy. {NO_PRECEDENT}"
    assert forbidden_in(header_for("Reentrancy", None)) == []


def test_the_forbidden_list_catches_what_it_is_meant_to():
    for phrase in FORBIDDEN_PHRASES:
        assert forbidden_in(f"This contract {phrase.upper()} indeed.") == [phrase]
    assert forbidden_in("Resembles a pattern.") == []


def test_a_model_note_with_a_link_cannot_change_a_card():
    """Fixture H17/H18: the card is built from the id alone; whatever text the model wrote
    elsewhere has no way into it."""
    card = card_for(PrecedentRef(finding_id=MORPHO_6, relation="same_class", closeness="close"))
    text = json.dumps(card.model_dump(mode="json"))
    assert "evil.example" not in text and "[audit]" not in text
    assert all(allowed_url(v) for v in (card.credit.link, card.credit.licence_url))
