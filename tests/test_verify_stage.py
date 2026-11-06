"""The annotation stage: nothing is dropped, the order never sees the answer key."""

import inspect
import json

from review.client import ModelUnavailable
from review.layered import LayerFlag, Place
from tests.verify_common import (
    FILES,
    GOOD_TEST,
    PASSED,
    StubRunner,
    poc_reply,
    reader,
    triage_reply,
    verdict_reply,
    writer,
)
from verify import stage
from verify.triage import Triage
from verify.types import Credit


def place(
    function, layers=("plain",), severity="high", note="state is updated late", file="src/Vault.sol"
):
    flags = tuple(LayerFlag(layer, severity, 6, 10, "e", False, note) for layer in layers)
    return Place(file, "Vault", function, flags)


def kinds(annotated):
    return {a.candidate.function: (a.planned.disposition, a.tier) for a in annotated}


def triage_by_function(**by_function):
    """A triage client that answers by the function named in the prompt."""

    def answer(prompt):
        for name, kind in by_function.items():
            if f"Vault.{name} in" in prompt.user:
                return triage_reply(kind, "high")
        return "words"

    return writer([answer])


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


def test_every_place_comes_back_with_a_disposition_and_a_tier_and_none_is_dropped():
    places = [
        place("withdraw", ("plain", "pipeline")),
        place("deposit"),
        place("mint"),
        place("burn"),
    ]
    cands = stage.candidates_of("rep", places)
    tri = triage_by_function(withdraw="logic", deposit="admin_by_design", mint="best_practice")
    out = stage.annotate(cands, FILES, triage_client=tri, per_report=10)
    assert len(out) == len(places)
    assert kinds(out) == {"withdraw": ("queued", 3), "deposit": ("skipped", 7),
                          "mint": ("skipped", 7), "burn": ("unverified", 6)}  # fmt: skip
    assert all(a.verdict is None for a in out)  # no writer, no simulation


def test_the_testable_places_are_simulated_and_a_confirmed_one_leads_the_output():
    places = [
        place("withdraw"),
        place("deposit", ("plain", "pipeline")),
        place("mint", severity="low"),
    ]
    cands = stage.candidates_of("rep", places)
    tri = triage_by_function(withdraw="logic", deposit="logic", mint="best_practice")
    w, r = writer([poc_reply()]), reader([verdict_reply("accept")])
    out = stage.annotate(cands, FILES, triage_client=tri, writer=w, reviewer=r,
                         runner=StubRunner(PASSED), solc="0.8.20")  # fmt: skip
    assert {a.candidate.function for a in out if a.verdict and a.verdict.status == "confirmed"} == {
        "withdraw",
        "deposit",
    }
    ordered = stage.output_order(out)
    assert [a.candidate.function for a in ordered] == ["deposit", "withdraw", "mint"]
    assert (
        ordered[0].credit == Credit("sandbox", ordered[0].verdict.test_sha)
        and ordered[0].test_file == GOOD_TEST
    )


def test_a_place_that_could_not_be_confirmed_stays_in_the_output_marked_unverified_in_tier_three():
    cands = stage.candidates_of("rep", [place("withdraw")])
    tri = triage_by_function(withdraw="logic")
    out = stage.annotate(
        cands, FILES, triage_client=tri, writer=writer(['{"feasible": false}']),
        reviewer=reader([verdict_reply()]), runner=StubRunner(PASSED), solc="0.8.20",
    )  # fmt: skip
    (a,) = out
    assert a.verdict.status == "unverifiable" and a.credit is None and a.tier == 3
    assert a.planned.disposition == "queued"


def test_the_output_order_puts_evidence_first_agreement_next_and_noise_last():
    def ann(function, disposition, *, credit=None, triage=None, layers=("plain",), severity="high"):
        from verify.funnel import Planned
        from verify.types import Candidate

        c = Candidate(
            "rep", "src/Vault.sol", "Vault", function, 1, 2, function, severity, frozenset(layers)
        )
        return stage.Annotated(Planned(c, disposition, "r", credit), triage)

    items = [
        ann("noise", "skipped", triage=Triage("best_practice", "high")),
        ann("untriaged", "unverified"),
        ann("over_cap", "unverified", triage=Triage("logic", "high")),
        ann("plain_logic", "queued", triage=Triage("logic", "high")),
        ann("both_logic", "queued", triage=Triage("logic", "low"), layers=("plain", "pipeline")),
        ann("checked", "check", credit=Credit("check", "tx-origin-auth")),
        ann("confirmed", "queued", credit=Credit("sandbox", "abc")),
    ]
    assert [a.candidate.function for a in stage.output_order(items)] == [
        "confirmed", "checked", "both_logic", "plain_logic", "over_cap", "untriaged", "noise"
    ]  # fmt: skip


def test_ranking_and_annotating_never_see_the_answer_key():
    assert "label" not in inspect.signature(stage.output_order).parameters
    cands = stage.candidates_of("rep", [place("withdraw"), place("deposit")])
    tri = triage_by_function(withdraw="logic", deposit="logic")
    plain_out = stage.annotate(cands, FILES, triage_client=tri)
    assert all(a.credit is None for a in plain_out)  # the default `matched` is empty
    known = stage.annotate(cands, FILES, triage_client=tri,
                           matched={cands[0].place: "AUDIT-1"})  # fmt: skip
    assert known[0].credit == Credit("report", "AUDIT-1") and known[0].tier == 0


def test_the_record_keeps_words_ids_and_hashes_and_no_sentence_or_test_text():
    cands = stage.candidates_of("rep", [place("withdraw", note="SECRET SENTENCE")])
    out = stage.annotate(cands, FILES, triage_client=triage_by_function(withdraw="logic"),
                         writer=writer([poc_reply()]), reviewer=reader([verdict_reply()]),
                         runner=StubRunner(PASSED), solc="0.8.20")  # fmt: skip
    text = json.dumps(stage.record(out[0]))
    assert "SECRET SENTENCE" not in text and "PocTest" not in text
    rec = stage.record(out[0])
    assert rec["verdict"]["status"] == "confirmed" and rec["credit"]["kind"] == "sandbox"
    assert rec["tier"] == 0 and rec["triage"] == {"kind": "logic", "confidence": "high"}


def reasoner_by_function(**by_function):
    """A reasoning client that answers by the function named in the prompt (others: words)."""

    def answer(prompt):
        for name, verdict in by_function.items():
            if f"Vault.{name} in" in prompt.user:
                return json.dumps({
                    "counter": "c SECRET COUNTER", "scenario": "s", "harm": "h",
                    "verdict": verdict, "confidence": "high", "fix": "f",
                })  # fmt: skip
        return "words"

    return writer([answer])


def reasoned_run(**kw):
    places = [place(f) for f in ("withdraw", "deposit", "mint", "burn", "pause")]
    cands = stage.candidates_of("rep", places)
    tri = triage_by_function(
        withdraw="logic", deposit="admin_by_design", mint="logic", burn="logic"
    )
    return stage.annotate(cands, FILES, triage_client=tri, **kw)


def test_the_reasoning_verifier_reads_every_place_that_nothing_else_vouches_for():
    r = reasoner_by_function(withdraw="valid", deposit="valid", mint="invalid", burn="uncertain")
    out = {a.candidate.function: a for a in reasoned_run(reasoner=r)}
    assert {k: v.status for k, v in out.items()} == {
        "withdraw": "valid", "deposit": "valid", "mint": "rejected", "burn": "uncertain",
        "pause": "unchecked",
    }  # fmt: skip
    assert {k: v.tier for k, v in out.items()} == {
        "withdraw": 2,
        "deposit": 2,
        "mint": 8,
        "burn": 4,
        "pause": 6,
    }  # triage called deposit by-design; the verifier's answer decides the tier
    assert len(r.prompts) == 6  # five places; the one it cannot read is asked a second time


def test_the_verifier_can_be_limited_to_the_places_triage_calls_logic():
    r = reasoner_by_function(withdraw="valid", deposit="valid", mint="valid", burn="valid")
    out = {a.candidate.function: a for a in reasoned_run(reasoner=r, reason_only_logic=True)}
    assert len(r.prompts) == 3
    assert out["deposit"].reasoned is None and out["deposit"].tier == 7
    assert out["withdraw"].status == "valid"


def test_a_place_a_report_or_a_check_already_vouches_for_is_not_sent_to_the_verifier():
    cands = stage.candidates_of("rep", [place("withdraw"), place("deposit")])
    r = reasoner_by_function(withdraw="invalid", deposit="invalid")
    tri = triage_by_function(withdraw="logic", deposit="logic")
    out = stage.annotate(cands, FILES, triage_client=tri,
                         matched={cands[0].place: "AUDIT-1"}, reasoner=r)  # fmt: skip
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
    out = reasoned_run(reasoner=reasoner_by_function(withdraw="valid"))
    first = next(a for a in out if a.candidate.function == "withdraw")
    rec = stage.record(first)
    assert rec["reasoned"] == {"verdict": "valid", "confidence": "high", "downgraded": False}
    assert rec["status"] == "valid" and rec["tier"] == 2
    assert "SECRET COUNTER" not in json.dumps(rec)
