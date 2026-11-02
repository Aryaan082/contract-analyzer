"""The mechanical checks and the funnel that decides which flags the verifier reads."""

import json

import pytest

from tests.verify_common import candidate
from verify import funnel as fn
from verify.mechanical import code_without_comments, mechanical_rule
from verify.types import Credit

ORIGIN = "require(tx.origin == owner);"
SEND = "payable(msg.sender).transfer(amount);"


@pytest.mark.parametrize(
    ("note", "code", "rule"),
    [
        ("authorises with tx.origin", f"    5| {ORIGIN}", "tx-origin-auth"),
        ("uses transfer() which forwards 2300 gas", f"    5| {SEND}", "transfer-gas-stipend"),
        (
            "uses send() and can run out of gas",
            "    5| ok = recipient.send(1 ether);",
            "transfer-gas-stipend",
        ),
    ],
)
def test_a_rule_fires_when_the_sentence_and_the_code_both_match(note, code, rule):
    assert mechanical_rule(candidate(note=note), code) == rule


@pytest.mark.parametrize(
    ("note", "code"),
    [
        ("authorises with tx.origin", "    5| require(msg.sender == owner);"),  # claim only
        ("state is updated late", f"    5| {ORIGIN}"),  # code only
        (
            "transfer() forwards 2300 gas",
            "    5| token.transfer(to, amount);",
        ),  # an ERC-20 transfer
        ("uses transfer()", "    5| // payable(x).transfer(1);"),  # commented out
        ("uses transfer()", "    5| /* payable(x).transfer(1); */"),
    ],
)
def test_a_rule_does_not_fire_on_half_a_match_an_erc20_transfer_or_a_comment(note, code):
    assert mechanical_rule(candidate(note=note), code) is None


def test_comments_and_line_numbers_are_removed_before_matching():
    assert code_without_comments("    5| a = 1; // tx.origin\n    6| /* x */ b = 2;").split() == [
        "a",
        "=",
        "1;",
        "b",
        "=",
        "2;",
    ]


def make(i: int, **kw):
    kw.setdefault("note", f"note {i}")
    return candidate(function=f"f{i}", **kw)


def dispositions(planned):
    return {p.candidate.function: p.disposition for p in planned}


def test_each_flag_goes_to_the_first_place_that_takes_it():
    cs = [make(1), make(2, note="uses tx.origin"), make(3)]
    codes = {cs[1].id: ORIGIN}
    got = fn.plan(cs, matched={cs[0].place: "TOB-1"}, codes=codes)
    assert dispositions(got) == {"f1": "report", "f2": "check", "f3": "unverified"}
    assert got[0].credit == Credit("report", "TOB-1")
    assert got[1].credit == Credit("check", "tx-origin-auth")
    assert got[2].credit is None and got[2].reason == "nothing vouches for it"


def test_a_matched_flag_is_credited_by_the_report_even_if_a_check_would_fire_too():
    c = make(1, note="uses tx.origin")
    got = fn.plan([c], matched={c.place: "TOB-1"}, codes={c.id: ORIGIN})
    assert got[0].disposition == "report"


def test_every_flag_is_returned_and_none_is_dropped():
    cs = [make(i) for i in range(5)]
    got = fn.plan(cs, matched={}, codes={})
    assert len(got) == 5 and fn.summary(got) == {"unverified": 5}


def test_flags_at_one_place_are_merged_with_both_layers_and_the_higher_severity():
    a = candidate(layers=frozenset({"plain"}), severity="low", note="first")
    b = candidate(layers=frozenset({"pipeline"}), severity="high", note="second")
    (merged,) = fn.merge_by_place([a, b])
    assert (
        merged.layers == {"plain", "pipeline"}
        and merged.severity == "high"
        and merged.note == "first"
    )
    assert len(fn.merge_by_place([a, candidate(function="deposit")])) == 2


def test_the_record_of_a_flag_has_no_sentence_and_is_json():
    c = make(1, layers=frozenset({"plain", "pipeline"}))
    (p,) = fn.plan([c], matched={c.place: "TOB-9"}, codes={})
    rec = fn.record(p)
    assert "note" not in rec and c.note not in json.dumps(rec)
    assert rec["credit"] == {"kind": "report", "ref": "TOB-9"}
    assert rec["layers"] == ["pipeline", "plain"]
    (q,) = fn.plan([make(2)], matched={}, codes={})
    assert fn.record(q)["credit"] is None
