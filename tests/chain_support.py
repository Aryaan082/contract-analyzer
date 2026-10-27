"""Stand-ins for the Postgres stores and the outside world, for the offline chain tests."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from chain.config import load_env
from chain.db import Database
from chain.signatures import Candidate, StoredEntry


class MemoryCodeStore:
    """Same contract as PostgresCodeStore, in a dict. Remembers what it was asked."""

    def __init__(self):
        self.rows: dict[tuple[int, str, int], tuple[str, bytes]] = {}
        self.finds: list[tuple[int, str, int]] = []
        self.saves: list[tuple[int, str, int, str, bytes]] = []

    def find(self, chain_id, address, block):
        self.finds.append((chain_id, address, block))
        return self.rows.get((chain_id, address.lower(), block))

    def save(self, chain_id, address, block, code_hash, code):
        self.saves.append((chain_id, address, block, code_hash, code))
        self.rows[chain_id, address.lower(), block] = (code_hash, code)


class MemorySelectorStore:
    def __init__(self):
        self.rows: dict[str, StoredEntry] = {}
        self.reads: list[list[str]] = []
        self.writes: list[dict[str, StoredEntry]] = []

    def read(self, selectors):
        self.reads.append(list(selectors))
        return {s: self.rows[s] for s in selectors if s in self.rows}

    def write(self, entries):
        self.writes.append(dict(entries))
        self.rows.update(entries)


NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def ago(**delta) -> datetime:
    return NOW - timedelta(**delta)


def cand(name, *, filtered=False, verified=True) -> Candidate:
    return Candidate(name=name, filtered=filtered, has_verified_contract=verified)


@contextmanager
def throwaway_database() -> Iterator[Database]:
    """A Database on the real local Postgres, inside a schema of its own that is dropped after.
    Skips the test if DATABASE_URL is not set. Nothing outside that schema is touched."""
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("needs DATABASE_URL")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True, connect_timeout=2) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    try:
        yield Database(make_conninfo(url, options=f"-c search_path={schema}"))
    finally:
        with psycopg.connect(url, autocommit=True, connect_timeout=2) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
