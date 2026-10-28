"""Builders shared by the Part E tests: cards on disk, a folder layout for one test, a scripted
agent runner that writes traces the way a real run does, and things that must never be called."""

import json
from pathlib import Path
from types import SimpleNamespace

from evals import pipeline
from evals.generate import RunResult
from tests.evals_support import make_trace

EVAL_MODEL = "deepseek/deepseek-v4.1-flash-1017"
ADDRESSES = {"c1": "0x" + "11" * 20, "c2": "0x" + "22" * 20, "c3": "0x" + "33" * 20}
CARD = """
[[contract]]
id = "{id}"
address = "{address}"
block = 100
class = "{klass}"
split = "{split}"
source = "a test"
reason = "a test"
"""


def write_cards(path: Path, split: str = "dev") -> Path:
    classes = {"c1": "benign", "c2": "flagged", "c3": "grey"}
    path.write_text(
        "".join(
            CARD.format(id=i, address=a, klass=classes[i], split=split)
            for i, a in ADDRESSES.items()
        )
    )
    return path


def make_where(tmp_path):
    """Folders for one test: cards, run-sets, results, summaries and the judge cache."""
    dirs = SimpleNamespace(
        cards=write_cards(tmp_path / "cards.toml"),
        held=write_cards(tmp_path / "held.toml", split="heldout"),
        runsets=tmp_path / "runsets",
        results=tmp_path / "results",
        summaries=tmp_path / "summaries",
        cache=tmp_path / "cache",
    )

    def argv(command: str, *rest: str, cards: Path | None = None) -> list[str]:
        return [
            command,
            "--cards",
            str(cards or dirs.cards),
            "--runsets-dir",
            str(dirs.runsets),
            "--results-dir",
            str(dirs.results),
            "--summaries-dir",
            str(dirs.summaries),
            "--judge-cache",
            str(dirs.cache),
            "--resamples",
            "50",
            *rest,
        ]

    dirs.argv = argv
    return dirs


def scripted_runner(cost: float = 0.004, verdicts: dict[str, str] | None = None):
    """A make_runner: each run writes a trace the way a real run does, at `cost` dollars."""
    verdicts = verdicts or {"c1": "no_red_flags_found", "c2": "high_risk", "c3": "elevated_risk"}
    by_address = {a: i for i, a in ADDRESSES.items()}
    calls = []

    def make(traces: Path):
        def run(card, ceiling, tags):
            calls.append((card.id, ceiling, tags))
            trace = make_trace(
                address=card.address,
                block=card.block,
                model=EVAL_MODEL,
                verdict=verdicts[by_address[card.address]],
                cost=cost,
                tags=tags,
                tools=[
                    ("identify_contract", {"address": card.address}),
                    ("simulate_sell", {"address": card.address}),
                ],
            )
            path = traces / f"r{tags['repeat']}-{card.id}.json"
            path.write_text(json.dumps(trace))
            return RunResult(path, cost, accepted=True)

        return run

    make.calls = calls
    return make


def forbidden_runner(*_):
    raise AssertionError("an agent run was started")


def forbidden_judge(_spec):
    raise AssertionError("a judge was started")


def retrieval_spy():
    seen = []

    def run(ns, rows):
        seen.append(ns)
        rows.append(
            (
                "dense-small",
                "held-out" if ns.held_out else "dev",
                SimpleNamespace(n=87, recall="0.84 [0.76-0.92]", mrr="0.80 [0.72-0.88]"),
            )
        )
        return 0

    run.seen = seen
    return run


def generate_run_set(where, *rest, make=None, **kw):
    return pipeline.main(
        where.argv("generate", "--go", "--max-spend", "1", *rest),
        make_runner=make or scripted_runner(),
        make_judge=kw.get("make_judge", forbidden_judge),
    )
