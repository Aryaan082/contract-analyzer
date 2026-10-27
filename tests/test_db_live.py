"""chain/db.py against the real local Postgres, in a throwaway schema that is dropped afterwards.

    uv run pytest -m live tests/test_db_live.py

Needs DATABASE_URL (in `.env` or the environment). Touches only its own schema.
"""

from datetime import timedelta

import pytest

from chain.db import PostgresCodeStore, PostgresSelectorStore
from chain.signatures import StoredEntry
from tests.chain_support import NOW, cand, throwaway_database

pytestmark = pytest.mark.live

ADDR = "0x" + "a1" * 20
HASH = "0x" + "ab" * 32


@pytest.fixture
def db():
    with throwaway_database() as database:
        yield database


def test_code_is_saved_by_hash_and_found_by_place(db):
    store = PostgresCodeStore(db)
    assert store.find(1, ADDR, 7) is None
    store.save(1, ADDR, 7, HASH, b"\x60\x01")
    assert store.find(1, ADDR.upper().replace("0X", "0x"), 7) == (HASH, b"\x60\x01")
    assert store.find(1, ADDR, 8) is None and store.find(8453, ADDR, 7) is None


def test_two_places_with_the_same_code_share_one_blob(db):
    store = PostgresCodeStore(db)
    store.save(1, ADDR, 7, HASH, b"\x60\x01")
    store.save(1, "0x" + "b2" * 20, 7, HASH, b"\x60\x01")
    count = db.run(lambda c: c.execute("SELECT count(*) FROM code_blob").fetchone()[0], -1)
    assert count == 1


def test_seeing_a_place_again_keeps_the_latest_observation(db):
    store = PostgresCodeStore(db)
    other = "0x" + "cd" * 32
    store.save(1, ADDR, 7, HASH, b"\x01")
    store.save(1, ADDR, 7, other, b"\x02")
    assert store.find(1, ADDR, 7) == (other, b"\x02")


def test_an_empty_code_is_stored(db):
    store = PostgresCodeStore(db)
    store.save(
        1, ADDR, 7, "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470", b""
    )
    assert store.find(1, ADDR, 7)[1] == b""


def entry(*names, age=timedelta(days=1), risky="none", ambiguous=False):
    return StoredEntry(NOW - age, 1, ambiguous, risky, tuple(names))


def test_a_selector_round_trips_with_its_names_in_the_databases_order(db):
    store = PostgresSelectorStore(db)
    names = (cand("zeta()"), cand("alpha()", filtered=True, verified=False), cand("mid()"))
    store.write({"0xa9059cbb": entry(*names, ambiguous=True, risky="low")})
    got = store.read(["0xa9059cbb", "0x70a08231"])
    assert set(got) == {"0xa9059cbb"}
    assert got["0xa9059cbb"].candidates == names  # not sorted by name
    assert (got["0xa9059cbb"].ambiguous, got["0xa9059cbb"].risky) == (True, "low")
    assert got["0xa9059cbb"].fetched_at == NOW - timedelta(days=1)


def test_a_selector_with_no_names_is_remembered_as_unknown(db):
    store = PostgresSelectorStore(db)
    store.write({"0x12345678": entry()})
    assert store.read(["0x12345678"])["0x12345678"].candidates == ()


def test_a_refresh_replaces_the_names_in_one_go(db):
    store = PostgresSelectorStore(db)
    store.write({"0xa9059cbb": entry(cand("old()"), cand("stale()"))})
    store.write({"0xa9059cbb": entry(cand("new()"), age=timedelta(0))})
    got = store.read(["0xa9059cbb"])["0xa9059cbb"]
    assert [c.name for c in got.candidates] == ["new()"] and got.fetched_at == NOW


def test_rows_that_break_the_format_rules_are_refused_and_become_a_miss(db):
    store = PostgresSelectorStore(db)
    store.write({"not-a-selector": entry(cand("a()"))})  # the CHECK constraint rejects it
    assert store.read(["not-a-selector"]) == {}
