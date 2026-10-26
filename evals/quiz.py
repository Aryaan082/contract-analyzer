"""The retrieval quiz: questions with answer keys, split into dev and held-out.

Files are in evals/datasets/ (see its README). The two label files record the split; dev is read
every run, held-out only at milestones.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from retrieval.filters import Filters

DATASETS = Path(__file__).resolve().parent / "datasets"
Split = Literal["dev", "heldout"]
STYLES = ("question", "tool_words", "code", "description")
KINDS = ("paraphrase", "identifier", "twin", "multi", "filtered", "paired", "snippet", "unseen")


class Question(BaseModel, frozen=True):
    id: str
    text: str
    style: Literal["question", "tool_words", "code", "description"]
    kind: Literal[
        "paraphrase", "identifier", "twin", "multi", "filtered", "paired", "snippet", "unseen"
    ]
    case: str | None = None
    filters: Filters | None = None
    source: str | None = None  # where a quoted fragment comes from: report, finding, figure, page


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_questions(directory: Path | None = None) -> list[Question]:
    return [
        Question.model_validate(r)
        for r in _rows((directory or DATASETS) / "retrieval_questions.jsonl")
    ]


def load_labels(split: Split, directory: Path | None = None) -> dict[str, tuple[str, ...]]:
    name = "heldout" if split == "heldout" else "dev"
    rows = _rows((directory or DATASETS) / f"retrieval_labels_{name}.jsonl")
    return {r["id"]: tuple(r["relevant"]) for r in rows}


def load_split(
    split: Split, directory: Path | None = None
) -> list[tuple[Question, tuple[str, ...]]]:
    """The questions of one split with their right answers, in file order."""
    labels = load_labels(split, directory)
    return [(q, labels[q.id]) for q in load_questions(directory) if q.id in labels]
