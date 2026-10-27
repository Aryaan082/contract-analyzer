"""lookup_selector: names for 4-byte function selectors, from Sourcify's signature database.

A selector is the first four bytes of keccak256("transfer(address,uint256)"). Bytecode holds only
the selector, so a name has to be looked up, and the lookup is a CLAIM: four bytes are few enough
that anyone can craft a junk function name with a chosen selector. The database marks the obvious
junk (`filtered`), and we keep three rules from that:

- the canonical name is an unfiltered one, preferring a name seen in a verified contract;
- two or more unfiltered names for one selector make it `ambiguous_selector`;
- a risky-sounding name among the alternatives (a `mint` hiding behind `transfer`'s selector) is
  flagged `risky_alternative`: high if unfiltered, low if the database called it spam.

One batched request per contract (`function=a,b,c&filter=false`). Answers are cached in Postgres
for 7 days (selector_status plus selector_name, chain/db.py); a stale entry is served, marked stale,
if the database is down; a broken cache is a miss. Names are untrusted: only strings shaped like
a Solidity signature are kept, capped in length, and most spam names are counted, not listed.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel

from chain.errors import ChainError, scrub
from chain.models import Provenance, ToolResult
from chain.risky_names import LABELS_VERSION, kinds_for_name

log = logging.getLogger(__name__)

SOURCIFY_URL = "https://api.4byte.sourcify.dev/signature-database/v1/lookup"
SOURCE = "sourcify-4byte"
TTL = timedelta(days=7)
BATCH_SIZE = 100  # the service answered 300 at once; stay well under what was tried
MAX_SELECTORS = 500
MAX_CANDIDATES = 50  # per selector, kept and stored
MAX_LISTED_ALTERNATIVES = 5
REQUEST_TIMEOUT_SECONDS = 10
ATTEMPTS = 2
MAX_RESPONSE_BYTES = 4_000_000
USER_AGENT = "contract-analyzer/0.1 (read-only analysis)"

LIMITATIONS = (
    "Names come from a public database that anyone can add to. A name is a claim about the "
    "selector, not proof of what the code does; collisions are cheap to craft.",
    "The canonical name is the first unfiltered one the database lists, preferring a name seen "
    "in a verified contract. The database gives no usage counts, so 'most common' is its order.",
    "A selector with no entry is unknown_selector. That is not suspicious by itself (new or "
    "private functions have none).",
)

_SELECTOR = re.compile(r"0x[0-9a-f]{8}")
# `name(types)`: Solidity identifiers and type characters only. Anything else in a name from the
# database (spaces, quotes, markup, sentences) is dropped, not cleaned.
_SIGNATURE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{0,99}\([A-Za-z0-9_$,()\[\]]{0,300}\)")


class Candidate(BaseModel, frozen=True):
    name: str  # a signature, e.g. "transfer(address,uint256)"
    filtered: bool  # the database's spam filter hid it from its default answer
    has_verified_contract: bool  # seen in at least one verified contract


class Label(BaseModel, frozen=True):
    code: Literal["ambiguous_selector", "risky_alternative", "weak_evidence"]
    severity: Literal["high", "low"] | None
    names: tuple[str, ...]


class SelectorEntry(BaseModel, frozen=True):
    selector: str  # 0x + 8 lowercase hex
    status: Literal["named", "unknown_selector", "unavailable"]
    name: str | None  # the canonical name
    has_verified_contract: bool | None
    alternatives: tuple[Candidate, ...]  # risky ones first, then the others in the database's order
    alternatives_hidden: int  # further names, mostly spam, counted but not listed
    labels: tuple[Label, ...]
    stale: bool  # older than the cache lifetime and the database could not be asked
    fetched_at: str | None  # when the database was asked (ISO 8601, UTC)


class LookupData(BaseModel, frozen=True):
    entries: tuple[SelectorEntry, ...]  # in the order asked, duplicates removed
    named: int
    unknown: int
    unavailable: int
    stale: int
    from_cache: int  # selectors answered without asking the database
    asked_database: int  # selectors the database was asked about
    limitations: tuple[str, ...] = LIMITATIONS


@dataclass(frozen=True, slots=True)
class StoredEntry:
    """What the cache keeps for one selector. `candidates` is empty for a selector the database
    did not know; that is recorded too, so it is not asked again for 7 days."""

    fetched_at: datetime
    labels_version: int
    ambiguous: bool
    risky: Literal["none", "low", "high"]
    candidates: tuple[Candidate, ...]


class SelectorStore(Protocol):
    def read(self, selectors: Sequence[str]) -> Mapping[str, StoredEntry]:
        """Whatever is stored for these selectors, fresh or not. A broken store returns {}."""

    def write(self, entries: Mapping[str, StoredEntry]) -> None:
        """Replace the stored entries. A broken store drops the write."""


class SignatureServiceError(Exception):
    """The database could not be asked, or gave an answer that makes no sense."""


Fetcher = Callable[[Sequence[str]], Mapping[str, tuple[Candidate, ...]]]


# -- reading the database's answer ---------------------------------------------------------------


def _candidates(items: object) -> tuple[Candidate, ...]:
    found: list[Candidate] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name, filtered, verified = (
            item.get("name"),
            item.get("filtered"),
            item.get("hasVerifiedContract"),
        )
        if (
            isinstance(name, str)
            and _SIGNATURE.fullmatch(name)
            and isinstance(filtered, bool)
            and isinstance(verified, bool)
            and all(c.name != name for c in found)
        ):
            found.append(Candidate(name=name, filtered=filtered, has_verified_contract=verified))
        if len(found) == MAX_CANDIDATES:
            break
    return tuple(found)


def parse_response(body: bytes, asked: Sequence[str]) -> dict[str, tuple[Candidate, ...]]:
    """Map every asked selector to its candidates (empty if the database does not know it)."""
    try:
        payload = json.loads(body)
        functions = payload["result"]["function"]
        ok = payload["ok"]
    except (ValueError, KeyError, TypeError):
        raise SignatureServiceError("unexpected response shape") from None
    if ok is not True or not isinstance(functions, dict):
        raise SignatureServiceError("unexpected response shape")
    by_selector = {str(k).lower(): v for k, v in functions.items()}
    return {selector: _candidates(by_selector.get(selector)) for selector in asked}


def fetch_sourcify(
    selectors: Sequence[str],
    *,
    opener: Callable = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, tuple[Candidate, ...]]:
    """One batched request for up to BATCH_SIZE selectors, with `filter=false` so spam-filtered
    names come back marked instead of hidden."""
    query = urllib.parse.urlencode({"function": ",".join(selectors), "filter": "false"}, safe=",")
    request = urllib.request.Request(
        f"{SOURCIFY_URL}?{query}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    last = "no attempt made"
    for attempt in range(ATTEMPTS):
        if attempt:
            sleep(0.5)
        try:
            with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as err:
            last = f"HTTP {err.code}"
            if err.code < 500 and err.code != 429:
                break  # our request is wrong or refused; asking again changes nothing
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = type(err).__name__
            continue
        if len(body) > MAX_RESPONSE_BYTES:
            raise SignatureServiceError("response too large")
        return parse_response(body, selectors)
    raise SignatureServiceError(last)


# -- turning candidates into an entry ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Judgement:
    canonical: Candidate | None
    others: tuple[Candidate, ...]
    ambiguous: bool
    risky: Literal["none", "low", "high"]
    labels: tuple[Label, ...]


def judge(candidates: Sequence[Candidate]) -> Judgement:
    unfiltered = [c for c in candidates if not c.filtered]
    canonical = next((c for c in unfiltered if c.has_verified_contract), None)
    if canonical is None and unfiltered:
        canonical = unfiltered[0]
    others = tuple(c for c in candidates if c is not canonical)

    labels: list[Label] = []
    ambiguous = len(unfiltered) >= 2
    if ambiguous:
        labels.append(
            Label(
                code="ambiguous_selector",
                severity=None,
                names=tuple(c.name for c in unfiltered[: MAX_LISTED_ALTERNATIVES + 1]),
            )
        )
    risky_high = [c for c in others if not c.filtered and kinds_for_name(c.name)]
    risky_low = [c for c in others if c.filtered and kinds_for_name(c.name)]
    risky: Literal["none", "low", "high"] = "high" if risky_high else "low" if risky_low else "none"
    if risky != "none":
        shown = risky_high if risky_high else risky_low
        labels.append(
            Label(
                code="risky_alternative",
                severity=risky,
                names=tuple(c.name for c in shown[:MAX_LISTED_ALTERNATIVES]),
            )
        )
    if canonical is not None and not canonical.has_verified_contract:
        labels.append(Label(code="weak_evidence", severity=None, names=(canonical.name,)))
    return Judgement(canonical, others, ambiguous, risky, tuple(labels))


def _entry(
    selector: str, candidates: Sequence[Candidate], fetched_at: datetime, *, stale: bool
) -> SelectorEntry:
    verdict = judge(candidates)
    risky_names = {c.name for c in verdict.others if kinds_for_name(c.name)}
    # Risky names first so the cap never hides one; spam that is not risky is only counted.
    plain = [c for c in verdict.others if not c.filtered and c.name not in risky_names]
    ranked = [c for c in verdict.others if c.name in risky_names] + plain
    listed = tuple(ranked[:MAX_LISTED_ALTERNATIVES])
    canonical = verdict.canonical
    return SelectorEntry(
        selector=selector,
        status="named" if canonical else "unknown_selector",
        name=canonical.name if canonical else None,
        has_verified_contract=canonical.has_verified_contract if canonical else None,
        alternatives=listed,
        alternatives_hidden=len(verdict.others) - len(listed),
        labels=verdict.labels,
        stale=stale,
        fetched_at=fetched_at.astimezone(UTC).isoformat(timespec="seconds"),
    )


def _unavailable(selector: str) -> SelectorEntry:
    return SelectorEntry(
        selector=selector,
        status="unavailable",
        name=None,
        has_verified_contract=None,
        alternatives=(),
        alternatives_hidden=0,
        labels=(),
        stale=False,
        fetched_at=None,
    )


def _stored(candidates: Sequence[Candidate], fetched_at: datetime) -> StoredEntry:
    verdict = judge(candidates)
    return StoredEntry(
        fetched_at=fetched_at,
        labels_version=LABELS_VERSION,
        ambiguous=verdict.ambiguous,
        risky=verdict.risky,
        candidates=tuple(candidates),
    )


# -- the tool ------------------------------------------------------------------------------------


def normalize_selectors(raw: Iterable[object]) -> list[str]:
    """0x + 8 hex (any case) to lowercase, duplicates removed, order kept; or ChainError."""
    seen: dict[str, None] = {}
    for item in raw:
        text = item.strip().lower() if isinstance(item, str) else ""
        if not _SELECTOR.fullmatch(text):
            shown = item[:20] if isinstance(item, str) else type(item).__name__
            raise ChainError(
                "invalid_selector",
                f"Not a 4-byte selector: {shown!r}. Expected 0x plus 8 hex characters.",
            )
        seen[text] = None
        if len(seen) > MAX_SELECTORS:
            raise ChainError("invalid_selector", f"More than {MAX_SELECTORS} selectors given.")
    return list(seen)


def lookup_selector(
    selectors: Iterable[str],
    provenance: Provenance | None = None,
    *,
    store: SelectorStore | None = None,
    fetch: Fetcher = fetch_sourcify,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ToolResult[LookupData]:
    """The tool. `provenance` is the target contract's (from `get_code`) so the lookup is cited
    with it; its `source` is replaced by the database's name. Without one, provenance says the
    lookup is not tied to a chain."""
    base = provenance or Provenance(
        chain="unknown", chain_id=None, address=None, block=None, source=SOURCE
    )
    stamped = base.model_copy(update={"source": SOURCE})
    try:
        wanted = normalize_selectors(selectors)
    except ChainError as err:
        return ToolResult[LookupData](provenance=stamped, data=err.to_tool_error())

    now = clock()
    stored = dict(store.read(wanted)) if store is not None and wanted else {}
    fresh = {s: e for s, e in stored.items() if now - e.fetched_at < TTL}
    to_ask = [s for s in wanted if s not in fresh]

    answered: dict[str, tuple[Candidate, ...]] = {}
    failed = 0
    for start in range(0, len(to_ask), BATCH_SIZE):
        chunk = to_ask[start : start + BATCH_SIZE]
        try:
            answered.update(fetch(chunk))
        except SignatureServiceError as err:
            failed += len(chunk)
            log.warning("selector database not available: %s", scrub(str(err)))
    if store is not None and answered:
        store.write({s: _stored(c, now) for s, c in answered.items()})

    entries = []
    for selector in wanted:
        if selector in answered:
            entries.append(_entry(selector, answered[selector], now, stale=False))
        elif selector in fresh:
            old = fresh[selector]
            entries.append(_entry(selector, old.candidates, old.fetched_at, stale=False))
        elif selector in stored:  # expired, and the database could not be asked
            old = stored[selector]
            entries.append(_entry(selector, old.candidates, old.fetched_at, stale=True))
        else:
            entries.append(_unavailable(selector))

    if wanted and all(e.status == "unavailable" for e in entries):
        error = ChainError(
            "lookup_unavailable",
            "The selector database could not be reached and nothing is cached for these "
            "selectors. Names are unknown, which is not the same as the selectors being unknown.",
        )
        return ToolResult[LookupData](provenance=stamped, data=error.to_tool_error())

    return ToolResult[LookupData](
        provenance=stamped,
        data=LookupData(
            entries=tuple(entries),
            named=sum(e.status == "named" for e in entries),
            unknown=sum(e.status == "unknown_selector" for e in entries),
            unavailable=sum(e.status == "unavailable" for e in entries),
            stale=sum(e.stale for e in entries),
            from_cache=sum(s in fresh for s in wanted),
            asked_database=len(to_ask) - failed,
        ),
    )
