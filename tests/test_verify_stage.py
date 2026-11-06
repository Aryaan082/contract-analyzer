"""The annotation stage: nothing is dropped, the order never sees the answer key."""

import inspect
import json

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
    assert kinds(out) == {"withdraw": ("queued", 2), "deposit": ("skipped", 5),
                          "mint": ("skipped", 5), "burn": ("unverified", 4)}  # fmt: skip
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


def test_a_place_that_could_not_be_confirmed_stays_in_the_output_marked_unverified_in_tier_two():
    cands = stage.candidates_of("rep", [place("withdraw")])
    tri = triage_by_function(withdraw="logic")
    out = stage.annotate(
        cands, FILES, triage_client=tri, writer=writer(['{"feasible": false}']),
        reviewer=reader([verdict_reply()]), runner=StubRunner(PASSED), solc="0.8.20",
    )  # fmt: skip
    (a,) = out
    assert a.verdict.status == "unverifiable" and a.credit is None and a.tier == 2
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
