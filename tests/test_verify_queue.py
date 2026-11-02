"""The review queue: who may be queued, who may be approved, and what the directory would get."""

import json

import pytest

from tests.verify_common import candidate
from verify import queue as q
from verify.funnel import Planned
from verify.reason import Reasoned
from verify.stage import Annotated, review_list
from verify.types import Credit


def annotated(function, verdict="valid", *, report="rep", credit=None, note="claim <b>one</b>"):
    c = candidate(report=report, function=function, note=note)
    reasoned = None if verdict is None else Reasoned(
        verdict, "high", "c", "who calls what", "someone loses funds", "check the balance",
        category="Configuration", severity="low", difficulty="high", root_cause="_update line 3",
    )  # fmt: skip
    return Annotated(Planned(c, "unverified", "r", credit), reasoned)


def entries(**kw):
    items = [
        annotated("a"), annotated("b", "invalid"), annotated("c", "uncertain"),
        annotated("d", None), annotated("e", credit=Credit("report", "AUDIT-1")),
    ]  # fmt: skip
    return q.entries_from(review_list(items), verifier="deepseek/x", **kw)


def test_only_a_valid_place_nothing_else_vouches_for_is_queued_pending():
    got = entries()
    assert [e.function for e in got] == ["a"]
    assert got[0].status == "pending" and got[0].verifier == "deepseek/x" and got[0].directory_ok


def test_the_claim_is_cleaned_of_markup_before_it_is_kept():
    assert "<" not in entries()[0].claim and "b>" not in entries()[0].claim


def test_a_held_out_reports_entry_is_queued_but_cannot_be_approved():
    got = entries(held_out_reports=["rep"])
    assert [e.directory_ok for e in got] == [False]
    with pytest.raises(ValueError, match="held-out"):
        q.decide(got, got[0].id, "approved")
    rejected = q.decide(got, got[0].id, "rejected")
    assert rejected[0].status == "rejected"
    with pytest.raises(ValueError):
        q.directory_record(got[0])


def test_a_decision_needs_a_known_id_and_a_real_status():
    got = entries()
    with pytest.raises(KeyError):
        q.decide(got, "nope", "approved")
    with pytest.raises(ValueError, match="approved or rejected"):
        q.decide(got, got[0].id, "pending")
    with pytest.raises(ValueError, match="unknown status"):
        q.QueueEntry(**{**got[0].__dict__, "status": "maybe"})


def test_only_an_approved_entry_becomes_a_directory_record_tagged_with_its_source():
    got = entries()
    with pytest.raises(ValueError, match="only an approved"):
        q.directory_record(got[0])
    done = q.decide(got, got[0].id, "approved", note="real, matches my read <x>", on="2026-11-06")
    (rec,) = q.approved_records(done)
    assert rec["source"] == q.SOURCE == "model_verified_user_approved"
    assert rec["id"] == f"rep:MODEL-{got[0].id}" and rec["approved_on"] == "2026-11-06"
    assert rec["description"] == "who calls what someone loses funds"
    assert rec["recommendation"] == "check the balance" and rec["lines"] == [6, 10]
    assert (rec["type"], rec["severity"], rec["difficulty"]) == ("Configuration", "low", "high")
    assert rec["root_cause"] == "_update line 3"
    assert "<" not in done[0].note
    assert q.approved_records(got) == []  # nothing is approved by being queued


def test_a_new_run_adds_entries_and_never_resets_a_decision():
    first = q.decide(entries(), entries()[0].id, "approved", on="2026-11-06")
    again = q.merge(first, entries() + q.entries_from(review_list([annotated("z")]), verifier="v"))
    assert [e.function for e in again] == ["a", "z"]
    assert again[0].status == "approved" and again[1].status == "pending"


def test_the_queue_round_trips_through_a_file_and_a_wrong_format_is_refused(tmp_path):
    path = tmp_path / "sub" / "queue.json"
    assert q.load(path) == []
    got = q.decide(entries(), entries()[0].id, "approved", on="2026-11-06")
    q.save(path, got)
    assert q.load(path) == got
    assert [p.name for p in path.parent.iterdir()] == ["queue.json"]  # no temporary file left
    path.write_text(json.dumps({"format": 99, "entries": []}))
    with pytest.raises(ValueError, match="format"):
        q.load(path)
