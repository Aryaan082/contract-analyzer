"""Planted variants: new contracts, each with one labelled issue, a safe twin and a renamed copy.

`evals/datasets/source_review/planted/P<nn>_<slug>/` holds, per variant:

    vulnerable.sol   new code that plants one issue from the 32 source-visible checklist entries
    safe.sol         the same code with that issue fixed (a few lines differ): the safe twin
    renamed.sol      `vulnerable.sol` with every identifier renamed, made by `renamed_text` below

The variants are ours, so no model has seen them and the labels are exact (the cards name contract,
function and entry). They were written after reading the checklist, so they are dev material only
(note 6.5). They are NOT the taxonomy entries' own `example_vulnerable` text, because that text is
in the reviewer's prompt: `copied_share` and `example_coverage` measure the overlap and a test
bounds both. They plant ordinary defensive-review shapes (a missing check, a call before an
update) and carry no exploit steps.

The renamed copy is the memorisation control of note 6.4: if recall falls on it, the reviewer was
remembering names. It is deterministic, so a test regenerates it and compares it with the file.
"""

import re
from pathlib import Path

from evals.source_review.cards import DATASET_DIR
from knowledge.taxonomy import load_taxonomy
from review.confirm import rename_identifiers
from review.lexer import lex

PLANTED_DIR = DATASET_DIR / "planted"
VARIANT_FILES = ("vulnerable.sol", "safe.sol", "renamed.sol")
_SPDX = re.compile(r"^// SPDX-License-Identifier: .*$")
_KEEP_LINE = re.compile(r"^\s*(pragma|import)\b")
_WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d[\d_.eE]*|[^\sA-Za-z0-9_$]")


def renamed_text(text: str) -> tuple[str, dict[str, str]]:
    """`text` with comments and strings handled as the reviewer's view handles them and every
    identifier that is not the language's own renamed `ID1`, `ID2`, ... in order of first use.
    Line numbers do not move. Returns the new text and the table (old name to new name)."""
    result = lex("F1", text)
    if result.view is None:
        raise ValueError("the text cannot be normalised, so it cannot be renamed")
    table: dict[str, str] = {}
    lines = []
    for number, line in enumerate(result.view.text.split("\n")):
        if _KEEP_LINE.match(line):
            lines.append(line)
        else:
            lines.append(rename_identifiers(line, table))
        if number == 0 and not line.strip():
            first = text.split("\n", 1)[0]
            if _SPDX.match(first):  # the licence line is a comment the view blanked
                lines[0] = first
    return "\n".join(lines).rstrip("\n") + "\n", table


def variant_dirs(root: Path = PLANTED_DIR) -> list[Path]:
    return sorted(d for d in root.iterdir() if d.is_dir() and re.match(r"^P\d\d_", d.name))


def write_renamed(root: Path = PLANTED_DIR) -> list[Path]:
    """Write `renamed.sol` beside every `vulnerable.sol`. Run it after changing a variant."""
    written = []
    for folder in variant_dirs(root):
        text, _ = renamed_text((folder / "vulnerable.sol").read_text())
        target = folder / "renamed.sol"
        target.write_text(text)
        written.append(target)
    return written


def stale_renamed(root: Path = PLANTED_DIR) -> list[str]:
    """Variants whose `renamed.sol` is missing or is not what `renamed_text` makes today."""
    out = []
    for folder in variant_dirs(root):
        target = folder / "renamed.sol"
        expected, _ = renamed_text((folder / "vulnerable.sol").read_text())
        if not target.exists() or target.read_text() != expected:
            out.append(folder.name)
    return out


def renamed_label(table: dict[str, str], contract: str, function: str) -> tuple[str, str]:
    """The label of the renamed copy: the original names through the table."""
    return table.get(contract, contract), table.get(function, function)


# ---- "new code, not the taxonomy's own text" ----------------------------------------------


def _tokens(text: str) -> list[str]:
    stripped = lex("F1", text).view
    source = stripped.text if stripped else text
    return _WORD.findall(source)


def shingles(text: str, size: int = 8) -> set[tuple[str, ...]]:
    tokens = _tokens(text)
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def taxonomy_example_texts() -> list[tuple[str, str]]:
    """(entry slug, text) for every `example_vulnerable` and `example_safe` in the taxonomy."""
    out = []
    for slug, entry in load_taxonomy().items():
        for name in ("example_vulnerable", "example_safe"):
            text = getattr(entry, name, None)
            if text:
                out.append((slug, text))
    return out


def copied_share(text: str, size: int = 8) -> float:
    """The share of `text`'s token shingles that also occur in any taxonomy example. Zero means no
    run of `size` tokens was lifted from the text that is in the reviewer's own prompt."""
    mine = shingles(text, size)
    if not mine:
        return 0.0
    theirs: set[tuple[str, ...]] = set()
    for _, example in taxonomy_example_texts():
        theirs |= shingles(example, size)
    return len(mine & theirs) / len(mine)


def example_coverage(text: str, size: int = 8) -> float:
    """The largest share of any single taxonomy example whose token shingles occur in `text`. An
    example copied whole scores 1.0; sharing an idiom such as `require(tx.origin == owner` scores
    a fraction."""
    mine = shingles(text, size)
    best = 0.0
    for _, example in taxonomy_example_texts():
        theirs = shingles(example, size)
        if theirs:
            best = max(best, len(theirs & mine) / len(theirs))
    return best
