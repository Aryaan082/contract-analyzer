"""Reading the judge's reply, strictly.

A reply is a label or it is not. There is no guessing: no hunting for a label word in prose, no
default, no repair of a half-finished object. Anything else is recorded with a code saying why it
was refused (`bad_reply`), and counted as unlabelled. The one tolerance is a single Markdown code
fence around an otherwise exact JSON object, which changes no word of the answer.
"""

import json
import re
from dataclasses import dataclass

from evals.judge.rubric import LABELS, MAX_STORED_REASON

_FENCE = re.compile(r"^```(?:json|JSON)?[ \t]*\n(.*)\n```$", re.DOTALL)
_BACKTICKED = re.compile(r"`([^`\n]{3,200})`")


@dataclass(frozen=True)
class Verdict:
    label: str
    reason: str


@dataclass(frozen=True)
class Parsed:
    verdicts: tuple[Verdict, ...] = ()  # one for a claim, one per sentence for a summary
    error: str | None = None  # a code from CODES when the reply was refused

    @property
    def ok(self) -> bool:
        return self.error is None


CODES = (
    "empty_reply",
    "not_json",
    "not_an_object",
    "wrong_keys",
    "bad_label",
    "bad_reason",
    "wrong_sentences",
    "cut_off",
)


def _object(text: str | None, finish_reason: str | None) -> tuple[dict | None, str | None]:
    if finish_reason == "length":
        return None, "cut_off"
    if text is None or not text.strip():
        return None, "empty_reply"
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        value = json.loads(body)
    except ValueError:
        return None, "not_json"
    if not isinstance(value, dict):
        return None, "not_an_object"
    return value, None


def _verdict(label: object, reason: object) -> tuple[Verdict | None, str | None]:
    if not isinstance(label, str) or label not in LABELS:
        return None, "bad_label"
    if not isinstance(reason, str) or not reason.strip():
        return None, "bad_reason"
    one_line = " ".join(reason.split())[:MAX_STORED_REASON]
    return Verdict(label, one_line), None


def parse_claim(text: str | None, finish_reason: str | None = None) -> Parsed:
    value, error = _object(text, finish_reason)
    if value is None:
        return Parsed(error=error)
    if set(value) != {"label", "reason"}:
        return Parsed(error="wrong_keys")
    verdict, error = _verdict(value["label"], value["reason"])
    return Parsed(error=error) if verdict is None else Parsed((verdict,))


def parse_summary(text: str | None, count: int, finish_reason: str | None = None) -> Parsed:
    """`count` is how many sentences were sent; the reply must answer each once, in order."""
    value, error = _object(text, finish_reason)
    if value is None:
        return Parsed(error=error)
    if set(value) != {"sentences"} or not isinstance(value["sentences"], list):
        return Parsed(error="wrong_keys")
    entries = value["sentences"]
    if len(entries) != count:
        return Parsed(error="wrong_sentences")
    verdicts = []
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != {"n", "label", "reason"}:
            return Parsed(error="wrong_keys")
        if entry["n"] != number or isinstance(entry["n"], bool):
            return Parsed(error="wrong_sentences")
        verdict, error = _verdict(entry["label"], entry["reason"])
        if verdict is None:
            return Parsed(error=error)
        verdicts.append(verdict)
    return Parsed(tuple(verdicts))


def quotes_found(reason: str, texts: list[str]) -> bool | None:
    """Whether every backticked quote in the reason really occurs in the claim or the evidence it
    was judged against (case and spacing ignored). None if the reason quotes nothing. A judge whose
    quotes are not in the evidence is making them up, whatever its label."""
    quotes = _BACKTICKED.findall(reason)
    if not quotes:
        return None
    haystack = _squash(" ".join(texts))
    return all(_squash(q) in haystack for q in quotes)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace('\\"', '"')).lower()
