"""Scoring on the output: a reader that spews flags cannot win on recall at K."""

import pytest

from evals.source_review import precision as pr
from evals.source_review.precision import OutPlace


def p(report="r", *labels, right_place=False, verified=False):
    return OutPlace(report, frozenset(labels), right_place, verified)


def test_an_empty_output_scores_zero_without_dividing_by_zero():
    m = pr.output_metrics([], 10)
    assert (m["places"], m["found"], m["credited_share"], m["findings_per_100_places"]) == (
        0,
        0,
        0.0,
        0.0,
    )
    assert m["at_k"][20] == {"places": 0, "found": 0, "credited_share": 0.0}


def test_the_counts_and_shares_of_a_small_output():
    out = [
        p("r", "A"),
        p("r", "A", "B"),
        p("r", right_place=True),
        p("r", verified=True),
        p("r"),
        p("r"),
    ]
    m = pr.output_metrics(out, 4)
    assert (m["places"], m["found"], m["recall"]) == (6, 2, 0.5)
    assert (m["credited"], m["verified_beyond_key"], m["right_place_only"], m["unverified"]) == (
        3,
        1,
        1,
        2,
    )
    assert m["credited_share"] == pytest.approx(0.5) and m["unverified_share"] == pytest.approx(0.5)
    assert m["findings_per_100_places"] == pytest.approx(100 * 2 / 6)


def test_a_place_with_a_label_and_a_verification_counts_once_as_credited():
    m = pr.output_metrics([p("r", "A", verified=True)], 1)
    assert m["credited"] == 1 and m["verified_beyond_key"] == 0


def test_k_is_per_report_and_cuts_the_tail():
    out = [p("a", "A1"), p("a"), p("a"), p("a", "A2"), p("b", "B1"), p("b", "B2")]
    m = pr.output_metrics(out, 4, ks=(1, 2, 10))
    assert (m["at_k"][1]["places"], m["at_k"][1]["found"]) == (2, 2)  # A1 and B1
    assert (m["at_k"][2]["places"], m["at_k"][2]["found"]) == (4, 3)  # A1, B1, B2
    assert (m["at_k"][10]["places"], m["at_k"][10]["found"]) == (6, 4)  # everything


def test_spewing_flags_does_not_win_when_the_finds_are_at_the_end():
    focused = [p("r", "A"), p("r", "B")] + [p("r", verified=True)]
    spew = [p("r") for _ in range(30)] + [p("r", "A"), p("r", "B"), p("r", "C")]
    f, s = pr.output_metrics(focused, 3), pr.output_metrics(spew, 3)
    assert s["found"] > f["found"]  # recall over everything rewards the spew
    assert f["at_k"][20]["found"] == 2 and s["at_k"][20]["found"] == 0  # recall at K does not
    assert s["unverified_share"] > 0.9 and f["unverified_share"] == 0
    assert not pr.beats(s, f) and pr.beats(f, s)


def test_beating_the_baseline_needs_more_finds_and_a_credited_share_that_is_not_lower():
    base = pr.output_metrics([p("r", "A"), p("r"), p("r")], 3)
    more_but_noisier = pr.output_metrics([p("r", "A"), p("r", "B")] + [p("r")] * 6, 3)
    more_and_cleaner = pr.output_metrics([p("r", "A"), p("r", "B"), p("r")], 3)
    assert more_but_noisier["at_k"][20]["found"] > base["at_k"][20]["found"]
    assert not pr.beats(more_but_noisier, base)  # more finds, lower credited share
    assert pr.beats(more_and_cleaner, base)
    assert not pr.beats(base, base)  # a tie is not a win
