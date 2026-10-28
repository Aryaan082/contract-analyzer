"""The rubric, its hash, the output schemas and the prompts built from them.

The words the judge is told are in `rubric.toml`. The hash identifies the rubric: it covers that
file's bytes and the two JSON schemas below, so a change to the instructions or to the shape of the
answer gives a new hash and, because the hash is in every cache key, new judgements.

Untrusted text (the claim, the evidence, the report's own sentences) reaches the judge only inside
fenced blocks. A block opens with `<<<BEGIN <name> <code>>>>` and closes with the line
`<<<END <name> <code>>>>`; the code is a hash of everything untrusted in the message, so text
inside a block cannot contain the closing line for its own block (that would need a SHA-256
fixed point), and the same message always gets the same code, which keeps the cache stable. Nothing
is ever put in the system message but the rubric.
"""

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

JUDGE_DIR = Path(__file__).resolve().parent
RUBRIC_PATH = JUDGE_DIR / "rubric.toml"
HASH_PATH = JUDGE_DIR / "rubric.sha256"

LABELS = ("supported", "partly", "not_supported", "contradicted")
BAD_LABELS = ("not_supported", "contradicted")  # what fails a report
MAX_REASON_CHARS = 300  # asked for; a longer reply is kept but cut to MAX_STORED_REASON
MAX_STORED_REASON = 600

CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "reason": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "label": {"type": "string", "enum": list(LABELS)},
                    "reason": {"type": "string"},
                },
                "required": ["n", "label", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}

EVIDENCE_CAP = (
    24_000  # characters of evidence given to the judge for one claim, all records together
)
_SAFE_ID = re.compile(r"[^A-Za-z0-9:_.\-]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class Rubric:
    version: int
    labels: tuple[str, ...]
    claim_system: str
    claim_user: str
    summary_system: str
    summary_user: str
    sha256: str


def rubric_hash(path: Path = RUBRIC_PATH) -> str:
    digest = hashlib.sha256(path.read_bytes())
    for schema in (CLAIM_SCHEMA, SUMMARY_SCHEMA):
        digest.update(b"\0" + json.dumps(schema, sort_keys=True).encode())
    return digest.hexdigest()


def load_rubric(path: Path = RUBRIC_PATH) -> Rubric:
    data = tomllib.loads(path.read_text())
    try:
        labels = tuple(data["labels"])
        if labels != LABELS:
            raise ValueError(f"rubric labels {labels} are not the labels the scorers count")
        return Rubric(
            version=data["version"],
            labels=labels,
            claim_system=data["claim"]["system"],
            claim_user=data["claim"]["user"],
            summary_system=data["summary"]["system"],
            summary_user=data["summary"]["user"],
            sha256=rubric_hash(path),
        )
    except KeyError as err:
        raise ValueError(f"rubric.toml is missing {err}") from None


def recorded_hash() -> str:
    return HASH_PATH.read_text().strip() if HASH_PATH.exists() else "none"


# ---- fenced untrusted text --------------------------------------------------


def clean_text(text: str) -> str:
    """Untrusted text with control characters (other than newline and tab) replaced."""
    return _CONTROL.sub("�", text)


def safe_id(value: str) -> str:
    """An evidence id for a header line outside any block: only characters that cannot make a
    line look like anything else."""
    return _SAFE_ID.sub("?", value)[:80]


def boundary_code(parts: list[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big") + part.encode())
    return digest.hexdigest()[:16]


def fence(name: str, code: str, text: str) -> str:
    return f"<<<BEGIN {name} {code}>>>\n{text}\n<<<END {name} {code}>>>"


# ---- the messages --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256((self.system + "\0" + self.user).encode()).hexdigest()

    @property
    def chars(self) -> int:
        return len(self.system) + len(self.user)

    def messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": self.system}, {"role": "user", "content": self.user}]


def claim_prompt(
    rubric: Rubric,
    *,
    claim: str,
    kind: str,
    severity: str,
    evidence: list[tuple[str, str, str]],
) -> Prompt:
    """The two messages for one claim. `evidence` is (id, source, text) for each record, text as
    the agent saw it and already cut to the cap. Not in it, on purpose: the verdict, the other
    claims, the summary and the agent's reasoning."""
    texts = [clean_text(claim), *(clean_text(t) for _, _, t in evidence)]
    code = boundary_code([kind, severity, *texts])
    blocks = []
    for number, ((evidence_id, source, _), text) in enumerate(
        zip(evidence, texts[1:], strict=True), start=1
    ):
        header = (
            f"Record {number} of {len(evidence)}: id {safe_id(evidence_id)} ({safe_id(source)})"
        )
        blocks.append(f"{header}\n{fence(f'evidence-{number}', code, text)}")
    user = rubric.claim_user.format(
        kind=safe_id(kind),
        severity=safe_id(severity),
        count=len(evidence),
        claim_block=fence("claim", code, texts[0]),
        evidence_blocks="\n\n".join(blocks) if blocks else "(none: the claim cites no record)",
    )
    return Prompt(rubric.claim_system, user)


def summary_prompt(
    rubric: Rubric,
    *,
    sentences: list[str],
    findings: list[str],
    checked: list[str],
    not_checked: list[str],
) -> Prompt:
    """The two messages for a summary: its numbered sentences and the report's own items."""
    groups = {
        "sentences": [clean_text(s) for s in sentences],
        "findings": [clean_text(s) for s in findings],
        "checked": [clean_text(s) for s in checked],
        "not_checked": [clean_text(s) for s in not_checked],
    }
    code = boundary_code([part for group in groups.values() for part in (str(len(group)), *group)])

    def block(name: str, items: list[str]) -> str:
        if not items:
            return "(none)"
        body = "\n".join(f"{number}. {item}" for number, item in enumerate(items, start=1))
        return fence(name, code, body)

    user = rubric.summary_user.format(
        sentence_block=block("sentences", groups["sentences"]),
        finding_block=block("findings", groups["findings"]),
        checked_block=block("checked", groups["checked"]),
        not_checked_block=block("not-checked", groups["not_checked"]),
    )
    return Prompt(rubric.summary_system, user)
