"""chain/db.py without a server: Postgres is optional, so every failure must be a miss."""

import logging

import psycopg
import pytest

from chain.db import RETRY_AFTER_SECONDS, Database, PostgresCodeStore, PostgresSelectorStore
from chain.signatures import StoredEntry
from tests.chain_support import NOW, cand


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def refusing(calls):
    def connect(url, **kwargs):
        calls.append(kwargs)
        raise psycopg.OperationalError(f"connection to {url} refused")

    return connect


def test_no_database_url_means_no_database():
    assert Database.from_env({}) is None
    assert Database.from_env({"DATABASE_URL": "  "}) is None
    assert Database.from_env({"DATABASE_URL": "postgresql://u@h/d"}) is not None


def test_a_server_that_is_down_is_a_miss_and_is_not_retried_for_30_seconds():
    calls, clock = [], Clock()
    db = Database("postgresql://u:SECRET@h/d", connect=refusing(calls), clock=clock)
    assert db.run(lambda conn: "value", "default") == "default"
    assert db.run(lambda conn: "value", "default") == "default"
    assert len(calls) == 1  # the second call did not even try
    clock.now += RETRY_AFTER_SECONDS + 1
    db.run(lambda conn: "value", "default")
    assert len(calls) == 2


def test_connecting_uses_a_short_timeout_and_autocommit():
    calls = []
    Database("postgresql://u@h/d", connect=refusing(calls)).run(lambda conn: 1, 0)
    assert calls[0]["connect_timeout"] == 2 and calls[0]["autocommit"] is True


def test_the_log_says_what_failed_but_never_the_connection_string(caplog):
    db = Database("postgresql://u:SECRET@h/d", connect=refusing([]))
    with caplog.at_level(logging.WARNING, logger="chain.db"):
        db.run(lambda conn: 1, 0)
    assert "OperationalError" in caplog.text
    assert "SECRET" not in caplog.text and "postgresql://" not in caplog.text


def test_a_failing_statement_degrades_that_call_but_does_not_trip_the_breaker():
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            raise psycopg.errors.UndefinedTable("relation missing")

        def transaction(self):
            return self

    def connect(url, **kwargs):
        calls.append(1)
        return Conn()

    db = Database("postgresql://u@h/d", connect=connect)
    assert db.run(lambda conn: 1, "fallback") == "fallback"
    assert db.run(lambda conn: 1, "fallback") == "fallback"
    assert len(calls) == 2  # tried again: this was not a connection problem


def test_the_stores_return_misses_when_the_server_is_down():
    db = Database("postgresql://u@h/d", connect=refusing([]))
    code = PostgresCodeStore(db)
    assert code.find(1, "0x" + "a1" * 20, 5) is None
    code.save(1, "0x" + "a1" * 20, 5, "0x" + "00" * 32, b"")  # must not raise
    names = PostgresSelectorStore(db)
    assert names.read(["0xa9059cbb"]) == {}
    names.write(
        {"0xa9059cbb": StoredEntry(NOW, 1, False, "none", (cand("a()"),))}
    )  # must not raise
    names.write({})


@pytest.mark.parametrize("bad", [OSError("no route"), psycopg.InterfaceError("closed")])
def test_other_connection_failures_are_misses_too(bad):
    def connect(url, **kwargs):
        raise bad

    assert Database("postgresql://u@h/d", connect=connect).run(lambda c: 1, 0) == 0
