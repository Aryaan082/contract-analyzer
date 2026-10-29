"""Fetching the verified source of one logic address as a bounded, hashed bundle (R1).

Everything the explorer returns is written by the contract's author: paths, names, comments and
the source itself are untrusted text. This module bounds it and hashes it, and says plainly what it
could not keep; it does not clean source text (comments and strings are R2's job) and never
interprets it.

Bounds (approach note, 2.3): the explorer client's 8 MB response cap, at most 200 files, at most
2,000,000 characters of source per address. Over the character cap nothing is kept ("never half a
review", decision 10); files past the file cap and imports the explorer stored no text for are
listed as dropped, never silently lost.

Caching (2.6): a bundle is content addressed, so it is kept for good, and a row ties (chain,
address, on-chain code hash) to its bundle hash, so a redeploy at the same address cannot be served
old source. "Not verified" and similar answers are kept for an hour only, because a contract can
be verified later. An explorer that is down or rate-limited is never cached: that is a failure,
not an answer.
"""

import json
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from chain.errors import ChainError
from chain.explorer import ExplorerClient, SourceRow, fetch_source_row
from chain.models import normalize_address
from review.types import (
    MAX_PATH_CHARS,
    Bundle,
    DroppedFile,
    Language,
    SourceFile,
    bundle_sha256,
    sha256_hex,
)

log = logging.getLogger(__name__)

MAX_FILES = 200
MAX_BUNDLE_CHARS = 2_000_000
MISS_TTL_SECONDS = 3600
MAX_LIBRARIES_LISTED = 20

FetchStatus = Literal["ok", "not_verified", "unsupported_language", "too_large", "unavailable"]
CACHEABLE_MISSES = ("not_verified", "unsupported_language", "too_large")


class BundleFetch(BaseModel, frozen=True):
    """The answer. `reason` is our own plain words; `bundle` is set only when `status` is ok. The
    two hints are the explorer's claims about a proxy (untrusted), kept so a caller can record where
    the explorer and the chain disagree."""

    status: FetchStatus
    reason: str | None = None
    bundle: Bundle | None = None
    from_cache: bool = False
    proxy_hint: bool = False
    implementation_hint: str | None = None


class BundleStore(Protocol):
    """What the cache must do. `chain.db.PostgresBundleStore` does it; a dead database is a miss."""

    def find(self, chain_id: int, address: str, code_hash: str) -> bytes | None: ...

    def save(
        self, chain_id: int, address: str, code_hash: str, bundle_sha256: str, bundle: bytes
    ) -> None: ...

    def find_miss(
        self, chain_id: int, address: str, code_hash: str, max_age_seconds: int
    ) -> tuple[str, str] | None: ...

    def save_miss(
        self, chain_id: int, address: str, code_hash: str, status: str, reason: str
    ) -> None: ...


# ---- paths and splitting -------------------------------------------------------------------


def clean_path(path: str) -> str:
    """A path safe to print: control, format (bidi, zero-width) and line-separator characters become
    `?`, and it is cut to the display limit. It is display text only; nothing resolves it."""
    cleaned = "".join(
        "?" if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in path.strip()
    )
    return cleaned[:MAX_PATH_CHARS] or "?"


@dataclass(frozen=True)
class Split:
    files: list[tuple[str, str]]  # (cleaned path, text) in the explorer's order
    dropped: list[DroppedFile] = field(default_factory=list)
    language: str | None = None  # the `language` of Standard JSON input, lower-cased


class UnreadableSource(Exception):
    """The source field looked like JSON but could not be read as a map of files."""


def split_source(source: str, plain_path: str) -> Split:
    """The files in the explorer's `SourceCode`: plain source (one file, named `plain_path`), a JSON
    map of files, or Standard JSON input wrapped in an extra pair of braces. Unlike
    chain.explorer.split_files this reports every entry that has no text, and refuses JSON it
    cannot read instead of treating it as source (Solidity never starts with a brace)."""
    text = source.strip()
    if not text.startswith("{"):
        return Split(files=[(clean_path(plain_path), source)])
    wrapped = text.startswith("{{") and text.endswith("}}")
    try:
        parsed = json.loads(text[1:-1] if wrapped else text)
    except (ValueError, RecursionError):
        raise UnreadableSource from None
    if not isinstance(parsed, dict):
        raise UnreadableSource
    sources = parsed.get("sources", parsed) if not wrapped else parsed.get("sources")
    if not isinstance(sources, dict):
        raise UnreadableSource
    language = parsed.get("language") if isinstance(parsed.get("language"), str) else None
    files: list[tuple[str, str]] = []
    dropped: list[DroppedFile] = []
    for path, item in sources.items():
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, str):
            files.append((clean_path(str(path)), content))
        else:
            dropped.append(DroppedFile(path=clean_path(str(path)), reason="urls_only"))
    return Split(files=files, dropped=dropped, language=language.lower() if language else None)


def order_main_first(
    files: list[tuple[str, str]], main_file: str | None, name: str | None
) -> list[tuple[str, str]]:
    """The deployed contract's own file first, then one named for it, then the explorer's order."""

    def rank(item: tuple[str, str]) -> int:
        path = item[0]
        if main_file and path == clean_path(main_file):
            return 0
        stem = path.rsplit("/", 1)[-1].removesuffix(".sol").removesuffix(".vy")
        return 1 if name and stem == name else 2

    return sorted(files, key=rank)  # stable


def detect_language(
    row: SourceRow, standard_language: str | None, paths: list[str]
) -> Language | None:
    """The language the explorer and the files agree on, or None when nothing says."""
    hints = [standard_language, row.compiler_type]
    if row.compiler and row.compiler.lower().startswith(("vyper", "yul")):
        hints.append(row.compiler)
    for hint in hints:
        lowered = (hint or "").lower()
        if "vyper" in lowered:
            return "vyper"
        if "yul" in lowered:
            return "yul"
        if "solc" in lowered or "solidity" in lowered:
            return "solidity"
    if any(p.endswith(".vy") for p in paths):
        return "vyper"
    if any(p.endswith(".sol") for p in paths):
        return "solidity"
    return None


def linked_libraries(libraries: str | None) -> list[DroppedFile]:
    """Libraries linked at other addresses (`Name:0xaddress;...`): not part of the bundle."""
    names = [part.split(":", 1)[0] for part in (libraries or "").split(";") if part.strip()]
    listed = [
        DroppedFile(path=clean_path(name), reason="linked_library")
        for name in names[:MAX_LIBRARIES_LISTED]
    ]
    if len(names) > MAX_LIBRARIES_LISTED:
        rest = len(names) - MAX_LIBRARIES_LISTED
        listed.append(
            DroppedFile(path=f"({rest} more libraries)", reason="linked_library", count=rest)
        )
    return listed


# ---- the fetch -----------------------------------------------------------------------------------


def fetch_source_bundle(
    explorer: ExplorerClient | None,
    store: BundleStore | None,
    *,
    chain: str,
    chain_id: int,
    address: str,
    block: int,
    code_hash: str,
) -> BundleFetch:
    """The bundle for the logic contract at `address`, whose code hash at the pinned `block` the
    caller already read. A bad answer from the explorer never raises: the status says what
    happened. An invalid address does (ChainError), as in every chain tool."""
    checked = normalize_address(address)
    if store is not None:
        cached = _from_cache(store, chain_id, checked, block, code_hash)
        if cached is not None:
            return cached
    try:
        row = fetch_source_row(explorer, chain_id, checked)
    except ChainError as err:
        return BundleFetch(status="unavailable", reason=err.message)
    hints = {"proxy_hint": row.proxy_hint, "implementation_hint": row.implementation}
    if not row.verified or row.source_code is None:
        return _miss(store, chain_id, checked, code_hash, "not_verified", None, hints)
    try:
        split = split_source(
            row.source_code, row.contract_file or f"{row.contract_name or 'contract'}.sol"
        )
    except UnreadableSource:
        return BundleFetch(
            status="unavailable",
            reason="The explorer's source could not be read as source or as a map of files.",
            **hints,
        )
    files = order_main_first(split.files, row.contract_file, row.contract_name)
    if not files:
        reason = "The explorer lists the files but stored no text for any of them."
        return BundleFetch(status="unavailable", reason=reason, **hints)
    language = detect_language(row, split.language, [path for path, _ in files])
    if language != "solidity":
        shown = language or "not stated"
        reason = f"Source language not supported ({shown}); only Solidity is reviewed."
        return _miss(store, chain_id, checked, code_hash, "unsupported_language", reason, hints)
    dropped = list(split.dropped)
    if len(files) > MAX_FILES:
        extra = len(files) - MAX_FILES
        dropped.append(
            DroppedFile(path=f"({extra} more files)", reason="too_many_files", count=extra)
        )
        files = files[:MAX_FILES]
    dropped.extend(linked_libraries(row.libraries))
    characters = sum(len(content) for _, content in files)
    if characters > MAX_BUNDLE_CHARS:
        reason = (
            f"The source is {characters} characters in {len(files)} files, over the limit of "
            f"{MAX_BUNDLE_CHARS}; nothing was kept."
        )
        return _miss(store, chain_id, checked, code_hash, "too_large", reason, hints)
    source_files = tuple(
        SourceFile(file_id=f"F{n}", path=path, content=content, sha256=sha256_hex(content))
        for n, (path, content) in enumerate(files, start=1)
    )
    bundle = Bundle(
        chain=chain,
        chain_id=chain_id,
        address=checked,
        block=block,
        code_hash=code_hash,
        language="solidity",
        compiler=row.compiler,
        files=source_files,
        dropped=tuple(dropped),
        bundle_sha256=bundle_sha256(source_files, "solidity", row.compiler),
    )
    if store is not None:
        store.save(
            chain_id, checked, code_hash, bundle.bundle_sha256, bundle.model_dump_json().encode()
        )
    return BundleFetch(status="ok", bundle=bundle, **hints)


def _from_cache(
    store: BundleStore, chain_id: int, address: str, block: int, code_hash: str
) -> BundleFetch | None:
    saved = store.find(chain_id, address, code_hash)
    if saved is not None:
        try:
            bundle = Bundle.model_validate_json(saved)
            if (bundle.chain_id, bundle.address.lower(), bundle.code_hash) != (
                chain_id,
                address.lower(),
                code_hash,
            ):
                raise ValueError("saved for other code")
        except (ValidationError, ValueError):
            log.warning("a saved source bundle was not usable, fetching it again")
        else:
            # the bundle belongs to this code; the block is where this run read that code
            return BundleFetch(
                status="ok", bundle=bundle.model_copy(update={"block": block}), from_cache=True
            )
    miss = store.find_miss(chain_id, address, code_hash, MISS_TTL_SECONDS)
    if miss is not None and miss[0] in CACHEABLE_MISSES:
        return BundleFetch(status=miss[0], reason=miss[1], from_cache=True)  # type: ignore[arg-type]
    return None


def _miss(
    store: BundleStore | None,
    chain_id: int,
    address: str,
    code_hash: str,
    status: FetchStatus,
    reason: str | None,
    hints: dict[str, object],
) -> BundleFetch:
    text = reason or "Contract source code is not verified on the explorer."
    if store is not None:
        store.save_miss(chain_id, address, code_hash, status, text)
    return BundleFetch(status=status, reason=text, **hints)
