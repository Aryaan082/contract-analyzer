"""The taxonomy: every entry loads, uses words the tools can say, and stays within bounds."""

import pytest

from knowledge.taxonomy import (
    MAX_ENTRY_WORDS,
    MAX_LOOK_FOR_WORDS,
    OPCODES,
    REFERENCE_HOSTS,
    TaxonomyError,
    entries_for_signals,
    load_entry,
    load_taxonomy,
    taxonomy_index,
)
from knowledge.vocabulary import (
    categorical_signals,
    field_signals,
    identify_kinds,
    unknown_signals,
)

ENTRIES = load_taxonomy()

VALID = """\
title = "A test entry"
group = "code_bugs"
summary = "A one-line summary that is long enough to be accepted."
applies_to = ["any"]
visible_in = ["source"]
description = '''
A description that is long enough to count as a description of the problem it names.
'''
what_to_check = '''
Read the code and look for the pattern named in the checklist below.
'''
look_for_in_code = '''
Pattern: something. Vulnerable when: something else. Safe form: another thing. False alarms: more.
'''
"""


def write(tmp_path, text, name="test-entry.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_there_are_about_thirty_to_forty_entries_with_stable_ids():
    assert 30 <= len(ENTRIES) <= 40
    assert all(e.id == f"taxonomy:{slug}" for slug, e in ENTRIES.items())


def test_every_signal_word_is_one_a_tool_can_say():
    """If a tool renames a verdict or a signal kind, this fails instead of the entry going stale."""
    for entry in ENTRIES.values():
        assert unknown_signals(entry.signals) == [], entry.slug


def test_the_vocabulary_is_read_from_the_tools_not_copied():
    vocabulary = categorical_signals()
    assert {"mint", "blacklist", "fee_setter", "delegatecall_opcode"} <= vocabulary["risk.kind"]
    assert {"sell_blocked", "buy_blocked", "sellable"} <= vocabulary["sell.verdict"]
    assert {"lookalike", "official"} <= vocabulary["identify.summary"]
    assert "claims_official_but_not_listed" in vocabulary["registry.status"]
    assert "erc20" in identify_kinds() and "erc4626" in identify_kinds()
    assert "sell.sell_tax_bps" in field_signals()


def test_unknown_signals_are_reported():
    assert unknown_signals(("risk.kind=mint", "risk.kind=teleport", "nonsense.field=x")) == [
        "risk.kind=teleport",
        "nonsense.field=x",
    ]
    assert unknown_signals(("sell.round_trip_loss_bps", "sell.made_up")) == ["sell.made_up"]


def test_every_risk_signal_and_blocked_verdict_leads_to_an_entry():
    """No tool result is a dead end: each risky thing a tool can say points to some entry."""
    said = {s for e in ENTRIES.values() for s in e.signals}
    for kind in categorical_signals()["risk.kind"]:
        assert f"risk.kind={kind}" in said, kind
    for needed in (
        "sell.verdict=sell_blocked",
        "sell.verdict=buy_blocked",
        "identify.summary=lookalike",
        "registry.status=claims_official_but_not_listed",
        "identify.kind=erc4626",
        "identify.near_misses",
    ):
        assert needed in said, needed


def test_applies_to_uses_real_kinds():
    allowed = identify_kinds() | {"any"}
    for entry in ENTRIES.values():
        assert set(entry.applies_to) <= allowed, entry.slug
        assert ("any" in entry.applies_to) <= (len(entry.applies_to) == 1), entry.slug


def test_entries_stay_inside_their_size_limits():
    for entry in ENTRIES.values():
        assert len(entry.look_for_in_code.split()) <= MAX_LOOK_FOR_WORDS
        assert entry.word_count <= MAX_ENTRY_WORDS
        assert len(entry.summary) <= 200


def test_the_whole_taxonomy_is_small_enough_to_hold_in_a_prompt():
    words = sum(e.word_count for e in ENTRIES.values())
    assert words < 14_000


def test_opcodes_and_references_are_valid():
    for entry in ENTRIES.values():
        assert set(entry.opcodes) <= OPCODES
        for reference in entry.references:
            assert reference.url.startswith("https://")
            assert reference.url.split("/")[2] in REFERENCE_HOSTS


def test_examples_come_in_pairs_and_contain_no_attacker_code():
    for entry in ENTRIES.values():
        assert bool(entry.example_vulnerable) == bool(entry.example_safe), entry.slug
        for example in (entry.example_vulnerable, entry.example_safe):
            if example:
                assert "contract " not in example, f"{entry.slug}: examples are fragments"
                assert "attacker" not in example.lower() and "exploit" not in example.lower()


def test_visibility_is_stated_for_every_entry():
    for entry in ENTRIES.values():
        assert entry.visible_in
        if "tools" in entry.visible_in:
            assert entry.signals, entry.slug


def test_the_index_has_one_short_line_per_entry():
    index = taxonomy_index()
    assert len(index) == len(ENTRIES)
    assert sum(len(f"{i} {t} {s}".split()) for i, t, s in index) < 1500


# --- the lookup ------------------------------------------------------------------------------


def test_a_tool_signal_finds_its_entries_exactly():
    slugs = {e.slug for e in entries_for_signals(["sell.verdict=sell_blocked"])}
    assert slugs == {"honeypot-sell-blocked"}
    slugs = {
        e.slug for e in entries_for_signals(["risk.kind=blacklist", "sell.verdict=sell_blocked"])
    }
    assert slugs == {"honeypot-sell-blocked", "address-blacklist"}
    assert entries_for_signals(["risk.kind=not-a-thing"]) == []


def test_the_kind_filter_drops_entries_for_other_kinds_but_never_when_the_kind_is_unknown():
    assert [e.slug for e in entries_for_signals(["identify.kind=erc4626"], kinds=["erc4626"])] == [
        "erc4626-share-inflation"
    ]
    lending = {"lending-liquidation-logic", "oracle-manipulation"}
    assert {
        e.slug for e in entries_for_signals(["identify.kind=aave_pool"], kinds=["aave_pool"])
    } >= lending
    # an ERC-20 is not shown a lending-only entry, but a contract of unknown kind is
    assert "lending-liquidation-logic" not in {
        e.slug for e in entries_for_signals(["identify.kind=aave_pool"], kinds=["erc20"])
    }
    assert "lending-liquidation-logic" in {
        e.slug for e in entries_for_signals(["identify.kind=aave_pool"], kinds=[])
    }


# --- loading ---------------------------------------------------------------------------------


def test_a_valid_entry_loads(tmp_path):
    entry = load_entry(write(tmp_path, VALID))
    assert entry.id == "taxonomy:test-entry" and entry.group == "code_bugs"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('group = "code_bugs"', 'group = "stuff"', "group"),
        ('visible_in = ["source"]', 'visible_in = ["tools"]', "no signals say which tool"),
        (
            'visible_in = ["source"]',
            'visible_in = ["source"]\nsignals = ["Not A Signal"]',
            "is not",
        ),
        (
            'visible_in = ["source"]',
            'visible_in = ["source"]\nopcodes = ["TELEPORT"]',
            "EVM opcode",
        ),
        (
            'visible_in = ["source"]',
            'visible_in = ["source"]\nreferences = [{title = "x y z", url = "https://evil.example/a"}]',
            "allowed list",
        ),
        (
            'visible_in = ["source"]',
            'visible_in = ["source"]\nexample_vulnerable = "a"',
            "both example",
        ),
        ('applies_to = ["any"]', "applies_to = []", "applies_to"),
        ('title = "A test entry"', 'title = "bad \\u200b title"', "unexpected character"),
    ],
)
def test_a_bad_entry_is_rejected_with_its_file_name(tmp_path, old, new, message):
    path = write(tmp_path, VALID.replace(old, new))
    with pytest.raises(TaxonomyError, match=f"test-entry.toml.*{message}"):
        load_entry(path)


def test_an_entry_over_the_word_limit_is_rejected(tmp_path):
    long_text = "Pattern " + "word " * (MAX_LOOK_FOR_WORDS + 5)
    text = VALID.replace("Pattern: something.", long_text)
    with pytest.raises(TaxonomyError, match="over 250 words"):
        load_entry(write(tmp_path, text))


def test_broken_toml_is_reported_not_skipped(tmp_path):
    with pytest.raises(TaxonomyError, match="test-entry.toml"):
        load_entry(write(tmp_path, "title = "))
