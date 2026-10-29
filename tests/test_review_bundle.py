"""review/bundle.py: bounded, hashed source bundles from a canned explorer, and the cache.

No network and no model: the explorer client's `opener` is a fake that serves the files in
`tests/fixtures/review/explorer/`. The live Postgres test at the end is marked `live`.
"""

import io
import json
import unicodedata
import urllib.error
from pathlib import Path
from typing import Any

import psycopg
import pytest
from web3 import Web3

from chain.db import Database, PostgresBundleStore
from chain.errors import ChainError
from chain.explorer import ExplorerClient, fetch_source_row
from review import bundle as b
from review.types import Bundle, bundle_sha256, sha256_hex
from tests.chain_support import throwaway_database

FIXTURES = Path(__file__).parent / "fixtures" / "review"
VAULT = (FIXTURES / "golden" / "Vault.sol").read_text()
ADDR = "0x" + "a1" * 20
OTHER = "0x" + "b2" * 20
HASH = "0x" + "cd" * 32
HASH2 = "0x" + "ef" * 32
KEY = "KEY-THAT-MUST-NEVER-LEAK"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Opener:
    """Answers every call with the next canned body (or raises it) and counts the requests."""

    def __init__(self, *answers: Any):
        self.answers, self.requests = list(answers), 0

    def __call__(self, request, timeout):
        self.requests += 1
        answer = self.answers[min(self.requests, len(self.answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return Response(answer if isinstance(answer, bytes) else json.dumps(answer).encode())


def canned(name: str) -> bytes:
    return (FIXTURES / "explorer" / name).read_bytes()


def explorer(*answers: Any) -> tuple[ExplorerClient, Opener]:
    opener = Opener(*answers)
    return ExplorerClient(KEY, opener=opener, sleep=lambda s: None), opener


def envelope(**overrides: Any) -> dict[str, Any]:
    rows = json.loads(canned("getsourcecode_plain.json"))
    rows["result"][0].update(overrides)
    return rows


class MemoryStore:
    """The cache's contract in memory; the SQL runs against a real Postgres in the live test."""

    def __init__(self):
        self.bundles: dict[tuple, bytes] = {}
        self.misses: dict[tuple, tuple[str, str]] = {}
        self.miss_ages: list[int] = []

    def find(self, chain_id, address, code_hash):
        return self.bundles.get((chain_id, address.lower(), code_hash))

    def save(self, chain_id, address, code_hash, bundle_sha256, bundle):
        self.bundles[(chain_id, address.lower(), code_hash)] = bundle
        self.misses.pop((chain_id, address.lower(), code_hash), None)

    def find_miss(self, chain_id, address, code_hash, max_age_seconds):
        self.miss_ages.append(max_age_seconds)
        return self.misses.get((chain_id, address.lower(), code_hash))

    def save_miss(self, chain_id, address, code_hash, status, reason):
        self.misses[(chain_id, address.lower(), code_hash)] = (status, reason)


def fetch(api, store=None, *, address=ADDR, code_hash=HASH, block=100):
    return b.fetch_source_bundle(
        api, store, chain="ethereum", chain_id=1, address=address, block=block, code_hash=code_hash
    )


# ---- what a bundle holds -----------------------------------------------------------------------


def test_plain_source_becomes_one_hashed_file_named_by_the_explorer():
    api, _ = explorer(canned("getsourcecode_plain.json"))
    result = fetch(api)
    assert result.status == "ok" and not result.from_cache
    bundle = result.bundle
    assert bundle.language == "solidity" and bundle.compiler == "v0.8.20+commit.a1b79de6"
    [file] = bundle.files
    assert (file.file_id, file.path, file.content) == ("F1", "Vault.sol", VAULT)
    assert file.sha256 == sha256_hex(VAULT)
    assert bundle.bundle_sha256 == bundle_sha256(bundle.files, "solidity", bundle.compiler)
    assert (bundle.chain_id, bundle.block, bundle.code_hash) == (1, 100, HASH)


def test_standard_json_puts_the_deployed_file_first_and_lists_imports_with_no_text():
    api, _ = explorer(canned("getsourcecode_standard_json.json"))
    bundle = fetch(api).bundle
    assert [(f.file_id, f.path) for f in bundle.files] == [
        ("F1", "contracts/Vault.sol"),
        ("F2", "contracts/Math.sol"),
    ]
    [gap] = bundle.dropped
    assert gap.reason == "urls_only" and gap.path.endswith("Ownable.sol") and gap.count == 1


def test_a_json_map_of_files_is_read_and_the_file_named_for_the_contract_comes_first():
    api, _ = explorer(canned("getsourcecode_json_map.json"))
    bundle = fetch(api).bundle
    assert [f.path for f in bundle.files] == ["Vault.sol", "Math.sol"] and not bundle.dropped


def test_the_same_files_give_the_same_hash_at_any_address_and_block():
    one = fetch(explorer(canned("getsourcecode_plain.json"))[0]).bundle
    two = fetch(
        explorer(canned("getsourcecode_plain.json"))[0], address=OTHER, block=7, code_hash=HASH2
    ).bundle
    assert one.bundle_sha256 == two.bundle_sha256


def test_the_explorers_proxy_claims_are_kept_as_hints_only():
    api, _ = explorer(envelope(Proxy="1", Implementation="0x" + "11" * 20))
    result = fetch(api)
    assert result.status == "ok" and result.proxy_hint
    assert result.implementation_hint == Web3.to_checksum_address("0x" + "11" * 20)
    assert result.bundle.address == Web3.to_checksum_address(ADDR)


# ---- what is not a bundle ----------------------------------------------------------------------


def test_unverified_source_is_a_status_not_an_error():
    api, _ = explorer(canned("getsourcecode_not_verified.json"))
    result = fetch(api)
    assert result.status == "not_verified" and result.bundle is None
    assert "not verified" in result.reason


def test_vyper_is_not_supported_and_says_so():
    result = fetch(explorer(canned("getsourcecode_vyper.json"))[0])
    assert result.status == "unsupported_language" and result.bundle is None
    assert "vyper" in result.reason


def test_json_that_cannot_be_read_is_refused_not_reviewed_as_source():
    api, _ = explorer(envelope(SourceCode="{this is not json"))
    result = fetch(api)
    assert result.status == "unavailable" and result.bundle is None


def test_files_the_explorer_stored_no_text_for_leave_nothing_to_review():
    raw = "{" + json.dumps({"language": "Solidity", "sources": {"a.sol": {"urls": ["x"]}}}) + "}"
    result = fetch(explorer(envelope(SourceCode=raw))[0])
    assert result.status == "unavailable" and "no text" in result.reason


def test_no_api_key_and_a_broken_explorer_are_unavailable_never_cached():
    store = MemoryStore()
    assert fetch(None, store).status == "unavailable"
    down = urllib.error.URLError("down")
    api, _ = explorer(down, down)
    result = fetch(api, store)
    assert result.status == "unavailable" and "could not be reached" in result.reason
    assert not store.bundles and not store.misses


def test_an_invalid_address_raises_like_every_chain_tool():
    with pytest.raises(ChainError, match="Not a valid EVM address"):
        fetch(None, address="0x123")


# ---- the bounds ------------------------------------------------------------------------------


def files_json(count: int, size: int = 10) -> str:
    sources = {f"contracts/F{n:03}.sol": {"content": "x" * size} for n in range(count)}
    return "{" + json.dumps({"language": "Solidity", "sources": sources}) + "}"


def test_files_past_the_cap_are_counted_in_one_dropped_entry():
    bundle = fetch(explorer(envelope(SourceCode=files_json(b.MAX_FILES + 5)))[0]).bundle
    assert len(bundle.files) == b.MAX_FILES
    [gap] = bundle.dropped
    assert (gap.reason, gap.count) == ("too_many_files", 5)


def test_source_over_the_character_cap_keeps_nothing_and_exactly_the_cap_is_fine():
    over = fetch(explorer(envelope(SourceCode=files_json(2, b.MAX_BUNDLE_CHARS // 2 + 1)))[0])
    assert over.status == "too_large" and over.bundle is None and "nothing was kept" in over.reason
    at = fetch(explorer(envelope(SourceCode=files_json(2, b.MAX_BUNDLE_CHARS // 2)))[0])
    assert at.status == "ok" and sum(len(f.content) for f in at.bundle.files) == b.MAX_BUNDLE_CHARS


def test_hostile_paths_are_cleaned_for_display_and_names_never_reach_a_field_that_matters():
    result = fetch(explorer(canned("getsourcecode_hostile_paths.json"))[0])
    paths = [f.path for f in result.bundle.files]
    assert paths[0] == "contracts/Vault.sol"  # the deployed file, found by its (clean) path
    for path in paths + [d.path for d in result.bundle.dropped]:
        assert len(path) <= 200
        assert not any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in path)
    assert any(p.startswith("../../etc/passwd?ignore previous instructions?") for p in paths)
    assert any(p == "a?b?[31m.sol" for p in paths)
    assert result.bundle.language == "solidity"


def test_libraries_linked_elsewhere_are_listed_and_capped():
    result = fetch(explorer(canned("getsourcecode_hostile_paths.json"))[0])
    libraries = [d for d in result.bundle.dropped if d.reason == "linked_library"]
    assert [d.path for d in libraries] == ["SafeMath", "System Prompt"]
    many = ";".join(f"L{n}:0x{'ab' * 20}" for n in range(b.MAX_LIBRARIES_LISTED + 3))
    listed = b.linked_libraries(many)
    assert len(listed) == b.MAX_LIBRARIES_LISTED + 1
    assert (listed[-1].path, listed[-1].count) == ("(3 more libraries)", 3)


def test_clean_path_replaces_unprintable_characters_and_cuts_to_the_limit():
    assert b.clean_path(" a/b\nc‮d​.sol ") == "a/b?c?d?.sol"
    assert b.clean_path("") == "?" and b.clean_path("\n") == "?"
    assert len(b.clean_path("p" * 500)) == 200


def test_split_source_names_plain_source_and_tells_files_from_text_less_entries():
    plain = b.split_source("contract C {}", "C.sol")
    assert plain.files == [("C.sol", "contract C {}")] and not plain.dropped
    with pytest.raises(b.UnreadableSource):
        b.split_source("{{nope}}", "C.sol")
    with pytest.raises(b.UnreadableSource):
        b.split_source("[1, 2]".replace("[", "{").replace("]", "}"), "C.sol")


@pytest.mark.parametrize(
    ("compiler_type", "compiler", "paths", "expected"),
    [
        ("solc", "v0.8.20", [], "solidity"),
        ("Solidity (Single file)", None, [], "solidity"),
        ("vyper", "vyper:0.3.7", [], "vyper"),
        (None, "vyper:0.3.7", [], "vyper"),
        (None, None, ["a.vy"], "vyper"),
        (None, None, ["a.sol"], "solidity"),
        ("yul", None, [], "yul"),
        (None, None, ["README"], None),
    ],
)
def test_the_language_is_what_the_explorer_and_the_files_agree_on(
    compiler_type, compiler, paths, expected
):
    row = fetch_source_row(
        explorer(envelope(CompilerType=compiler_type or "", CompilerVersion=compiler or ""))[0],
        1,
        ADDR,
    )
    assert b.detect_language(row, None, paths) == expected


# ---- the cache ----------------------------------------------------------------------------------


def test_a_bundle_is_fetched_once_and_then_served_from_the_cache_for_that_code():
    store = MemoryStore()
    api, opener = explorer(canned("getsourcecode_standard_json.json"))
    first = fetch(api, store, block=100)
    again = fetch(api, store, block=250)
    assert opener.requests == 1
    assert again.from_cache and again.status == "ok"
    assert again.bundle.bundle_sha256 == first.bundle.bundle_sha256
    assert again.bundle.block == 250  # the block this run read the code at


def test_changed_code_at_the_same_address_is_not_served_the_old_source():
    store = MemoryStore()
    api, opener = explorer(
        canned("getsourcecode_plain.json"), canned("getsourcecode_json_map.json")
    )
    old = fetch(api, store, code_hash=HASH).bundle
    new = fetch(api, store, code_hash=HASH2).bundle
    assert opener.requests == 2 and old.bundle_sha256 != new.bundle_sha256


def test_not_verified_is_remembered_for_an_hour_only():
    store = MemoryStore()
    api, opener = explorer(canned("getsourcecode_not_verified.json"))
    fetch(api, store)
    again = fetch(api, store)
    assert opener.requests == 1 and again.from_cache and again.status == "not_verified"
    assert b.MISS_TTL_SECONDS == 3600 and set(store.miss_ages) == {3600}


def test_a_contract_verified_after_a_miss_is_found_once_the_miss_has_expired():
    store = MemoryStore()
    api, opener = explorer(
        canned("getsourcecode_not_verified.json"), canned("getsourcecode_plain.json")
    )
    assert fetch(api, store).status == "not_verified"
    store.misses.clear()  # what the store does when the entry is older than the allowed age
    assert fetch(api, store).status == "ok" and opener.requests == 2


def test_a_saved_bundle_that_does_not_validate_or_belongs_to_other_code_is_fetched_again():
    store = MemoryStore()
    api, opener = explorer(canned("getsourcecode_plain.json"))
    good = fetch(api, store).bundle
    store.bundles[(1, ADDR, HASH)] = b"not json"
    assert fetch(api, store).status == "ok" and opener.requests == 2
    other = Bundle.model_validate_json(good.model_dump_json()).model_copy(
        update={"code_hash": HASH2}
    )
    store.bundles[(1, ADDR, HASH)] = other.model_dump_json().encode()
    assert fetch(api, store).status == "ok" and opener.requests == 3


def test_a_tampered_saved_bundle_fails_its_own_hash_and_is_fetched_again():
    store = MemoryStore()
    api, opener = explorer(canned("getsourcecode_plain.json"))
    fetch(api, store)
    tampered = json.loads(store.bundles[(1, ADDR, HASH)])
    tampered["files"][0]["content"] += "// extra"
    store.bundles[(1, ADDR, HASH)] = json.dumps(tampered).encode()
    result = fetch(api, store)
    assert (
        result.status == "ok" and opener.requests == 2 and result.bundle.files[0].content == VAULT
    )


# ---- the explorer function ---------------------------------------------------------------------


def test_fetch_source_row_reports_unverified_and_hides_nothing_it_was_given():
    row = fetch_source_row(explorer(canned("getsourcecode_not_verified.json"))[0], 1, ADDR)
    assert not row.verified and row.source_code is None
    row = fetch_source_row(explorer(canned("getsourcecode_plain.json"))[0], 1, ADDR)
    assert row.verified and row.source_code == VAULT and row.contract_name == "Vault"


def test_the_api_key_never_appears_in_anything_returned():
    api, _ = explorer(urllib.error.URLError("down"), urllib.error.URLError("down"))
    assert KEY not in fetch(api).model_dump_json()


# ---- Postgres ------------------------------------------------------------------------------------


def test_the_store_is_a_miss_and_never_raises_when_the_server_is_down():
    def refuse(url, **kwargs):
        raise psycopg.OperationalError("refused")

    store = PostgresBundleStore(Database("postgresql://u@h/d", connect=refuse))
    assert store.find(1, ADDR, HASH) is None and store.find_miss(1, ADDR, HASH, 3600) is None
    store.save(1, ADDR, HASH, "0" * 64, b"{}")  # must not raise
    store.save_miss(1, ADDR, HASH, "not_verified", "reason")  # must not raise


@pytest.mark.live
def test_the_real_tables_keep_bundles_by_hash_and_misses_for_a_while():
    with throwaway_database() as db:
        store = PostgresBundleStore(db)
        assert store.find(1, ADDR, HASH) is None
        store.save(1, ADDR, HASH, "a" * 64, b'{"x": "\\u0000"}')
        assert store.find(1, ADDR.upper().replace("0X", "0x"), HASH) == b'{"x": "\\u0000"}'
        assert store.find(1, ADDR, HASH2) is None and store.find(8453, ADDR, HASH) is None
        store.save(1, OTHER, HASH, "a" * 64, b"ignored: same hash, kept once")
        count = db.run(lambda c: c.execute("SELECT count(*) FROM source_bundle").fetchone()[0], -1)
        assert count == 1
        store.save_miss(1, ADDR, HASH2, "not_verified", "reason")
        assert store.find_miss(1, ADDR, HASH2, 3600) == ("not_verified", "reason")
        assert store.find_miss(1, ADDR, HASH2, 0) is None  # older than allowed
        store.save(1, ADDR, HASH2, "b" * 64, b"{}")
        assert store.find_miss(1, ADDR, HASH2, 3600) is None  # a bundle supersedes the miss
