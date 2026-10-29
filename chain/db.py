"""Postgres, for two things kept apart on purpose:

- the CODE STORE (`code_blob`, `code_at`): bytecode saved for good. Code at a given address and
  block never changes, and a hash names exactly one byte string, so nothing here expires.
- the SELECTOR CACHE (`selector_status`, `selector_name`): names from a public database, which
  change, so entries are refetched after 7 days (chain/signatures.py).
- the SOURCE CACHE (`source_bundle`, `source_for_code`, `source_miss`): a published source bundle is
  named by its own hash and never changes, so it is kept for good; "not verified" and the like are
  kept only briefly, because a contract can be verified later (review/bundle.py).

Postgres is an optional helper, never a requirement: if `DATABASE_URL` is missing or the server
is down, a read is a miss and a write is dropped, with one scrubbed warning in the log, and the
tools carry on from the chain and the network. After a connection failure we do not try again
for 30 seconds, so a dead server costs one short timeout, not one per call.

Every statement is parameterised; table and column names are constants in this file.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence

import psycopg

from chain.errors import scrub
from chain.signatures import Candidate, StoredEntry

log = logging.getLogger(__name__)

DATABASE_ENV = "DATABASE_URL"
CONNECT_TIMEOUT_SECONDS = 2
RETRY_AFTER_SECONDS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS code_blob (
    code_hash  text PRIMARY KEY CHECK (code_hash ~ '^0x[0-9a-f]{64}$'),
    code       bytea NOT NULL,
    first_seen timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS code_at (
    chain_id    bigint NOT NULL,
    address     text NOT NULL CHECK (address ~ '^0x[0-9a-f]{40}$'),
    block       bigint NOT NULL,
    code_hash   text NOT NULL REFERENCES code_blob (code_hash),
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chain_id, address, block)
);
CREATE TABLE IF NOT EXISTS source_bundle (
    bundle_sha256 text PRIMARY KEY CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
    bundle        bytea NOT NULL,
    first_seen    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS source_for_code (
    chain_id      bigint NOT NULL,
    address       text NOT NULL CHECK (address ~ '^0x[0-9a-f]{40}$'),
    code_hash     text NOT NULL CHECK (code_hash ~ '^0x[0-9a-f]{64}$'),
    bundle_sha256 text NOT NULL REFERENCES source_bundle (bundle_sha256),
    observed_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chain_id, address, code_hash)
);
CREATE TABLE IF NOT EXISTS source_miss (
    chain_id   bigint NOT NULL,
    address    text NOT NULL CHECK (address ~ '^0x[0-9a-f]{40}$'),
    code_hash  text NOT NULL CHECK (code_hash ~ '^0x[0-9a-f]{64}$'),
    status     text NOT NULL,
    reason     text NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chain_id, address, code_hash)
);
CREATE TABLE IF NOT EXISTS selector_status (
    selector       text PRIMARY KEY CHECK (selector ~ '^0x[0-9a-f]{8}$'),
    fetched_at     timestamptz NOT NULL,
    labels_version integer NOT NULL,
    ambiguous      boolean NOT NULL,
    risky          text NOT NULL CHECK (risky IN ('none', 'low', 'high'))
);
CREATE TABLE IF NOT EXISTS selector_name (
    selector              text NOT NULL REFERENCES selector_status (selector) ON DELETE CASCADE,
    name                  text NOT NULL,
    filtered              boolean NOT NULL,
    has_verified_contract boolean NOT NULL,
    position              smallint NOT NULL,
    PRIMARY KEY (selector, name)
);
"""


class Database:
    """A way to run work against Postgres that never raises into the tools."""

    def __init__(
        self,
        url: str,
        *,
        connect: Callable[..., psycopg.Connection] = psycopg.connect,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._connect = connect
        self._clock = clock
        self._schema_ready = False
        self._down_until = 0.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Database | None":
        url = env.get(DATABASE_ENV, "").strip()
        return cls(url) if url else None

    def run[T](self, work: Callable[[psycopg.Connection], T], default: T) -> T:
        """Run `work` in one transaction. On any database trouble, log it and return `default`."""
        if self._clock() < self._down_until:
            return default
        try:
            with self._connect(
                self._url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS
            ) as conn:
                if not self._schema_ready:
                    conn.execute(SCHEMA)
                    self._schema_ready = True
                with conn.transaction():
                    return work(conn)
        except (psycopg.OperationalError, psycopg.InterfaceError, OSError) as exc:
            self._down_until = self._clock() + RETRY_AFTER_SECONDS
            log.warning("postgres not reachable, going on without it: %s", _describe(exc))
        except psycopg.Error as exc:
            log.warning("postgres call failed, going on without it: %s", _describe(exc))
        return default


def _describe(exc: BaseException) -> str:
    # The class name and the sqlstate say what went wrong; the message may carry connection details.
    state = getattr(exc, "sqlstate", None)
    return f"{type(exc).__name__}{f' [{state}]' if state else ''}: {scrub(str(exc))[:120]}"


class PostgresCodeStore:
    """Bytecode saved by hash, plus a record of where each was seen."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def find(self, chain_id: int, address: str, block: int) -> tuple[str, bytes] | None:
        """The (code hash, code) saved for this exact place, if it was ever read."""

        def work(conn: psycopg.Connection) -> tuple[str, bytes] | None:
            row = conn.execute(
                "SELECT b.code_hash, b.code FROM code_at a JOIN code_blob b USING (code_hash) "
                "WHERE a.chain_id = %s AND a.address = %s AND a.block = %s",
                (chain_id, address.lower(), block),
            ).fetchone()
            return (row[0], bytes(row[1])) if row else None

        return self._db.run(work, None)

    def save(self, chain_id: int, address: str, block: int, code_hash: str, code: bytes) -> None:
        def work(conn: psycopg.Connection) -> None:
            conn.execute(
                "INSERT INTO code_blob (code_hash, code) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (code_hash, code),
            )
            # Seen again at the same place: the latest observation wins. For a block that was
            # later reorganised away this is what keeps the record honest.
            conn.execute(
                "INSERT INTO code_at (chain_id, address, block, code_hash) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (chain_id, address, block) "
                "DO UPDATE SET code_hash = EXCLUDED.code_hash, observed_at = now()",
                (chain_id, address.lower(), block, code_hash),
            )

        self._db.run(work, None)


class PostgresBundleStore:
    """Source bundles by hash, which on-chain code each belongs to, and short-lived misses. It keeps
    bytes and strings only: review/bundle.py owns what a bundle is and checks it on the way out."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def find(self, chain_id: int, address: str, code_hash: str) -> bytes | None:
        """The bundle JSON saved for this code at this address, if any."""

        def work(conn: psycopg.Connection) -> bytes | None:
            row = conn.execute(
                "SELECT b.bundle FROM source_for_code f JOIN source_bundle b USING (bundle_sha256) "
                "WHERE f.chain_id = %s AND f.address = %s AND f.code_hash = %s",
                (chain_id, address.lower(), code_hash),
            ).fetchone()
            return bytes(row[0]) if row else None

        return self._db.run(work, None)

    def save(
        self, chain_id: int, address: str, code_hash: str, bundle_sha256: str, bundle: bytes
    ) -> None:
        def work(conn: psycopg.Connection) -> None:
            conn.execute(
                "INSERT INTO source_bundle (bundle_sha256, bundle) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (bundle_sha256, bundle),
            )
            conn.execute(
                "INSERT INTO source_for_code (chain_id, address, code_hash, bundle_sha256) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (chain_id, address, code_hash) "
                "DO UPDATE SET bundle_sha256 = EXCLUDED.bundle_sha256, observed_at = now()",
                (chain_id, address.lower(), code_hash, bundle_sha256),
            )
            # a bundle is now known for this code, so an older miss is out of date
            conn.execute(
                "DELETE FROM source_miss WHERE chain_id = %s AND address = %s AND code_hash = %s",
                (chain_id, address.lower(), code_hash),
            )

        self._db.run(work, None)

    def find_miss(
        self, chain_id: int, address: str, code_hash: str, max_age_seconds: int
    ) -> tuple[str, str] | None:
        """(status, reason) of a miss no older than `max_age_seconds`, else None."""

        def work(conn: psycopg.Connection) -> tuple[str, str] | None:
            row = conn.execute(
                "SELECT status, reason FROM source_miss WHERE chain_id = %s AND address = %s "
                "AND code_hash = %s AND checked_at > now() - make_interval(secs => %s)",
                (chain_id, address.lower(), code_hash, max_age_seconds),
            ).fetchone()
            return (row[0], row[1]) if row else None

        return self._db.run(work, None)

    def save_miss(
        self, chain_id: int, address: str, code_hash: str, status: str, reason: str
    ) -> None:
        def work(conn: psycopg.Connection) -> None:
            conn.execute(
                "INSERT INTO source_miss (chain_id, address, code_hash, status, reason) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (chain_id, address, code_hash) "
                "DO UPDATE SET status = EXCLUDED.status, reason = EXCLUDED.reason, "
                "checked_at = now()",
                (chain_id, address.lower(), code_hash, status, reason),
            )

        self._db.run(work, None)


class PostgresSelectorStore:
    """The selector cache: one status row per selector, one name row per candidate name."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def read(self, selectors: Sequence[str]) -> dict[str, StoredEntry]:
        def work(conn: psycopg.Connection) -> dict[str, StoredEntry]:
            status = conn.execute(
                "SELECT selector, fetched_at, labels_version, ambiguous, risky "
                "FROM selector_status WHERE selector = ANY(%s)",
                (list(selectors),),
            ).fetchall()
            names = conn.execute(
                "SELECT selector, name, filtered, has_verified_contract FROM selector_name "
                "WHERE selector = ANY(%s) ORDER BY selector, position",
                (list(selectors),),
            ).fetchall()
            candidates: dict[str, list[Candidate]] = {}
            for selector, name, filtered, verified in names:
                candidates.setdefault(selector, []).append(
                    Candidate(name=name, filtered=filtered, has_verified_contract=verified)
                )
            return {
                selector: StoredEntry(
                    fetched_at=fetched_at,
                    labels_version=labels_version,
                    ambiguous=ambiguous,
                    risky=risky,
                    candidates=tuple(candidates.get(selector, ())),
                )
                for selector, fetched_at, labels_version, ambiguous, risky in status
            }

        return self._db.run(work, {})

    def write(self, entries: Mapping[str, StoredEntry]) -> None:
        def work(conn: psycopg.Connection) -> None:
            for selector, entry in entries.items():
                _replace(conn, selector, entry)

        if entries:
            self._db.run(work, None)


def _replace(conn: psycopg.Connection, selector: str, entry: StoredEntry) -> None:
    """Swap one selector's status and names for the new answer (the caller's transaction)."""
    conn.execute(
        "INSERT INTO selector_status (selector, fetched_at, labels_version, ambiguous, risky) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (selector) DO UPDATE SET "
        "fetched_at = EXCLUDED.fetched_at, labels_version = EXCLUDED.labels_version, "
        "ambiguous = EXCLUDED.ambiguous, risky = EXCLUDED.risky",
        (selector, entry.fetched_at, entry.labels_version, entry.ambiguous, entry.risky),
    )
    conn.execute("DELETE FROM selector_name WHERE selector = %s", (selector,))
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO selector_name (selector, name, filtered, has_verified_contract, position) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (selector, name) DO UPDATE SET "
            "filtered = EXCLUDED.filtered, has_verified_contract = EXCLUDED.has_verified_contract, "
            "position = EXCLUDED.position",
            [
                (selector, c.name, c.filtered, c.has_verified_contract, position)
                for position, c in enumerate(entry.candidates)
            ],
        )
