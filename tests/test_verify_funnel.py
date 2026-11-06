"""The mechanical checks and the funnel that decides which flags are simulated."""

import json

import pytest

from tests.verify_common import candidate
from verify import funnel as fn
from verify.mechanical import code_without_comments, mechanical_rule
from verify.triage import Triage
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


def t(kind="logic", confidence="high") -> Triage:
    return Triage(kind, confidence)


def make(i: int, **kw):
    kw.setdefault("note", f"note {i}")
    return candidate(function=f"f{i}", **kw)


def dispositions(planned):
    return {p.candidate.function: p.disposition for p in planned}


def test_each_flag_goes_to_the_first_place_that_takes_it():
    cs = [make(1), make(2, note="uses tx.origin"), make(3), make(4), make(5), make(6)]
    codes = {cs[1].id: ORIGIN}
    tri = {cs[2].id: t("admin_by_design"), cs[3].id: t("best_practice"),
           cs[4].id: t("logic"), cs[5].id: t("unclear")}  # fmt: skip
    matched = {cs[0].place: "TOB-1"}
    got = fn.plan(cs, matched=matched, codes=codes, triage=tri)
    assert dispositions(got) == {"f1": "report", "f2": "check", "f3": "skipped", "f4": "skipped",
                                 "f5": "queued", "f6": "skipped"}  # fmt: skip
    assert got[0].credit == Credit("report", "TOB-1") and got[1].credit == Credit(
        "check", "tx-origin-auth"
    )
    assert got[2].reason == "triage: admin_by_design" and got[4].credit is None


def test_a_flag_that_was_not_triaged_is_unverified_not_dropped():
    got = fn.plan([make(1)], matched={}, codes={}, triage={})
    assert [(p.disposition, p.reason) for p in got] == [("unverified", "not triaged")]


def test_a_matched_flag_is_never_simulated_even_if_triage_says_logic():
    c = make(1)
    got = fn.plan([c], matched={c.place: "TOB-1"}, codes={}, triage={c.id: t()})
    assert got[0].disposition == "report"


def test_the_queue_is_ranked_by_agreement_then_confidence_then_severity_then_id():
    both = frozenset({"plain", "pipeline"})
    cs = [
        make(1, layers=frozenset({"plain"}), severity="high"),
        make(2, layers=both, severity="low"),
        make(3, layers=frozenset({"plain"}), severity="high"),
        make(4, layers=frozenset({"plain"}), severity="medium"),
    ]
    tri = {cs[0].id: t(confidence="low"), cs[1].id: t(confidence="low"),
           cs[2].id: t(confidence="high"), cs[3].id: t(confidence="high")}  # fmt: skip
    got = fn.plan(cs, matched={}, codes={}, triage=tri, per_report=10)
    ranks = {p.candidate.function: p.rank for p in got}
    assert ranks == {
        "f2": 1,
        "f3": 2,
        "f4": 3,
        "f1": 4,
    }  # both layers, then high confidence, then low


def test_the_cap_is_per_report_and_the_rest_stay_unverified():
    cs = [candidate(report=r, function=f"f{i}", note=f"n{i}") for r in ("a", "b") for i in range(4)]
    tri = {c.id: t() for c in cs}
    got = fn.plan(cs, matched={}, codes={}, triage=tri, per_report=3)
    for report in ("a", "b"):
        mine = [p for p in got if p.candidate.report == report]
        assert sorted(p.rank for p in mine if p.disposition == "queued") == [1, 2, 3]
        assert [p.reason for p in mine if p.disposition == "unverified"] == [
            "testable but over the cap"
        ]
    assert fn.summary(got) == {"queued": 6, "unverified": 2}


def test_a_plan_is_the_same_every_time_whatever_the_input_order():
    cs = [make(i) for i in range(6)]
    tri = {c.id: t() for c in cs}
    a = fn.plan(cs, matched={}, codes={}, triage=tri, per_report=3)
    b = fn.plan(list(reversed(cs)), matched={}, codes={}, triage=tri, per_report=3)
    assert {p.candidate.function: p.rank for p in a} == {p.candidate.function: p.rank for p in b}


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
    (p,) = fn.plan([c], matched={c.place: "TOB-9"}, codes={}, triage={})
    rec = fn.record(p)
    assert "note" not in rec and c.note not in json.dumps(rec)
    assert rec["credit"] == {"kind": "report", "ref": "TOB-9"} and rec["layers"] == [
        "pipeline",
        "plain",
    ]
    (q,) = fn.plan([make(2)], matched={}, codes={}, triage={})
    assert fn.record(q)["credit"] is None
