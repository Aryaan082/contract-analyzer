"""Audit items: fetch, the pre-registered checks, labels, and the report, with a fake network and
scripted clients only: no model, no network."""

import hashlib
import io
import json
import urllib.error
from pathlib import Path

import pytest

from evals.source_review import audit_items as ai
from evals.source_review import compare as cmp
from evals.source_review.audit_report import build_audit_report, render_audit
from review.client import ScriptedReviewer

CODE = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Vault {
    mapping(address => uint256) public balance;
    address public feeTo;

    function deposit() external payable {
        balance[msg.sender] += msg.value;
    }

    function setFeeTo(address to) external {
        feeTo = to;
    }

    function withdraw(uint256 amount) external {
        require(balance[msg.sender] >= amount, "low");
        (bool ok,) = msg.sender.call{value: amount}("");
        require(ok, "send");
        balance[msg.sender] -= amount;
    }
}
"""
REPORT = """# Issue H-1: Anyone can set the fee recipient

Source: https://github.com/sherlock-audit/2099-01-demo-judging/issues/1

## Found by
a, b

### Description
[Vault.sol#L12-L14](https://github.com/sherlock-audit/2099-01-demo/blob/main/src/Vault.sol#L12-L14):
```solidity
    function setFeeTo(address to) external {
        feeTo = to; //@audit no access control
    }
```

# Issue M-2: Reentrancy on withdraw

Source: https://github.com/sherlock-audit/2099-01-demo-judging/issues/2

### Description
[Vault.sol#L17-L21](https://github.com/sherlock-audit/2099-01-demo/blob/main/src/Vault.sol#L17-L21):
```solidity
    function deposit() external payable {
        balance[msg.sender] += msg.value;
    }
```

# Issue M-3: No quote at all

[Vault.sol#L8](https://github.com/sherlock-audit/2099-01-demo/blob/main/src/Vault.sol#L8)
"""
TOML = """
[[contest]]
slug = "2099-01-demo"
commit = "abc123"
split = "dev"

[[issue]]
id = "2099-01-demo:H-1"
contest = "2099-01-demo"
severity = "H"
found_by = 2
title = "Anyone can set the fee recipient"
path = "src/Vault.sol"
ranges = [[12, 14]]
bytes = 600
split = "dev"
rank = 1

[[issue]]
id = "2099-01-demo:M-2"
contest = "2099-01-demo"
severity = "M"
found_by = 7
title = "Reentrancy on withdraw"
path = "src/Vault.sol"
ranges = [[17, 21]]
bytes = 600
split = "dev"
rank = 2

[[issue]]
id = "2099-01-demo:M-3"
contest = "2099-01-demo"
severity = "M"
found_by = 1
title = "No quote at all"
path = "src/Vault.sol"
ranges = [[8, 8]]
bytes = 600
split = "dev"
rank = 3

[[issue]]
id = "2099-01-demo:M-4"
contest = "2099-01-demo"
severity = "M"
found_by = 5
title = "Reserve"
path = "src/Vault.sol"
ranges = [[17, 21]]
bytes = 600
split = "dev"
rank = 31
"""


class Fake:
    """An opener serving the file and the report; anything else is a 404. Counts the requests."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files, self.requests = files, []

    def __call__(self, request, timeout=0):
        url = request.full_url
        self.requests.append(url)
        for tail, body in self.files.items():
            if url.endswith(tail):
                return io.BytesIO(body.encode())
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)  # type: ignore[arg-type]


def served() -> Fake:
    return Fake({"src/Vault.sol": CODE, "README.md": REPORT})


@pytest.fixture
def items(tmp_path: Path) -> ai.ItemsFile:
    path = tmp_path / "items.toml"
    path.write_text(TOML)
    return ai.load_items(path)


def test_items_file_is_the_registered_one():
    items = ai.load_items()
    assert len(items.contests) == 16
    for side in ai.SIDES:
        ranks = [i.rank for i in items.side(side)]
        assert ranks == sorted(ranks) and ranks[:30] == list(range(1, 31))
    assert all(i.split == items.contests[i.contest].split for i in items.issues)
    assert all(len(c.commit) == 40 for c in items.contests.values())


def test_urls_are_pinned_to_the_commit_and_the_cache_is_used_once(items, tmp_path):
    contest = items.contests["2099-01-demo"]
    issue = items.issues[0]
    assert ai.file_url(contest, issue).endswith("/2099-01-demo/abc123/src/Vault.sol")
    fake = served()
    for _ in range(2):
        assert ai.cached(tmp_path / "c", "files", "x", ai.file_url(contest, issue), fake) == CODE
    assert len(fake.requests) == 1


def test_a_404_is_a_missing_file_not_an_error(items, tmp_path):
    checked = ai.check_issue(items.issues[0], items.contests["2099-01-demo"], tmp_path, Fake({}))
    assert checked.status == "file missing" and not checked.accepted


def test_a_quote_found_at_the_linked_lines_passes_and_labels_the_function(items, tmp_path):
    c = ai.check_issue(items.issues[0], items.contests["2099-01-demo"], tmp_path, served())
    assert c.status == "ok" and c.functions == (("Vault", "setFeeTo"),)
    assert c.sha256 == hashlib.sha256(CODE.encode()).hexdigest()


def test_a_quote_that_is_not_in_the_file_fails_the_check(items, tmp_path):
    c = ai.check_issue(items.issues[1], items.contests["2099-01-demo"], tmp_path, served())
    assert c.status == "quote not found" and c.functions == ()


def test_no_quote_means_unverified_but_kept(items, tmp_path):
    c = ai.check_issue(items.issues[2], items.contests["2099-01-demo"], tmp_path, served())
    assert c.status == "unverified position" and c.accepted
    assert c.functions == (("Vault", "deposit"),)


def test_comment_only_lines_and_trailing_comments_do_not_count_as_code():
    assert ai.normal("  feeTo = to; //@audit no access control ") == "feeTo=to;"
    assert ai.normal("// only a comment") == ""
    assert ai.blocks("no code here") == []


def test_ranges_outside_any_function_label_nothing():
    assert ai.labelled_functions(CODE, [(5, 5)]) == []  # a state variable
    assert ai.labelled_functions(CODE, [(10, 12)]) == [("Vault", "deposit"), ("Vault", "setFeeTo")]


def test_gather_replaces_a_failed_item_by_the_next_reserve_in_rank_order(
    items, tmp_path, monkeypatch
):
    monkeypatch.setattr(ai, "PER_SIDE", 3)
    lines: list[str] = []
    results = ai.gather(items, tmp_path, served(), lines.append)
    assert [r.issue.rank for r in results] == [1, 2, 3, 31]
    assert [r.issue.rank for r in results if r.accepted] == [1, 3, 31]
    assert any("replaced 2099-01-demo:M-2" in line for line in lines)


@pytest.fixture
def labelled(items, tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "PER_SIDE", 3)
    results = ai.gather(items, tmp_path / "cache", served(), lambda _: None)
    path = tmp_path / "labels.toml"
    ai.write_labels(results, path)
    return items, ai.load_labels(path), tmp_path / "cache"


def test_labels_round_trip_and_files_group_their_issues(labelled):
    items, labels, cache = labelled
    assert labels["2099-01-demo:M-2"][0] == "quote not found"
    (item,) = ai.file_items(items, labels, "dev")
    assert item.id == "2099-01-demo:src/Vault.sol" and item.group == "audit_dev"
    assert [i.number for i in item.issues] == ["H-1", "M-3", "M-4"]
    assert item.keys == {("Vault", "setFeeTo"), ("Vault", "deposit"), ("Vault", "withdraw")}
    (source,) = ai.load_sources([item], items, cache, Fake({})).values()  # served from the cache
    assert source.files == (("src/Vault.sol", CODE),)


def test_a_cached_file_that_changed_since_the_labels_is_refused(labelled):
    items, labels, cache = labelled
    (item,) = ai.file_items(items, labels, "dev")
    (cache / "files" / "2099-01-demo" / "src" / "Vault.sol").write_text(CODE + "\n// edited\n")
    with pytest.raises(ValueError, match="not the one the labels were made from"):
        ai.load_sources([item], items, cache, Fake({}))


def _p(function: str | None) -> ScriptedReviewer:
    found = (
        []
        if function is None
        else [
            {
                "file": "src/Vault.sol",
                "contract": "Vault",
                "function": function,
                "start_line": 1,
                "end_line": 1,
                "severity": "medium",
                "description": "d",
            }
        ]
    )
    return ScriptedReviewer([json.dumps({"findings": found})], name=cmp.OPUS.name, cost_usd=0.01)


def _a(*functions: str) -> ScriptedReviewer:
    from evals.source_review.hostile import Flaw, Scenario, scripted_factory

    flaws = tuple(Flaw("Vault", f, "taxonomy:missing-access-control") for f in functions)
    return scripted_factory(
        Scenario(files=(("src/Vault.sol", CODE),), flaws=flaws), flaws, cmp.OPUS.name
    )


def _run(labelled, split_group: str, p: ScriptedReviewer, a: ScriptedReviewer):
    from dataclasses import replace

    items, labels, cache = labelled
    (item,) = ai.file_items(items, labels, "dev")
    item = replace(item, group=split_group)
    sources = ai.load_sources([item], items, cache, Fake({}))
    records = cmp.run_compare(
        [item],
        sources,
        lambda i, s: p,
        lambda i, s: a,
        spent=lambda: 0.0,
        max_spend=9.0,
        out=lambda _: None,
    )
    meta = {"model": "m", "date": "2026-10-29", "spent": 0.0, "max_spend": 9.0}
    return build_audit_report({item.id: item}, records, meta)


def test_report_counts_who_found_which_judged_issue_and_allows_the_claim_only_when_shown(labelled):
    report = _run(labelled, "audit_held_out", _p("setFeeTo"), _a("setFeeTo", "deposit"))
    side = report["sides"]["held_out"]
    assert (side["P"]["found"], side["A"]["found"], side["issues"]) == (1, 2, 3)
    assert (side["both"], side["a_found_p_missed"], side["p_found_a_missed"]) == (1, 1, 0)
    assert side["relative_improvement"][0] == pytest.approx(1.0)
    text = "\n".join(render_audit(report))
    assert "100% more judged issues than plain" in text and "DRAFT LABELS" in text


def test_no_claim_when_plain_is_as_good_or_better_or_found_nothing(labelled):
    worse = "\n".join(render_audit(_run(labelled, "audit_held_out", _p("setFeeTo"), _a())))
    assert "none. No improvement is shown." in worse
    undefined = _run(labelled, "audit_held_out", _p(None), _a("setFeeTo"))
    assert "undefined (plain found none)" in "\n".join(render_audit(undefined))
    dev = "\n".join(render_audit(_run(labelled, "audit_dev", _p(None), _a("setFeeTo"))))
    assert "dev is exploratory; no claim." in dev


def test_a_refused_request_leaves_the_file_unscored(labelled):
    dead = cmp.Budget(_p("setFeeTo"), 0.0)
    report = _run(labelled, "audit_dev", dead, dead)
    assert report["sides"]["dev"] == {"issues": 0} and report["not_scored"]


def test_run_refuses_without_confirmation_and_held_out_needs_a_declaration(labelled, monkeypatch):
    from evals.source_review import audit_cli

    items, labels, cache = labelled
    files = ai.file_items(items, labels, "dev")
    monkeypatch.setattr(
        audit_cli,
        "prepare",
        lambda args: (items, files, ai.load_sources(files, items, cache, Fake({}))),
    )
    monkeypatch.setattr(
        cmp, "OpenRouterReviewer", lambda *a, **k: pytest.fail("a client was built")
    )
    lines: list[str] = []
    code = audit_cli.main(["run", "--split", "held_out", "--cache", str(cache)], lines.append)
    text = "\n".join(lines)
    assert (
        code == 2
        and "--confirm-spend was not given" in text
        and "--max-spend was not given" in text
    )
    assert "held-out needs --declare" in text
