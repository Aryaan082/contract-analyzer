"""Real audit items: judged, known bugs in contest code (evals/datasets/audit_items/README.md).

`items.toml` lists Sherlock issues (title, file, linked lines, size, side, rank) and the pinned
commit of each contest. This module fetches the affected files and the judging reports into a cache
OUTSIDE the repo (third-party source is never stored here), runs the checks the rules fix in
advance, and writes `labels.toml`: for each accepted issue the labelled functions and the file's
SHA-256. No model is involved in any of it.

The checks (rule 6): a code block the report quotes must be found at the linked lines of the
fetched file (or nowhere in it, which leaves the position unverified), and some function of the
file must contain or overlap the linked lines. An issue that fails is replaced by the next reserve
issue of its side, in rank order.
"""

import hashlib
import re
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from evals.source_review import plain
from evals.source_review.cards import DATASET_DIR
from evals.source_review.compare import Source
from evals.source_review.hostile import make_bundle
from review.lexer import Lexed, lex

ITEMS_PATH = DATASET_DIR.parent / "audit_items" / "items.toml"
LABELS_PATH = DATASET_DIR.parent / "audit_items" / "labels.toml"
SIDES = ("dev", "held_out")
PER_SIDE = 30
RAW = "https://raw.githubusercontent.com/sherlock-audit/{slug}/{ref}/{path}"
MAX_BYTES = 3_000_000
WINDOW = 5  # lines either side of a linked range in which quoted code may sit
NEED = 0.7  # share of a quote's code lines that must be found there

Opener = Callable[..., object]


@dataclass(frozen=True)
class Contest:
    slug: str
    commit: str
    split: str


@dataclass(frozen=True)
class AuditIssue:
    id: str  # `<contest>:<H|M>-<n>`
    contest: str
    severity: str
    found_by: int | None
    title: str
    path: str
    ranges: tuple[tuple[int, int], ...]
    bytes: int
    split: str
    rank: int

    @property
    def number(self) -> str:
        return self.id.split(":", 1)[1]


@dataclass(frozen=True)
class ItemsFile:
    contests: dict[str, Contest]
    issues: tuple[AuditIssue, ...]

    def side(self, split: str) -> list[AuditIssue]:
        return sorted((i for i in self.issues if i.split == split), key=lambda i: i.rank)


def load_items(path: Path = ITEMS_PATH) -> ItemsFile:
    data = tomllib.loads(path.read_text())
    contests = {c["slug"]: Contest(c["slug"], c["commit"], c["split"]) for c in data["contest"]}
    issues = tuple(
        AuditIssue(
            id=i["id"],
            contest=i["contest"],
            severity=i["severity"],
            found_by=i.get("found_by"),
            title=i["title"],
            path=i["path"],
            ranges=tuple((int(a), int(b)) for a, b in i["ranges"]),
            bytes=i["bytes"],
            split=i["split"],
            rank=i["rank"],
        )
        for i in data["issue"]
    )
    return ItemsFile(contests, issues)


# ---- fetching (the cache lives outside the repo) --------------------------------------------


def file_url(contest: Contest, issue: AuditIssue) -> str:
    return RAW.format(slug=contest.slug, ref=contest.commit, path=urllib.parse.quote(issue.path))


def report_url(contest: Contest) -> str:
    return RAW.format(slug=f"{contest.slug}-judging", ref="main", path="README.md")


def fetch_text(url: str, opener: Opener = urllib.request.urlopen) -> str:
    """A read-only GET of a public text file. Raises FileNotFoundError on a 404."""
    request = urllib.request.Request(url, headers={"User-Agent": "contract-analyzer-evals"})
    try:
        with opener(request, timeout=30) as response:  # type: ignore[operator]
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise FileNotFoundError(url) from None
        raise
    if len(body) > MAX_BYTES:
        raise ValueError(f"{url} is over {MAX_BYTES} bytes")
    return body.decode("utf-8", errors="replace")


def cached(cache: Path, kind: str, name: str, url: str, opener: Opener) -> str:
    target = cache / kind / name
    if target.exists():
        return target.read_text()
    text = fetch_text(url, opener)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return text


# ---- the report's quoted code ---------------------------------------------------------------

_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)\n```", re.S)
MIN_LEN = 8  # a code line shorter than this ("}", "{") says nothing about where it sits


def issue_section(report: str, issue_number: str) -> str:
    """The text of one issue (`H-1`) in a judging README: up to the next issue."""
    marker = re.search(rf"^# Issue {re.escape(issue_number)}: ", report, flags=re.M)
    if not marker:
        return ""
    rest = report[marker.end() :]
    nxt = re.search(r"^# Issue [A-Z]-\d+: ", rest, flags=re.M)
    return rest[: nxt.start()] if nxt else rest


def normal(line: str) -> str:
    """A code line without its trailing comment or any whitespace; '' for a comment-only line."""
    return re.sub(r"\s+", "", re.sub(r"//.*$", "", line))


def blocks(section: str) -> list[list[str]]:
    """The code blocks of an issue as lists of distinctive normalised lines (two or more each)."""
    found = (
        [n for n in (normal(x) for x in m.group(1).split("\n")) if len(n) >= MIN_LEN]
        for m in _FENCE.finditer(section)
    )
    return [b for b in found if len(b) >= 2]


def verify_position(issue: AuditIssue, text: str, report: str) -> str:
    """'ok': a code block of the issue sits at its linked lines. 'quote not found': a block quotes
    this file but not at those lines (the file moved on). 'unverified position': no block quotes
    the file (none, or only a proof of concept), so nothing can be checked."""
    lines = text.split("\n")
    anywhere = {normal(x) for x in lines}
    near: set[str] = set()
    for lo, hi in issue.ranges:
        near |= {normal(x) for x in lines[max(0, lo - 1 - WINDOW) : hi + WINDOW]}
    near_hit = file_hit = False
    for block in blocks(issue_section(report, issue.number)):
        near_hit |= sum(x in near for x in block) / len(block) >= NEED
        file_hit |= sum(x in anywhere for x in block) / len(block) >= NEED
    if near_hit:
        return "ok"
    return "quote not found" if file_hit else "unverified position"


# ---- labels ---------------------------------------------------------------------------------


def labelled_functions(text: str, ranges: Sequence[tuple[int, int]]) -> list[tuple[str, str]]:
    """(contract, function) for each linked range: the innermost function that holds it, else every
    function that overlaps it. Empty when no function does."""
    lexed: Lexed = lex("F1", text)
    members = plain._members(lexed)
    out: list[tuple[str, str]] = []
    for lo, hi in ranges:
        holding = [(c, m) for c, m in members if m.start_line <= lo and hi <= m.end_line]
        if holding:
            chosen = [min(holding, key=lambda cm: cm[1].end_line - cm[1].start_line)]
        else:
            chosen = [(c, m) for c, m in members if m.start_line <= hi and lo <= m.end_line]
        for contract, member in chosen:
            if (contract, member.name) not in out:
                out.append((contract, member.name))  # type: ignore[arg-type]
    return out


@dataclass(frozen=True)
class Checked:
    issue: AuditIssue
    status: str  # ok | unverified position | quote not found | no function | file missing
    functions: tuple[tuple[str, str], ...] = ()
    sha256: str = ""

    @property
    def accepted(self) -> bool:
        return self.status in ("ok", "unverified position")


def check_issue(
    issue: AuditIssue, contest: Contest, cache: Path, opener: Opener = urllib.request.urlopen
) -> Checked:
    try:
        text = cached(
            cache, "files", f"{contest.slug}/{issue.path}", file_url(contest, issue), opener
        )
    except FileNotFoundError:
        return Checked(issue, "file missing")
    report = cached(cache, "reports", f"{contest.slug}.md", report_url(contest), opener)
    status = verify_position(issue, text, report)
    if status == "quote not found":
        return Checked(issue, status)
    functions = labelled_functions(text, issue.ranges)
    if not functions:
        return Checked(issue, "no function")
    return Checked(issue, status, tuple(functions), hashlib.sha256(text.encode()).hexdigest())


def gather(
    items: ItemsFile,
    cache: Path,
    opener: Opener = urllib.request.urlopen,
    out: Callable[[str], None] = print,
) -> list[Checked]:
    """Walk each side in rank order until it holds PER_SIDE accepted issues. Every issue looked at,
    accepted or not, is returned in the order it was looked at."""
    results: list[Checked] = []
    for side in SIDES:
        taken = 0
        for issue in items.side(side):
            if taken >= PER_SIDE:
                break
            checked = check_issue(issue, items.contests[issue.contest], cache, opener)
            results.append(checked)
            taken += checked.accepted
            if not checked.accepted:
                out(f"  replaced {issue.id} ({side}, rank {issue.rank}): {checked.status}")
        if taken < PER_SIDE:
            out(f"  {side}: only {taken} of {PER_SIDE} issues could be accepted")
    return results


def write_labels(results: Sequence[Checked], path: Path = LABELS_PATH) -> None:
    q = lambda s: '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'  # noqa: E731
    lines = [
        "# Labels for the accepted audit issues, from the parser after the fetch (rule 6 and 7).",
        "# Draft: every label is unreviewed. `status` is the outcome of the pre-registered check.",
        "",
    ]
    for r in results:
        i = r.issue
        funcs = ", ".join(f"[{q(c)}, {q(f)}]" for c, f in r.functions)
        lines += [
            "[[issue]]",
            f"id = {q(i.id)}",
            f"split = {q(i.split)}",
            f"rank = {i.rank}",
            f"status = {q(r.status)}",
            f"functions = [{funcs}]",
            f"file_sha256 = {q(r.sha256)}",
            "",
        ]
    path.write_text("\n".join(lines))


# ---- files as the unit of a run -------------------------------------------------------------


@dataclass(frozen=True)
class FileItem:
    """One run: a file of a contest, with every accepted issue that points into it."""

    id: str  # `<contest>:<path>`
    group: str  # `audit_dev` or `audit_held_out`
    contest: str
    path: str
    commit: str
    sha256: str = ""  # of the file the labels were made from
    issues: tuple[AuditIssue, ...] = field(default=())
    labels: dict[str, frozenset[tuple[str, str]]] = field(default_factory=dict)  # issue id -> keys

    @property
    def keys(self) -> frozenset[tuple[str, str]]:
        return frozenset().union(*self.labels.values()) if self.labels else frozenset()


def load_labels(
    path: Path = LABELS_PATH,
) -> dict[str, tuple[str, tuple[tuple[str, str], ...], str]]:
    """issue id -> (status, functions, file SHA-256)."""
    data = tomllib.loads(path.read_text())
    return {
        i["id"]: (i["status"], tuple((c, f) for c, f in i["functions"]), i["file_sha256"])
        for i in data["issue"]
    }


def file_items(items: ItemsFile, labels: dict, split: str | None = None) -> list[FileItem]:
    """The files to run, in rank order of their first accepted issue, for one side or both."""
    by_file: dict[tuple[str, str], list[AuditIssue]] = {}
    for side in SIDES if split is None else (split,):
        for issue in items.side(side):
            status, funcs, _ = labels.get(issue.id, ("", (), ""))
            if status in ("ok", "unverified position") and funcs:
                by_file.setdefault((issue.contest, issue.path), []).append(issue)
    out = []
    for (slug, path), issues in by_file.items():
        out.append(
            FileItem(
                id=f"{slug}:{path}",
                group=f"audit_{issues[0].split}",
                contest=slug,
                path=path,
                commit=items.contests[slug].commit,
                sha256=labels[issues[0].id][2],
                issues=tuple(issues),
                labels={i.id: frozenset(labels[i.id][1]) for i in issues},
            )
        )
    return out


def load_sources(
    files: Sequence[FileItem],
    items: ItemsFile,
    cache: Path,
    opener: Opener = urllib.request.urlopen,
) -> dict[str, Source]:
    """The bundle both arms read: the affected file alone, under its real path. The cache is read
    (and filled from the pinned commit when a file is missing)."""
    out: dict[str, Source] = {}
    for f in files:
        contest = items.contests[f.contest]
        url = RAW.format(slug=f.contest, ref=contest.commit, path=urllib.parse.quote(f.path))
        text = cached(cache, "files", f"{f.contest}/{f.path}", url, opener)
        if f.sha256 and hashlib.sha256(text.encode()).hexdigest() != f.sha256:
            raise ValueError(f"{f.id}: the cached file is not the one the labels were made from")
        out[f.id] = Source(make_bundle([(f.path, text)]), None)
    return out
