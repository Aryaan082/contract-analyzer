"""The layered run: the union by place, and that it only ever adds to the plain reader's places."""

import json

import pytest

from evals.source_review.hostile import make_bundle
from review import layered as ly
from review.client import ModelUnavailable, ScriptedReviewer
from review.types import Flag
from tests.test_evals_tob_eval import CODE, PIPELINE_REPLY, PLAIN_REPLY

PATHS = {"F1": "src/Vault.sol", "F2": "src/Other.sol"}


def flag(file_id="F1", function="withdraw", contract="Vault", severity="high", note="n", **kw):
    base = dict(contract=contract, function=function, entry="plain", severity=severity,
                confidence="medium", confirmed=False, file_id=file_id, start_line=6, end_line=9,
                note=note)  # fmt: skip
    return Flag(**{**base, **kw})


def test_plain_places_come_first_in_the_readers_order_then_the_places_only_the_pipeline_raised():
    plain = [flag("src/Vault.sol", "withdraw"), flag("src/Vault.sol", "deposit")]
    pipe = [flag("F2", "mint", "Token"), flag("F1", "deposit", entry="taxonomy:x")]
    places = ly.union_places(plain, pipe, PATHS)
    assert [(p.file, p.function) for p in places] == [
        ("src/Vault.sol", "withdraw"), ("src/Vault.sol", "deposit"), ("src/Other.sol", "mint")
    ]  # fmt: skip
    assert [p.layers for p in places] == [{"plain"}, {"plain", "pipeline"}, {"pipeline"}]


def test_a_pipeline_file_id_and_a_plain_path_name_the_same_place():
    plain, pipe = [flag("src/Vault.sol")], [flag("F1")]
    (place,) = ly.union_places(plain, pipe, PATHS)
    assert place.key == ("src/Vault.sol", "Vault", "withdraw") and len(place.flags) == 2
    assert [f.layer for f in place.flags] == ["plain", "pipeline"]


def test_nothing_is_dropped_or_merged_inside_a_layer():
    plain = [flag("src/Vault.sol", note="a"), flag("src/Vault.sol", note="b", start_line=1)]
    (place,) = ly.union_places(plain, [], PATHS)
    assert len(place.flags) == 2 and place.layers == {"plain"}


def test_a_place_takes_its_most_severe_flag_and_no_function_is_an_empty_name():
    place = ly.union_places(
        [flag("src/Vault.sol", severity="low"), flag("src/Vault.sol", severity="high")], [], PATHS
    )[0]
    assert place.severity == "high"
    (nowhere,) = ly.union_places([flag("src/Vault.sol", function=None, contract=None)], [], PATHS)
    assert nowhere.key == ("src/Vault.sol", "", "")


def test_additive_problems_names_a_lost_or_changed_plain_flag():
    plain = [flag("src/Vault.sol")]
    good = ly.LayeredResult(ly.union_places(plain, [], PATHS), plain_flags=tuple(plain))
    assert ly.additive_problems(good, PATHS) == []
    missing = ly.LayeredResult([], plain_flags=tuple(plain))
    assert "a plain place is missing from the union" in ly.additive_problems(missing, PATHS)
    changed = ly.LayeredResult(
        ly.union_places([flag("src/Vault.sol", severity="low")], [], PATHS),
        plain_flags=tuple(plain),
    )
    assert "a plain flag was changed or lost in the union" in ly.additive_problems(changed, PATHS)
    extra = ly.LayeredResult(ly.union_places(plain + plain, [], PATHS), plain_flags=tuple(plain))
    assert "different number of plain flags" in " ".join(ly.additive_problems(extra, PATHS))


def bundle():
    return make_bundle([("src/Vault.sol", CODE)])


def test_a_layered_run_joins_the_two_readers_and_adds_their_costs():
    plain_client = ScriptedReviewer([PLAIN_REPLY], name="p/plain", cost_usd=0.02)
    pipe_client = ScriptedReviewer([PIPELINE_REPLY], name="p/pipe", cost_usd=0.03)
    out = ly.run_layered(bundle(), plain_client, pipe_client)
    assert out.plain_ok and out.pipeline_ok
    assert len(out.plain_flags) == 1 and len(out.pipeline_flags) == 1
    (place,) = out.places  # both readers flagged Vault.withdraw
    assert place.layers == {"plain", "pipeline"} and place.key[2] == "withdraw"
    assert out.cost_usd == pytest.approx(0.05)
    assert (
        "taxonomy" not in plain_client.prompts[0].user
    )  # layer 1 is the plain request: no checklist
    assert ly.additive_problems(out, {"F1": "src/Vault.sol"}) == []


def test_layer_one_keeps_its_places_when_the_pipeline_cannot_answer():
    plain_client = ScriptedReviewer([PLAIN_REPLY], name="p/plain")
    pipe_client = ScriptedReviewer([ModelUnavailable("down")], name="p/pipe")
    out = ly.run_layered(bundle(), plain_client, pipe_client)
    assert out.plain_ok and not out.pipeline_ok
    assert len(out.places) == 1 and out.places[0].layers == {"plain"}


def test_layer_two_keeps_its_places_when_the_plain_reader_cannot_answer():
    plain_client = ScriptedReviewer([ModelUnavailable("down")], name="p/plain")
    pipe_client = ScriptedReviewer([PIPELINE_REPLY], name="p/pipe")
    out = ly.run_layered(bundle(), plain_client, pipe_client)
    assert not out.plain_ok and out.pipeline_ok
    assert [p.layers for p in out.places] == [{"pipeline"}]


def test_the_plain_request_inside_the_layered_run_is_the_pre_registered_one():
    plain_client = ScriptedReviewer([json.dumps({"findings": []})], name="p/plain")
    ly.run_layered(
        bundle(), plain_client, ScriptedReviewer([json.dumps({"items": []})], name="q/x")
    )
    assert plain_client.prompts[0].user.startswith(ly.plain.PLAIN_INSTRUCTION)
