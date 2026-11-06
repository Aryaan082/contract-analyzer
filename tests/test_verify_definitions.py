"""The definitions the verifier is given: Trail of Bits' classification, in our words."""

from verify import definitions as d


def test_there_are_fourteen_types_five_severities_and_four_difficulties():
    assert len(d.CATEGORIES) == 14 and len(d.SEVERITIES) == 5 and len(d.DIFFICULTIES) == 4
    assert set(d.SEVERITIES) == {"high", "medium", "low", "informational", "undetermined"}
    assert set(d.DIFFICULTIES) == {"low", "medium", "high", "undetermined"}


def test_every_meaning_is_a_non_empty_sentence_fragment_and_none_is_a_copy_of_a_report():
    for table in (d.CATEGORIES, d.SEVERITIES, d.DIFFICULTIES):
        assert all(len(v.split()) >= 4 for v in table.values())
    # paraphrased, not copied: the report's own sentences are not reproduced
    assert "The issue does not pose an immediate risk" not in d.SEVERITIES["informational"]
    assert (
        "Insufficient authorization or assessment of rights" not in d.CATEGORIES["Access Controls"]
    )


def test_privileged_access_is_a_difficulty_not_a_reason_to_reject():
    assert "privileged access" in d.DIFFICULTIES["high"]


def test_the_text_names_everything_and_lists_what_is_not_a_finding():
    text = d.definitions_text()
    for name in (*d.CATEGORIES, *d.SEVERITIES, *d.DIFFICULTIES):
        assert name in text
    assert text.count("NOT A FINDING") == 1
    assert all(item in text for item in d.NOT_A_FINDING)
