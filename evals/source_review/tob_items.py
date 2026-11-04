"""Trail of Bits report items: known bugs in audited Solidity (evals/datasets/tob_items/README.md).

A Trail of Bits (ToB) review lists findings with a severity and a target file, and names the
repository and commit it audited. This module turns a pinned list of ToB PDFs into

* `reports.toml`   each report: its pinned PDF, what the rules decided (kept or dropped, and why),
                   the repositories and commits, the side (dev or held-out), the bundle size;
* `files.toml`     the in-scope Solidity files of each kept report: path, size, git blob hash;
* `labels.toml`    the findings to look for: report, ToB id, severity, title, target as printed,
                   the files it resolves to, category, difficulty, page. No ToB prose and no code.

Every rule is mechanical: no model reads a PDF or a file here. The PDF text, the repository listing
and the source are fetched into a cache OUTSIDE the repo; nothing third-party is written into it.
The PDFs and the code are untrusted data: they are only read as text (`pypdf`, `git` blob hashes),
never executed, built or installed.

The rules, in the order they apply to a report (one that fails a rule is dropped with it):

1. the parser reads it and it has a High, Medium or Undetermined finding;
2. its targets are Solidity code in public GitHub repositories, each at a commit, and it is not a
   review of a diff or a pull request (the audited scope would then be the changed lines only);
3. the repository, the commit and its tree exist on GitHub;
4. the code that is in scope (`in_scope`) is at most 200,000 tokens, counted as the pipeline counts
   (stripped text, `review.known`), the same ceiling for both arms;
5. at least one of its High, Medium or Undetermined findings names a Solidity file of that code
   (`resolve_target`).
"""

import hashlib
import json
import os
import posixpath
import re
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pypdf.errors import PyPdfError

from evals.source_review.cards import DATASET_DIR
from evals.source_review.compare import Source
from evals.source_review.hostile import make_bundle
from knowledge.parse.pdf_text import Line, read_lines, repair_unknown_characters
from knowledge.parse.tob import BIG_HEADING_SIZE, ParsedReport, ParseError, body_lines, parse_report
from review.known import classify, load_known
from review.lexer import lex

DATASET = DATASET_DIR.parent / "tob_items"
REPORTS_PATH = DATASET / "reports.toml"
FILES_PATH = DATASET / "files.toml"
LABELS_PATH = DATASET / "labels.toml"
PUBLICATIONS = "trailofbits/publications"
PUBLICATIONS_COMMIT = "89940dfbec07ad03b0989e67d78e84250765366b"  # the commit knowledge/ pins too
KEPT_SEVERITIES = ("critical", "high", "medium", "undetermined")
FIRST_SLUGS = (
    "2025-01-bacon-labs-bunniv2",
    "2025-01-beethovenx-sonicstaking",
    "2025-01-parabollabs-protocolcontractsupdates",
    "2025-01-shape-gasback",
    "2025-02-chainlink-customsendersreceivers",
    "2025-02-franklintempleton-benjicontracts",
    "2025-02-automata-dcap-attestation-onchain-pccs",
    "2025-03-offchain-custom-fee-erc20-bridge",
    "2025-03-offchain-custom-fee-token-exchange-rate",
    "2025-03-offchain-disablegateway-action",
    "2025-03-offchain-security-council-rotation",
    "2025-03-offchain-sequencer-liveness",
    "2025-03-otim-smart-wallet",
    "2025-04-reserve-folio-solidity",
    "2025-04-offchainlabs-reward-distributor-fixes",
    "2025-05-caplabs-coveredagentprotocol",
    "2025-05-fabriclabs-zipperprotocol",
    "2025-05-shapenetwork-token",
    "2025-09-shapenetwork-buybackcontract",
    "2026-03-shape-tokenlock",
    "2025-06-reserveprotocol-solidity400",
    "2025-07-offchain-upgrade-executor",
    "2025-06-offchain-setcoregovernorquorumaction",
    "2025-08-gemini-smartwallet",
    "2025-10-franklintempleton-benjiswapcontract",
    "2025-10-radiustechnology-evmauth",
    "2025-1-everstake-ethereum-staking-protocol",
    "2025-11-liquidium-cross-chain-lending",
    "2026-04-gensyn-bridged-gensyn-token",
    "2026-04-gensyn-buyback-and-burn-vault",
    "2026-04-gensyn-delphidynamicparamutuelmarkets",
    "2026-04-gensyn-erc-20-token",
    "2026-04-franklintempleton-benjiswapdifferentialreview",
)  # the first 33 reports Aryaan approved on 2026-11-03; each file is `<slug>-securityreview.pdf`
PDF_MAX_BYTES = 12_000_000
RETRIES = 4  # a timeout or a 5xx is tried again, 2, 4 and 8 seconds apart
WIDENED_FILES = (
    "2026-09-gensyn-delphi-lmsr-markets-automated-settlement-securityreview.pdf",
    "2026-06-arcuslabs-rootchain-securityreview.pdf",
    "2025-10-vechain-vechainthorhayabusaupgrade-securityreview.pdf",
    "2025-04-lagrange-latoken-securityreview.pdf",
    "2025-04-serai-dex-security-review.pdf",
    "2024-08-elixir-technologies-ltd-elixir-protocol-securityreview.pdf",
    "2024-06-aladdinfx-oracle-securityreview.pdf",
    "2024-05-panoptic-liquidation-engine-invariant-development.pdf",
    "2024-05-ailayerlabs-6079smartcontracts-securityreview.pdf",
    "2024-05-lisksmartcontracts-securityreview.pdf",
    "2024-05-parabollabs-protocolcontracts-securityreview.pdf",
    "2024-05-wonderland-prophet-securityreview.pdf",
    "2024-03-curvance-invariant-development.pdf",
    "2024-03-lisksmartcontracts-securityreview.pdf",
    "2024-03-aladdinfxprotocol-securityreview.pdf",
    "2024-03-intuition-smart-contracts-securityreview.pdf",
    "2024-01-metalayerblast-securityreview.pdf",
    "2023-12-acronym-foundation-securityreview.pdf",
    "2024-01-pyth-entropy-securityreview.pdf",
    "2023-11-immutable-zkevmbridgecontracts-securityreview.pdf",
    "2023-10-saltyio-securityreview.pdf",
    "2023-09-hyperlane-securityreview.pdf",
    "2023-09-elixir-securityreview.pdf",
    "2023-08-immutable-securityreview.pdf",
    "2023-07-dappos-securityreview.pdf",
    "2023-07-sandclock-securityreview.pdf",
    "2023-07-arcade-securityreview.pdf",
    "2023-05-fraxgov-securityreview.pdf",
    "2023-05-franklintempleton-moneymarket-securityreview.pdf",
    "2023-04-ajnalabs-securityreview.pdf",
    "2023-04-tempus-raft-securityreview.pdf",
    "2023-04-mysoloans-securityreview.pdf",
    "2023-04-smardexamm-realestateexecutives-securityreview.pdf",
    "2023-03-atlendis-atlendissmartcontracts-securityreview.pdf",
    "2023-03-primitive-securityreview.pdf",
    "2023-02-nestedfinance-smartcontracts-securityreview.pdf",
)  # 36 more, approved 2026-11-03 (survey: tiers A, B and C)
PDF_FILES = (*(f"{s}-securityreview.pdf" for s in FIRST_SLUGS), *WIDENED_FILES)


def slug_of(file: str) -> str:
    """The report's name: its file name without `.pdf` and without a trailing `-securityreview`."""
    return file.removesuffix(".pdf").removesuffix("-securityreview")


SLUGS = tuple(slug_of(f) for f in PDF_FILES)
CEILING_TOKENS = 200_000  # in-scope code, pipeline-counted, one ceiling for both arms
SIDES = ("dev", "held_out")
MAX_BYTES = 2_000_000  # one source file
USER_AGENT = "contract-analyzer-evals"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
PDF_RAW = f"{RAW}/{PUBLICATIONS}/{PUBLICATIONS_COMMIT}/reviews/{{file}}"

Opener = Callable[..., object]


class NotFound(Exception):
    """A GitHub or raw URL that answers 404 (or 422 for a commit the repository does not have)."""


# ---- the pinned report list --------------------------------------------------------------------


@dataclass(frozen=True)
class RepoPin:
    label: str  # the repository's name, e.g. `bunni-v2`: the path prefix when a report has several
    owner: str  # as printed in the report
    name: str
    printed: str  # the commit as the report printed it (7 to 40 hex characters)
    commit: str = ""  # the full hash, resolved through GitHub
    tree: str = ""
    full_name: str = ""  # what GitHub calls the repository now (it may have been renamed)
    licence: str = ""  # the SPDX id GitHub reports, or NOASSERTION, or none
    files_bytes: int = 0  # bytes of in-scope files


@dataclass(frozen=True)
class PinnedFile:
    repo: str  # RepoPin.label
    path: str  # in the repository
    size: int
    blob: str  # git blob hash: the fetched bytes must hash to it


@dataclass(frozen=True)
class Label:
    id: str  # `<report>:<ToB id>`
    report: str
    finding_id: str
    severity: str
    title: str
    target: str  # as ToB printed it
    files: tuple[str, ...]  # display paths in the bundle that the target resolves to
    category: str
    difficulty: str
    page: int


@dataclass(frozen=True)
class Report:
    slug: str
    file: str
    size: int
    sha256: str
    blob: str
    status: str = "pending"  # pending | candidate | kept | dropped
    reason: str = ""
    side: str = ""
    repos: tuple[RepoPin, ...] = ()
    tokens: int = 0  # in-scope code, pipeline-counted (0 until the sources are fetched)
    kept_findings: int = 0

    @property
    def kept(self) -> bool:
        return self.status == "kept"


def display_path(repos: Sequence[RepoPin], repo: str, path: str) -> str:
    """The path both arms see: the repository path, prefixed with the repository name when the
    report audited more than one repository (so two `src/` trees cannot collide)."""
    return f"{repo}/{path}" if len(repos) > 1 else path


def load_reports(path: Path = REPORTS_PATH) -> list[Report]:
    data = tomllib.loads(path.read_text())
    out = []
    for r in data["report"]:
        repos = tuple(RepoPin(**p) for p in r.get("repo", []))
        fields = {k: v for k, v in r.items() if k != "repo"}
        out.append(Report(repos=repos, **fields))
    return out


def load_files(path: Path = FILES_PATH) -> dict[str, list[PinnedFile]]:
    data = tomllib.loads(path.read_text())
    out: dict[str, list[PinnedFile]] = {}
    for f in data.get("file", []):
        out.setdefault(f["report"], []).append(
            PinnedFile(f["repo"], f["path"], int(f["size"]), f["blob"])
        )
    return out


def load_labels(path: Path = LABELS_PATH) -> list[Label]:
    data = tomllib.loads(path.read_text())
    return [
        Label(
            id=i["id"],
            report=i["report"],
            finding_id=i["finding_id"],
            severity=i["severity"],
            title=i["title"],
            target=i["target"],
            files=tuple(i["files"]),
            category=i["category"],
            difficulty=i["difficulty"],
            page=int(i["page"]),
        )
        for i in data.get("label", [])
    ]


# ---- reading a PDF -----------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoRef:
    url: str
    owner: str
    name: str
    printed: str  # the commit as printed; "" if none


@dataclass(frozen=True)
class Targets:
    repos: tuple[RepoRef, ...]
    problems: tuple[str, ...]  # why the code cannot be used; empty if it can


_REPO_LINE = re.compile(r"^Repository\s+(.*)$")
_VERSION_LINE = re.compile(r"^(?:Initial\s+)?(?:Version|Commit)\b\s*(.*)$", re.I)
_COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b")
_DIFF = re.compile(r"\bdiff\b|earlier commit|later commit|pull request", re.I)
_GITHUB = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?github\.com/)?"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)  # a URL, or a bare `owner/name`; a URL with more path (a tree or pull request) does not match


def read_targets(lines: Sequence[Line]) -> Targets:
    """The repositories and commits of the 'Project Targets' section, and why the report cannot be
    used if it cannot: a diff or pull request review, a repository that is not on GitHub or not
    given, a repository without a commit. The first commit printed for a repository is the one
    audited (a second one is the fixes; with an `Initial Version` and a `Final Version`, the
    initial one)."""
    body, _ = body_lines(list(lines))
    starts = [
        i
        for i, ln in enumerate(body)
        if ln.size >= BIG_HEADING_SIZE and re.match(r"project targets", ln.text, re.I)
    ]
    if not starts:
        return Targets((), ("no Project Targets section",))
    start = starts[-1]
    end = next(
        (i for i in range(start + 1, len(body)) if body[i].size >= BIG_HEADING_SIZE), len(body)
    )
    section = body[start + 1 : end]
    problems: list[str] = []
    if any(_DIFF.search(ln.text) for ln in section):
        problems.append("a review of a diff or pull request")
    entries: list[list[str]] = []  # [url, commit text]
    for ln in section:
        if m := _REPO_LINE.match(ln.text):
            entries.append([m.group(1).strip(), ""])
        elif entries and (m := _VERSION_LINE.match(ln.text)) and not entries[-1][1]:
            commit = _COMMIT.search(m.group(1))
            entries[-1][1] = commit.group(0) if commit else "-"
    if not entries:
        problems.append("no repository listed")
    repos: dict[str, RepoRef] = {}
    for url, commit in entries:
        match = _GITHUB.match(url)
        if not match or url.strip().lower() == "n/a":
            problems.append(f"repository is not a public GitHub URL: {url[:60]}")
            continue
        if commit in ("", "-"):
            problems.append(f"no commit printed for {url[:60]}")
            continue
        key = f"{match.group(1)}/{match.group(2)}".lower()
        repos.setdefault(key, RepoRef(url, match.group(1), match.group(2), commit))
    return Targets(tuple(repos.values()), tuple(dict.fromkeys(problems)))


@dataclass(frozen=True)
class ParsedPdf:
    report: ParsedReport | None
    targets: Targets
    error: str = ""


def read_pdf(path: Path) -> ParsedPdf:
    """Parse one PDF with the knowledge parser. Never raises on a report it cannot read."""
    try:
        lines, _ = repair_unknown_characters(read_lines(path))
    except (PyPdfError, ValueError, OSError) as err:
        return ParsedPdf(None, Targets((), ()), str(err)[:200])
    try:
        parsed = parse_report(lines)
    except ParseError as err:
        return ParsedPdf(None, read_targets(lines), str(err)[:200])
    return ParsedPdf(parsed, read_targets(lines))


# ---- which findings, which files ---------------------------------------------------------------

_TOP_ONLY_DIRS = frozenset(
    {
        "lib", "libs", "vendor", "vendors", "dependencies", "third_party", "third-party",
        "forge-std", "out", "artifacts", "cache", "broadcast",
    }
)  # fmt: skip
_ANY_DIRS = frozenset(
    {
        "node_modules", "deploy", "deployments", "migrations", "fuzz", "invariant", "invariants",
        "echidna", "medusa", "harness", "harnesses", "certora", "audit", "audits", "docs",
        "example", "examples", "tmp", "temp",
    }
)  # fmt: skip
_TEST_WORDS = frozenset({"test", "tests", "script", "scripts", "mock", "mocks"})


def in_scope(path: str) -> bool:
    """A Solidity file of the audited code: not a test, mock, script, deployment or vendored
    dependency. Dependency folders (`lib/`, `out/`) count only at the top of the repository, so a
    project's own `src/lib/` is kept."""
    if not path.endswith(".sol"):
        return False
    parts = path.split("/")
    for depth, part in enumerate(parts[:-1]):
        low = part.lower()
        if low in _ANY_DIRS or (depth == 0 and low in _TOP_ONLY_DIRS):
            return False
        if _TEST_WORDS & set(re.split(r"[-_.]", low)):
            return False
    name = parts[-1]
    return not (
        re.search(r"\.(t|s)\.sol$", name)
        or re.match(r"(Mock|Test)", name)
        or re.search(r"(Mock|Test|Harness)s?\.sol$", name)
    )


def expand_braces(text: str) -> list[str]:
    """`contracts/{A.sol,B.sol}` -> two paths. Nested braces expand innermost first; a path that
    comes out twice is listed once."""
    match = re.search(r"\{([^{}]*)\}", text)
    if not match:
        return [text]
    out: list[str] = []
    for option in match.group(1).split(","):
        out += expand_braces(text[: match.start()] + option.strip() + text[match.end() :])
    return list(dict.fromkeys(out))


def target_tokens(target: str) -> list[str]:
    """The file names a ToB `Target:` line lists: comma or semicolon separated, braces expanded,
    line numbers and bracketed notes removed."""
    tokens = []
    for piece in expand_braces(target):
        for part in re.split(r"[;,\n]", piece):
            part = re.sub(r"\(.*?\)", "", part)
            part = re.sub(r"(?:#L\d+(?:-L?\d+)?|:\d+(?:-\d+)?)\s*$", "", part.strip())
            part = part.strip().lstrip("./").strip()
            if part:
                tokens.append(part)
    return tokens


def resolve_target(token: str, files: Sequence[tuple[str, str]]) -> str | None:
    """The one file a target token names, or None. `files` are (display path, path in the repo) of
    every Solidity file at the commit. In order: the exact path; a path that ends the token or that
    the token ends (`repo/src/A.sol` against `src/A.sol`); the file name, ignoring case; and, for
    a token whose extension is not `.sol` (a typo such as `.col`), the stem. The match must be
    unique among the files, else the token resolves to nothing."""
    name = posixpath.basename(token)
    stem, ext = posixpath.splitext(name)
    if not ext:
        return None  # a contract or topic name, not a file

    def unique(found: list[str]) -> str | None:
        found = sorted(set(found))
        return found[0] if len(found) == 1 else None

    if ext.lower() == ".sol":
        exact = [d for d, p in files if token in (d, p)]
        if exact:
            return unique(exact)
        suffix = [
            d
            for d, p in files
            if token.endswith("/" + p) or p.endswith("/" + token) or d.endswith("/" + token)
        ]
        if suffix:
            return unique(suffix)
        return unique([d for d, p in files if posixpath.basename(p).lower() == name.lower()])
    return unique(
        [
            d
            for d, p in files
            if posixpath.splitext(posixpath.basename(p))[0].lower() == stem.lower()
        ]
    )


def label_findings(
    slug: str, parsed: ParsedReport, all_files: Sequence[tuple[str, str]], scope: set[str]
) -> list[Label]:
    """The kept findings (High, Medium, Undetermined) whose target resolves to an in-scope file."""
    out = []
    for raw in parsed.findings:
        if raw.severity not in KEPT_SEVERITIES or not raw.target:
            continue
        resolved = []
        for token in target_tokens(raw.target):
            found = resolve_target(token, all_files)
            if found and found in scope and found not in resolved:
                resolved.append(found)
        if not resolved:
            continue
        out.append(
            Label(
                id=f"{slug}:{raw.finding_id or f'F{raw.number:02d}'}",
                report=slug,
                finding_id=raw.finding_id or f"F{raw.number:02d}",
                severity=raw.severity,
                title=raw.title,
                target=raw.target,
                files=tuple(resolved),
                category=raw.category or "",
                difficulty=raw.difficulty or "",
                page=raw.page,
            )
        )
    return out


def side_of(slugs_with_counts: Sequence[tuple[str, int]]) -> dict[str, str]:
    """Whole reports to dev or held-out: in hash order of the slug, each report goes to the side
    with fewer labelled findings so far, a tie to dev (the rule of the Sherlock audit items)."""
    totals = {"dev": 0, "held_out": 0}
    out = {}
    for slug, count in sorted(
        slugs_with_counts, key=lambda x: hashlib.sha256(x[0].encode()).hexdigest()
    ):
        side = "dev" if totals["dev"] <= totals["held_out"] else "held_out"
        out[slug] = side
        totals[side] += count
    return out


# ---- GitHub (read-only) ------------------------------------------------------------------------


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 (git's own hash)


@dataclass
class GitHub:
    """Read-only GETs with a disk cache outside the repo. Metadata comes from api.github.com (repo,
    commit, tree: no source); source comes from raw.githubusercontent.com at a full commit hash."""

    cache: Path
    opener: Opener = urllib.request.urlopen
    calls: int = field(default=0, init=False)
    pause: float = 2.0  # seconds before the first retry; it doubles

    def _fetch(self, url: str, limit: int) -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token and url.startswith(API + "/"):  # only api.github.com ever sees it
            headers["Authorization"] = f"Bearer {token}"  # read-only; never printed or stored
        request = urllib.request.Request(url, headers=headers)
        self.calls += 1
        for attempt in range(RETRIES):
            try:
                with self.opener(request, timeout=60) as response:  # type: ignore[operator]
                    body = response.read(limit + 1)
                break
            except urllib.error.HTTPError as err:
                if err.code in (404, 422):
                    raise NotFound(url) from None
                if err.code < 500 or attempt == RETRIES - 1:
                    raise
            except (TimeoutError, urllib.error.URLError, ConnectionError):
                if attempt == RETRIES - 1:
                    raise
            time.sleep(self.pause * 2**attempt)
        if len(body) > limit:
            raise ValueError(f"{url} is over {limit} bytes")
        return body

    def api(self, path: str) -> dict:
        target = self.cache / "github" / (hashlib.sha256(path.encode()).hexdigest()[:24] + ".json")
        if target.exists():
            return json.loads(target.read_text())
        data = json.loads(self._fetch(f"{API}/{path}", 20_000_000))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data))
        return data

    def repo(self, owner: str, name: str) -> dict:
        return self.api(f"repos/{owner}/{name}")

    def commit(self, owner: str, name: str, ref: str) -> dict:
        return self.api(f"repos/{owner}/{name}/commits/{ref}")

    def tree(self, owner: str, name: str, tree_sha: str) -> dict:
        return self.api(f"repos/{owner}/{name}/git/trees/{tree_sha}?recursive=1")

    def source(self, pin: RepoPin, path: str) -> bytes:
        """One file of a repository at its pinned commit. Cached by the commit and the path."""
        target = self.cache / "src" / pin.full_name / pin.commit / path
        if target.exists():
            return target.read_bytes()
        url = f"{RAW}/{pin.full_name}/{pin.commit}/{urllib.parse.quote(path)}"
        body = self._fetch(url, MAX_BYTES)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return body

    def pdf(self, file: str) -> Path:
        """A report PDF from the pinned publications commit, saved under `cache/pdfs/` (a new
        folder holding nothing but PDFs). Existing files are kept."""
        target = self.cache / "pdfs" / file
        if not target.exists():
            body = self._fetch(PDF_RAW.format(file=urllib.parse.quote(file)), PDF_MAX_BYTES)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        return target


def pin_pdfs(gh: GitHub) -> list[Report]:
    """The pinned report list: each PDF fetched if missing, hashed, and checked against the
    publications repository's own listing (size and git blob hash at the pinned commit)."""
    owner, name = PUBLICATIONS.split("/")
    tree = gh.tree(
        owner, name, gh.commit(owner, name, PUBLICATIONS_COMMIT)["commit"]["tree"]["sha"]
    )
    listed = {e["path"]: (int(e["size"]), e["sha"]) for e in tree["tree"] if e["type"] == "blob"}
    out = []
    for file in PDF_FILES:
        data = gh.pdf(file).read_bytes()
        blob = git_blob_sha(data)
        if listed.get(f"reviews/{file}") != (len(data), blob):
            raise ValueError(f"{file} is not the file the publications repository lists")
        out.append(Report(slug_of(file), file, len(data), hashlib.sha256(data).hexdigest(), blob))
    return out


# ---- the plan (metadata only: no source is read) -----------------------------------------------


@dataclass
class Plan:
    report: Report
    files: list[PinnedFile] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)


def plan_report(base: Report, pdf: Path, gh: GitHub) -> Plan:
    """Rules 1 to 3 and 5 for one report, from the PDF and GitHub's listing; no source is fetched.
    The result is `candidate` (rule 4, the size ceiling, needs the source) or `dropped` with a
    reason."""

    def dropped(reason: str, repos: tuple[RepoPin, ...] = ()) -> Plan:
        return Plan(
            Report(**{**base.__dict__, "status": "dropped", "reason": reason, "repos": repos})
        )

    parsed = read_pdf(pdf)
    if parsed.report is None:
        return dropped(f"the parser cannot read it ({parsed.error})")
    wanted = [f for f in parsed.report.findings if f.severity in KEPT_SEVERITIES]
    if not wanted:
        return dropped("no High, Medium or Undetermined finding")
    if parsed.targets.problems or not parsed.targets.repos:
        return dropped("; ".join(parsed.targets.problems) or "no repository listed")

    pins: list[RepoPin] = []
    listing: dict[str, list[tuple[str, int, str]]] = {}  # label -> (path, size, blob) of .sol files
    for ref in parsed.targets.repos:
        try:
            info = gh.repo(ref.owner, ref.name)
            full = info["full_name"]  # the repository's name now (it may have been renamed)
            owner, name = full.split("/", 1)
            commit = gh.commit(owner, name, ref.printed)
            tree = gh.tree(owner, name, commit["commit"]["tree"]["sha"])
        except NotFound:
            return dropped(f"{ref.owner}/{ref.name} at {ref.printed} is not on GitHub", tuple(pins))
        if tree.get("truncated"):
            return dropped(f"{full}: the file listing is truncated", tuple(pins))
        pins.append(
            RepoPin(
                label=ref.name,
                owner=ref.owner,
                name=ref.name,
                printed=ref.printed,
                commit=commit["sha"],
                tree=commit["commit"]["tree"]["sha"],
                full_name=full,
                licence=(info.get("license") or {}).get("spdx_id") or "none",
            )
        )
        listing[ref.name] = [
            (e["path"], int(e["size"]), e["sha"])
            for e in tree["tree"]
            if e["type"] == "blob" and e["path"].endswith(".sol")
        ]
    all_files = [
        (display_path(pins, label, p), p) for label, rows in listing.items() for p, _, _ in rows
    ]
    files = [
        PinnedFile(label, p, size, blob)
        for label, rows in listing.items()
        for p, size, blob in rows
        if in_scope(p)
    ]
    scope = {display_path(pins, f.repo, f.path) for f in files}
    pins = [
        RepoPin(**{**p.__dict__, "files_bytes": sum(f.size for f in files if f.repo == p.label)})
        for p in pins
    ]
    labels = label_findings(base.slug, parsed.report, all_files, scope)
    if not labels:
        return dropped("no finding names a Solidity file of the audited code", tuple(pins))
    report = Report(
        **{
            **base.__dict__,
            "status": "candidate",
            "repos": tuple(pins),
            "kept_findings": len(labels),
        }
    )
    return Plan(report, sorted(files, key=lambda f: (f.repo, f.path)), labels)


# ---- the source (fetched only after Aryaan approves each repository) ----


def texts_of(report: Report, files: Sequence[PinnedFile], gh: GitHub) -> list[tuple[str, str]]:
    """(display path, text) of each in-scope file, fetched at the pinned commit and checked against
    the git blob hash in files.toml. A file that does not hash to it is an error, not a guess."""
    pins = {p.label: p for p in report.repos}
    out = []
    for f in files:
        data = gh.source(pins[f.repo], f.path)
        if git_blob_sha(data) != f.blob:
            raise ValueError(
                f"{report.slug}: {f.repo}/{f.path} does not match its pinned blob hash"
            )
        out.append((display_path(report.repos, f.repo, f.path), data.decode("utf-8", "replace")))
    return out


def pipeline_tokens(texts: Sequence[tuple[str, str]]) -> int:
    """The size of the code as the pipeline counts it: stripped text of the definitions that are
    new, i.e. not a copy of a known library release (`review.known`), at 3.5 characters a token."""
    bundle = make_bundle(list(texts))
    lexed = {f.file_id: lex(f.file_id, f.content) for f in bundle.files}
    return classify(bundle.files, lexed, load_known()).new_tokens


def load_sources(
    reports: Sequence[Report], files: dict[str, list[PinnedFile]], gh: GitHub
) -> dict[str, Source]:
    """The bundle both arms read, per kept report: every in-scope file under its display path."""
    return {
        r.slug: Source(make_bundle(texts_of(r, files[r.slug], gh)), None) for r in reports if r.kept
    }


def finalize(plans: Sequence[Plan], gh: GitHub, ceiling: int = CEILING_TOKENS) -> list[Plan]:
    """Rule 4 for every candidate (fetches its source), then the sides. Returns the plans with
    `kept` or `dropped` reports; a dropped report loses its files and labels."""
    done: list[Plan] = []
    for plan in plans:
        r = plan.report
        if r.status != "candidate":
            done.append(plan)
            continue
        tokens = pipeline_tokens(texts_of(r, plan.files, gh))
        if tokens > ceiling:
            reason = f"in-scope code is {tokens:,} tokens, over the ceiling of {ceiling:,}"
            done.append(
                Plan(
                    Report(
                        **{**r.__dict__, "status": "dropped", "reason": reason, "tokens": tokens}
                    )
                )
            )
        else:
            done.append(
                Plan(
                    Report(**{**r.__dict__, "status": "kept", "tokens": tokens}),
                    plan.files,
                    plan.labels,
                )
            )
    sides = side_of([(p.report.slug, len(p.labels)) for p in done if p.report.kept])
    return [
        Plan(
            Report(**{**p.report.__dict__, "side": sides.get(p.report.slug, "")}), p.files, p.labels
        )
        for p in done
    ]


# ---- writing the dataset files -----------------------------------------------------------------


def q(value: object) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_reports(reports: Sequence[Report], path: Path = REPORTS_PATH) -> None:
    lines = [
        "# The 33 pinned Trail of Bits PDFs and what the pre-registered rules decided for each.",
        "# Written by `python -m evals.source_review tob plan|fetch`. No model is involved.",
        "",
    ]
    for r in reports:
        lines += [
            "[[report]]",
            f"slug = {q(r.slug)}",
            f"file = {q(r.file)}",
            f"size = {r.size}",
            f"sha256 = {q(r.sha256)}",
            f"blob = {q(r.blob)}",
            f"status = {q(r.status)}",
            f"reason = {q(r.reason)}",
            f"side = {q(r.side)}",
            f"tokens = {r.tokens}",
            f"kept_findings = {r.kept_findings}",
        ]
        for p in r.repos:
            lines += [
                "",
                "[[report.repo]]",
                f"label = {q(p.label)}",
                f"owner = {q(p.owner)}",
                f"name = {q(p.name)}",
                f"printed = {q(p.printed)}",
                f"commit = {q(p.commit)}",
                f"tree = {q(p.tree)}",
                f"full_name = {q(p.full_name)}",
                f"licence = {q(p.licence)}",
                f"files_bytes = {p.files_bytes}",
            ]
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def write_files(files: dict[str, Sequence[PinnedFile]], path: Path = FILES_PATH) -> None:
    lines = ["# In-scope Solidity files of each kept report: path, bytes, git blob hash.", ""]
    for slug in sorted(files):
        for f in files[slug]:
            lines += [
                "[[file]]",
                f"report = {q(slug)}",
                f"repo = {q(f.repo)}",
                f"path = {q(f.path)}",
                f"size = {f.size}",
                f"blob = {q(f.blob)}",
                "",
            ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def write_labels(labels: Sequence[Label], path: Path = LABELS_PATH) -> None:
    lines = [
        "# The findings to look for. Draft labels: made by rule from the report's Target field.",
        "# Source: Trail of Bits publications (CC-BY-SA-4.0). Titles and ids only, no report text.",
        "",
    ]
    for lab in labels:
        files = ", ".join(q(f) for f in lab.files)
        lines += [
            "[[label]]",
            f"id = {q(lab.id)}",
            f"report = {q(lab.report)}",
            f"finding_id = {q(lab.finding_id)}",
            f"severity = {q(lab.severity)}",
            f"title = {q(lab.title)}",
            f"target = {q(lab.target)}",
            f"files = [{files}]",
            f"category = {q(lab.category)}",
            f"difficulty = {q(lab.difficulty)}",
            f"page = {lab.page}",
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
