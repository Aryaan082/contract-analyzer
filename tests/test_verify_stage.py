"""The annotation stage: nothing is dropped, the order never sees the answer key."""

import inspect
import json

from review.client import ModelUnavailable
from review.layered import LayerFlag, Place
from tests.verify_common import CODE, FILES, writer
from verify import stage
from verify.funnel import Planned
from verify.reason import Reasoned
from verify.types import Candidate, Credit


def place(
    function, layers=("plain",), severity="high", note="state is updated late", file="src/Vault.sol"
):
    flags = tuple(LayerFlag(layer, severity, 6, 10, "e", False, note) for layer in layers)
    return Place(file, "Vault", function, flags)


def test_one_candidate_per_place_with_the_most_severe_flags_lines_and_both_layers():
    p = Place("src/Vault.sol", "Vault", "withdraw", (
        LayerFlag("plain", "low", 1, 2, "plain", False, "weak"),
        LayerFlag("pipeline", "high", 6, 9, "taxonomy:x", False, "strong"),
    ))  # fmt: skip
    (c,) = stage.candidates_of("rep", [p])
    assert (c.report, c.start_line, c.end_line, c.note, c.severity) == (
        "rep",
        6,
        9,
        "strong",
        "high",
    )
    assert c.layers == {"plain", "pipeline"}


def test_with_no_verifier_every_place_comes_back_unverified_and_none_is_dropped():
    places = [place("withdraw", ("plain", "pipeline")), place("deposit"), place("mint")]
    out = stage.annotate(stage.candidates_of("rep", places), FILES)
    assert len(out) == len(places)
    assert {a.candidate.function: (a.planned.disposition, a.tier, a.status) for a in out} == {
        f: ("unverified", 4, "unchecked") for f in ("withdraw", "deposit", "mint")
    }


def test_a_place_a_deterministic_check_confirms_is_credited_without_the_verifier():
    files = {"src/Vault.sol": CODE.replace("require(ok);", "require(tx.origin == msg.sender);")}
    cands = stage.candidates_of("rep", [place("withdraw", note="authorises with tx.origin")])
    r = reasoner_by_function(withdraw="invalid")
    (a,) = stage.annotate(cands, files, reasoner=r)
    assert a.planned.disposition == "check" and a.credit == Credit("check", "tx-origin-auth")
    assert a.tier == 1 and a.status == "credited" and not r.prompts


def ann(function, *, credit=None, reasoned=None, layers=("plain",), severity="high"):
    c = Candidate("rep", "src/Vault.sol", "Vault", function, 1, 2, function, severity,
                  frozenset(layers))  # fmt: skip
    return stage.Annotated(Planned(c, "unverified", "r", credit), reasoned)


def test_the_output_order_puts_evidence_first_agreement_next_and_rejections_last():
    items = [
        ann("rejected", reasoned=Reasoned("invalid", "high")),
        ann("unchecked"),
        ann("uncertain", reasoned=Reasoned("uncertain", "low")),
        ann("plain_valid", reasoned=Reasoned("valid", "high")),
        ann("both_valid", reasoned=Reasoned("valid", "low"), layers=("plain", "pipeline")),
        ann("checked", credit=Credit("check", "tx-origin-auth")),
        ann("matched", credit=Credit("report", "AUDIT-1")),
    ]
    assert [a.candidate.function for a in stage.output_order(items)] == [
        "matched", "checked", "both_valid", "plain_valid", "uncertain", "unchecked", "rejected"
    ]  # fmt: skip


def test_ranking_and_annotating_never_see_the_answer_key():
    assert "label" not in inspect.signature(stage.output_order).parameters
    cands = stage.candidates_of("rep", [place("withdraw"), place("deposit")])
    plain_out = stage.annotate(cands, FILES)
    assert all(a.credit is None for a in plain_out)  # the default `matched` is empty
    known = stage.annotate(cands, FILES, matched={cands[0].place: "AUDIT-1"})
    assert known[0].credit == Credit("report", "AUDIT-1") and known[0].tier == 0


def reasoner_by_function(**by_function):
    """A reasoning client that answers by the function named in the prompt (others: words)."""

    def answer(prompt):
        for name, verdict in by_function.items():
            if f"Vault.{name} in" in prompt.user:
                return json.dumps({
                    "trace": "t", "counter": "c SECRET COUNTER", "evidence_lines": [7],
                    "scenario": "s", "harm": "h", "type": "Timing", "severity": "medium",
                    "difficulty": "low", "verdict": verdict, "confidence": "high", "fix": "f",
                })  # fmt: skip
        return "words"

    return writer([answer])


def reasoned_run(**kw):
    places = [place(f) for f in ("withdraw", "deposit", "mint", "burn", "pause")]
    return stage.annotate(stage.candidates_of("rep", places), FILES, **kw)


def test_the_reasoning_verifier_reads_every_place_that_nothing_else_vouches_for():
    r = reasoner_by_function(withdraw="valid", deposit="valid", mint="invalid", burn="uncertain")
    out = {a.candidate.function: a for a in reasoned_run(reasoner=r)}
    assert {k: v.status for k, v in out.items()} == {
        "withdraw": "valid", "deposit": "valid", "mint": "rejected", "burn": "uncertain",
        "pause": "unchecked",
    }  # fmt: skip
    assert {k: v.tier for k, v in out.items()} == {
        "withdraw": 2, "deposit": 2, "mint": 5, "burn": 3, "pause": 4
    }  # fmt: skip
    assert len(r.prompts) == 6  # five places; the one it cannot read is asked a second time


def test_a_place_a_report_vouches_for_is_not_sent_to_the_verifier():
    cands = stage.candidates_of("rep", [place("withdraw"), place("deposit")])
    r = reasoner_by_function(withdraw="invalid", deposit="invalid")
    out = stage.annotate(cands, FILES, matched={cands[0].place: "AUDIT-1"}, reasoner=r)
    assert len(r.prompts) == 1 and "Vault.deposit in" in r.prompts[0].user
    assert out[0].status == "credited" and out[0].tier == 0 and out[0].reasoned is None


def test_nothing_is_deleted_every_place_is_in_exactly_one_list_in_output_order():
    r = reasoner_by_function(withdraw="valid", deposit="valid", mint="invalid", burn="uncertain")
    out = reasoned_run(reasoner=r)
    shown = stage.review_list(out)
    assert sorted(a.candidate.function for a in shown.shown) == ["deposit", "withdraw"]
    assert [a.candidate.function for a in shown.uncertain] == ["burn"]
    assert [a.candidate.function for a in shown.rejected] == ["mint"]
    assert [a.candidate.function for a in shown.unchecked] == ["pause"]
    everyone = shown.shown + shown.uncertain + shown.rejected + shown.unchecked
    assert sorted(a.candidate.function for a in everyone) == sorted(
        a.candidate.function for a in out
    )


def test_a_verifier_that_cannot_answer_leaves_every_place_unchecked_and_shown_nowhere():
    out = reasoned_run(reasoner=writer([ModelUnavailable("no credit")]))
    assert all(a.status == "unchecked" and a.reasoned is None for a in out)
    assert stage.review_list(out).shown == []


def test_the_record_has_the_verdict_words_and_none_of_the_models_sentences():
    cands = stage.candidates_of("rep", [place("withdraw", note="SECRET SENTENCE")])
    (first,) = stage.annotate(cands, FILES, reasoner=reasoner_by_function(withdraw="valid"))
    rec = stage.record(first)
    assert rec["reasoned"] == {"verdict": "valid", "confidence": "high", "downgraded": False}
    assert rec["status"] == "valid" and rec["tier"] == 2 and rec["disposition"] == "unverified"
    text = json.dumps(rec)
    assert "SECRET COUNTER" not in text and "SECRET SENTENCE" not in text


def test_a_valid_place_below_severity_low_goes_to_minor_and_nothing_is_deleted():
    def with_severity(function, severity):
        reasoned = Reasoned("valid", "high", "c", "s", "h", "f", category="Timing",
                            severity=severity, difficulty="low")  # fmt: skip
        return ann(function, reasoned=reasoned)

    levels = (("a", "high"), ("b", "low"), ("c", "informational"), ("d", "undetermined"))
    got = stage.review_list([with_severity(f, s) for f, s in levels])
    assert sorted(a.candidate.function for a in got.shown) == ["a", "b"]
    assert sorted(a.candidate.function for a in got.minor) == ["c", "d"]
    assert len(got.shown + got.minor) == 4 and not got.uncertain and not got.rejected
