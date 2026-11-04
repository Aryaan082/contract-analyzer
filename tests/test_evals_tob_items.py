"""Trail of Bits items: the pre-registered rules, the GitHub reader and the dataset files, with a
fake network and no PDF, model or source from the internet."""

import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from evals.source_review import tob_items as ti
from knowledge.parse.pdf_text import Line


def line(text: str, size: float = 11.0, page: int = 5, y: float = 600.0) -> Line:
    return Line(page=page, y=y, x=50.0, text=text, code=False, bold=False, size=size)


def section(*texts: str) -> list[Line]:
    return [
        line("Project Targets", 18.0),
        *(line(t) for t in texts),
        line("Project Coverage", 18.0),
    ]


# ---- project targets -------------------------------------------------------------------------


def test_targets_read_a_repository_and_its_commit():
    lines = section(
        "The engagement involved reviewing the following target.",
        "Vault",
        "Repository https://github.com/acme/vault",
        "Version 0123456789abcdef0123456789abcdef01234567",
        "Type Solidity",
    )
    got = ti.read_targets(lines)
    assert got.problems == ()
    assert [(r.owner, r.name, r.printed) for r in got.repos] == [
        ("acme", "vault", "0123456789abcdef0123456789abcdef01234567")
    ]


def test_targets_accept_a_bare_github_url_and_a_commit_label():
    got = ti.read_targets(section("Repository github.com/acme/vault.git/", "Commit abcdef1"))
    assert [(r.owner, r.name, r.printed) for r in got.repos] == [("acme", "vault", "abcdef1")]


def test_targets_keep_the_first_commit_of_a_repository_listed_twice():
    got = ti.read_targets(
        section(
            "Repository https://github.com/acme/vault",
            "Version Commit 69c68749cefaf959c1874f75e752fd7a71a07f6c",
            "Repository https://github.com/Acme/Vault/",
            "Version Commit 54332c10d6d6587537f821e3f50f3a230059e6b3",
        )
    )
    assert len(got.repos) == 1 and got.repos[0].printed.startswith("69c6874")


def test_targets_two_repositories_are_kept_in_order():
    got = ti.read_targets(
        section(
            "Repository https://github.com/a/one",
            "Version 1111111",
            "Repository https://github.com/b/two",
            "Version 2222222",
        )
    )
    assert [r.name for r in got.repos] == ["one", "two"]


@pytest.mark.parametrize(
    ("texts", "reason"),
    [
        (
            ("Repository n/a (source code provided in a zip file)", "Version January 15, 2025"),
            "GitHub",
        ),
        (("Repository https://gitlab.com/a/b", "Version 1234567"), "GitHub"),
        (("Repository https://github.com/a/b", "Version January 15, 2025"), "no commit"),
        (
            ("Repository https://github.com/a/b", "Version Diff between 03ee167 and 5b046f6"),
            "diff",
        ),
        (
            ("Repository https://github.com/a/b", "Earlier commit c88e958", "Later commit e82bfd6"),
            "diff",
        ),
        (("Repository https://github.com/a/b", "Pull request 124", "Commit f2067bc"), "diff"),
    ],
)
def test_targets_that_cannot_be_used_say_why(texts, reason):
    got = ti.read_targets(section(*texts))
    assert any(reason in p for p in got.problems), got.problems


def test_targets_accept_a_bare_owner_name_and_the_initial_version():
    got = ti.read_targets(
        section(
            "Repository othernet-global/salty-io",
            "Initial Version ec79122645eea6468bd6040f2ad67eab00eae34e",
            "Final Version 65bcd8ea791f4c70452768e4ebe15efd8f1430b6",
        )
    )
    assert got.problems == ()
    assert (got.repos[0].owner, got.repos[0].name) == ("othernet-global", "salty-io")
    assert got.repos[0].printed.startswith("ec79122")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/a/b/pull/198",
        "https://github.com/a/b/tree/main/contracts",
        "N/A",
        "n/a",
        "gitlab.com/a/b",
    ],
)
def test_targets_that_are_not_a_whole_repository_are_refused(url):
    got = ti.read_targets(section(f"Repository {url}", "Version abcdef1"))
    assert got.repos == () and got.problems


def test_targets_without_the_section_say_so():
    assert ti.read_targets([line("Executive Summary", 18.0)]).problems == (
        "no Project Targets section",
    )


def test_a_footer_in_the_section_is_not_read_as_text():
    footer = line("Trail of Bits 8 Review (diff)", y=30.0)
    got = ti.read_targets(
        [
            line("Project Targets", 18.0),
            line("Repository https://github.com/a/b"),
            footer,
            line("Version abcdef1"),
            line("Project Coverage", 18.0),
        ]
    )
    assert got.problems == () and got.repos[0].printed == "abcdef1"


# ---- scope and targets ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["src/Vault.sol", "contracts/a/B.sol", "src/lib/BunniHookLogic.sol", "evm/contracts/I.sol"],
)
def test_source_files_are_in_scope(path):
    assert ti.in_scope(path)


@pytest.mark.parametrize(
    "path",
    [
        "test/Vault.t.sol",
        "src/Vault.t.sol",
        "script/Deploy.s.sol",
        "lib/openzeppelin/ERC20.sol",
        "node_modules/x/Y.sol",
        "src/mocks/Token.sol",
        "contracts/test/Helper.sol",
        "forge-test/A.sol",
        "foundry/forge-script/A.sol",
        "src/MockToken.sol",
        "src/TestHelper.sol",
        "src/VaultHarness.sol",
        "docs/Example.sol",
        "src/Vault.vy",
        "README.md",
    ],
)
def test_tests_mocks_scripts_dependencies_and_other_languages_are_out_of_scope(path):
    assert not ti.in_scope(path)


def test_a_projects_own_nested_lib_folder_is_kept_but_a_top_level_one_is_not():
    assert ti.in_scope("src/lib/Math.sol")
    assert not ti.in_scope("lib/Math.sol")


def test_braces_expand_and_nest():
    assert ti.expand_braces("contracts/{A.sol,B.sol}") == ["contracts/A.sol", "contracts/B.sol"]
    assert ti.expand_braces("x/{a,b/{c,d}}.sol") == ["x/a.sol", "x/b/c.sol", "x/b/d.sol"]


def test_target_tokens_split_and_strip():
    assert ti.target_tokens("src/A.sol, src/B.sol:12-30") == ["src/A.sol", "src/B.sol"]
    assert ti.target_tokens("contracts/v/{V3.sol,V4.sol}") == [
        "contracts/v/V3.sol",
        "contracts/v/V4.sol",
    ]
    assert ti.target_tokens("./src/A.sol#L10-L20 (see below)") == ["src/A.sol"]
    assert ti.target_tokens("OraclePool") == ["OraclePool"]


FILES = [
    ("repo/src/Vault.sol", "src/Vault.sol"),
    ("repo/src/lib/Math.sol", "src/lib/Math.sol"),
    ("repo/src/AmAmm.sol", "src/AmAmm.sol"),
    ("repo/src/a/Dup.sol", "src/a/Dup.sol"),
    ("repo/src/b/Dup.sol", "src/b/Dup.sol"),
]


@pytest.mark.parametrize(
    ("token", "want"),
    [
        ("src/Vault.sol", "repo/src/Vault.sol"),  # a repository path
        ("repo/src/Vault.sol", "repo/src/Vault.sol"),  # a display path
        ("other/repo/src/Vault.sol", "repo/src/Vault.sol"),  # extra leading folders
        ("lib/biddog/AmAMM.sol", "repo/src/AmAmm.sol"),  # the file name, ignoring case
        ("Math.sol", "repo/src/lib/Math.sol"),
        ("src/lib/Math.col", "repo/src/lib/Math.sol"),  # a typo in the extension: the stem
        ("Dup.sol", None),  # two files share the name
        ("Missing.sol", None),
        ("OraclePool", None),  # a contract name is not a file
        ("Vault", None),
    ],
)
def test_resolve_target(token, want):
    assert ti.resolve_target(token, FILES) == want


# ---- sides ---------------------------------------------------------------------------------------


def test_sides_balance_findings_in_hash_order_and_are_stable():
    rows = [("a", 5), ("b", 5), ("c", 1), ("d", 1)]
    first = ti.side_of(rows)
    assert first == ti.side_of(list(reversed(rows)))
    assert set(first.values()) == {"dev", "held_out"}
    order = sorted(rows, key=lambda r: __import__("hashlib").sha256(r[0].encode()).hexdigest())
    assert first[order[0][0]] == "dev"  # a tie goes to dev


def test_one_report_goes_to_dev():
    assert ti.side_of([("only", 3)]) == {"only": "dev"}


# ---- GitHub, with a fake network ---------------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(routes: dict[str, bytes | int], calls: list[str]):
    def opener(request, timeout=0):
        url = request.full_url
        calls.append(url)
        for suffix, body in routes.items():
            if url.endswith(suffix):
                if isinstance(body, int):
                    raise urllib.error.HTTPError(url, body, "x", {}, None)  # type: ignore[arg-type]
                return FakeResponse(body)
        raise urllib.error.HTTPError(url, 404, "nope", {}, None)  # type: ignore[arg-type]

    return opener


def test_github_api_answers_are_cached_on_disk(tmp_path):
    calls: list[str] = []
    gh = ti.GitHub(
        tmp_path, fake_opener({"repos/a/b": json.dumps({"full_name": "a/b"}).encode()}, calls)
    )
    assert gh.repo("a", "b")["full_name"] == "a/b"
    assert gh.repo("a", "b")["full_name"] == "a/b"
    assert len(calls) == 1


@pytest.mark.parametrize("code", [404, 422])
def test_a_missing_repository_or_commit_is_not_found(tmp_path, code):
    gh = ti.GitHub(tmp_path, fake_opener({"commits/abc": code}, []))
    with pytest.raises(ti.NotFound):
        gh.commit("a", "b", "abc")


def test_source_is_fetched_once_at_the_commit_and_cached(tmp_path):
    calls: list[str] = []
    body = b"contract A {}\n"
    gh = ti.GitHub(tmp_path, fake_opener({"/a/b/" + "c" * 40 + "/src/A.sol": body}, calls))
    pin = ti.RepoPin("b", "a", "b", "ccccccc", commit="c" * 40, full_name="a/b")
    assert gh.source(pin, "src/A.sol") == body
    assert gh.source(pin, "src/A.sol") == body
    assert len(calls) == 1 and ("c" * 40) in calls[0]


def test_a_timeout_is_retried_and_then_succeeds(tmp_path):
    state = {"n": 0}

    def opener(request, timeout=0):
        state["n"] += 1
        if state["n"] < 3:
            raise TimeoutError("slow")
        return FakeResponse(b"ok")

    gh = ti.GitHub(tmp_path, opener, pause=0.0)
    assert gh._fetch("https://raw.githubusercontent.com/a/b/c/x", 100) == b"ok"
    assert state["n"] == 3


def test_a_timeout_that_never_clears_is_raised(tmp_path):
    def opener(request, timeout=0):
        raise TimeoutError("slow")

    gh = ti.GitHub(tmp_path, opener, pause=0.0)
    with pytest.raises(TimeoutError):
        gh._fetch("https://raw.githubusercontent.com/a/b/c/x", 100)


def test_the_api_token_goes_only_to_the_api_host(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    seen = {}

    def opener(request, timeout=0):
        seen[request.full_url] = request.get_header("Authorization")
        return FakeResponse(b"{}")

    gh = ti.GitHub(tmp_path, opener, pause=0.0)
    gh._fetch("https://api.github.com/repos/a/b", 100)
    gh._fetch("https://raw.githubusercontent.com/a/b/c/x", 100)
    assert seen["https://api.github.com/repos/a/b"] == "Bearer secret-token"
    assert seen["https://raw.githubusercontent.com/a/b/c/x"] is None


def test_git_blob_hash_matches_git():
    # `printf 'hello\n' | git hash-object --stdin`
    assert ti.git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_a_file_that_does_not_hash_to_its_pin_is_an_error(tmp_path):
    body = b"contract A {}\n"
    gh = ti.GitHub(tmp_path, fake_opener({"/src/A.sol": body}, []))
    pin = ti.RepoPin("b", "a", "b", "ccccccc", commit="c" * 40, full_name="a/b")
    report = ti.Report("r", "r.pdf", 1, "s", "b", status="kept", repos=(pin,))
    with pytest.raises(ValueError, match="pinned blob hash"):
        ti.texts_of(report, [ti.PinnedFile("b", "src/A.sol", len(body), "0" * 40)], gh)
    good = ti.PinnedFile("b", "src/A.sol", len(body), ti.git_blob_sha(body))
    assert ti.texts_of(report, [good], gh) == [("src/A.sol", body.decode())]


def test_display_path_is_prefixed_only_with_several_repositories():
    one = (ti.RepoPin("a", "o", "a", "1"),)
    two = (*one, ti.RepoPin("b", "o", "b", "2"))
    assert ti.display_path(one, "a", "src/X.sol") == "src/X.sol"
    assert ti.display_path(two, "a", "src/X.sol") == "a/src/X.sol"


# ---- sizes and the dataset files ---------------------------------------------------------------


CODE = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
/// natspec that the pipeline does not count
contract Vault {
    uint256 public total;
    function add(uint256 x) external { total += x; }
}
"""


def test_pipeline_tokens_count_stripped_code_not_comments():
    commented = CODE + "// " + "x" * 5000 + "\n"
    assert ti.pipeline_tokens([("src/V.sol", commented)]) == ti.pipeline_tokens(
        [("src/V.sol", CODE)]
    )
    assert ti.pipeline_tokens([("src/V.sol", CODE)]) > 0


def test_finalize_applies_the_ceiling_and_the_sides(tmp_path):
    bodies = {
        "src/V.sol": CODE.encode(),
        "src/W.sol": (CODE.replace("Vault", "Wallet") * 40).encode(),
    }
    gh = ti.GitHub(tmp_path, fake_opener({f"/{p}": b for p, b in bodies.items()}, []))
    plans = []
    for slug, path in (("small", "src/V.sol"), ("big", "src/W.sol")):
        pin = ti.RepoPin("r", "o", "r", "abcdef1", commit="c" * 40, full_name="o/r")
        report = ti.Report(slug, f"{slug}.pdf", 1, "s", "b", status="candidate", repos=(pin,))
        label = ti.Label(f"{slug}:T-1", slug, "T-1", "high", "t", path, (path,), "c", "Low", 3)
        plans.append(
            ti.Plan(
                report,
                [ti.PinnedFile("r", path, len(bodies[path]), ti.git_blob_sha(bodies[path]))],
                [label],
            )
        )
    done = ti.finalize(plans, gh, ceiling=1000)
    by = {p.report.slug: p.report for p in done}
    assert by["small"].status == "kept" and by["small"].side == "dev" and by["small"].tokens > 0
    assert by["big"].status == "dropped" and "over the ceiling" in by["big"].reason
    assert by["big"].side == ""


def test_round_trip_of_the_dataset_files(tmp_path):
    pin = ti.RepoPin("r", "o", "r", "abcdef1", "c" * 40, "t" * 40, "o/r", "MIT", 10)
    report = ti.Report("slug", "slug.pdf", 5, "s" * 64, "b" * 40, "kept", "", "dev", (pin,), 99, 1)
    ti.write_reports([report], tmp_path / "reports.toml")
    assert ti.load_reports(tmp_path / "reports.toml") == [report]
    files = {"slug": [ti.PinnedFile("r", "src/A.sol", 4, "f" * 40)]}
    ti.write_files(files, tmp_path / "files.toml")
    assert ti.load_files(tmp_path / "files.toml") == files
    label = ti.Label(
        "slug:T-1",
        "slug",
        "T-1",
        "high",
        'A "quoted" title',
        "src/A.sol",
        ("src/A.sol",),
        "Data",
        "Low",
        7,
    )
    ti.write_labels([label], tmp_path / "labels.toml")
    assert ti.load_labels(tmp_path / "labels.toml") == [label]


def test_the_committed_dataset_agrees_with_itself():
    reports = {r.slug: r for r in ti.load_reports()}
    assert tuple(reports) == ti.SLUGS
    assert all(len(r.sha256) == 64 and len(r.blob) == 40 for r in reports.values())
    files, labels = ti.load_files(), ti.load_labels()
    for lab in labels:
        assert reports[lab.report].status in ("candidate", "kept")
        display = {
            ti.display_path(reports[lab.report].repos, f.repo, f.path) for f in files[lab.report]
        }
        assert set(lab.files) <= display and lab.files
        assert lab.severity in ti.KEPT_SEVERITIES
    for slug, r in reports.items():
        if r.status in ("candidate", "kept"):
            assert r.kept_findings == sum(lab.report == slug for lab in labels)
            assert all(ti.in_scope(f.path) for f in files[slug])
        else:
            assert r.reason and slug not in files
    for r in reports.values():
        if r.kept:
            assert r.side in ti.SIDES and 0 < r.tokens <= ti.CEILING_TOKENS


def test_nothing_third_party_is_in_the_dataset_files():
    solidity = re.compile(r"^\s*(function \w+\(|pragma solidity|contract \w+ )", re.M)
    for path in (ti.REPORTS_PATH, ti.FILES_PATH, ti.LABELS_PATH):
        assert not solidity.search(Path(path).read_text())
