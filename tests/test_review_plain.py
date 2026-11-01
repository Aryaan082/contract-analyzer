"""The plain reader moved to review/: the pre-registered request must be exactly what it was."""

import hashlib
import json

from evals.source_review import costs, scoring
from review import plain
from review.reviewer import contract_at, flags_of
from review.types import Flag


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_pre_registered_plain_request_is_byte_for_byte_what_it_was():
    # fingerprints taken before the move (2026-11-06); a change here changes arm P of every ToB run
    assert (
        sha(plain.PLAIN_INSTRUCTION)
        == "dc0fe1bd90abeffcd83d0b9e7986e28441b9d1d5f082fec3369ffbfde6946920"
    )
    assert (
        sha(plain.PLAIN_SYSTEM)
        == "13a7ca90ed739685da2c7ad9d20554776023cc5a8b91023b33ba52240f05e9c5"
    )
    schema = json.dumps(plain.PLAIN_SCHEMA, sort_keys=True)
    assert sha(schema) == "93375dfa6151cb1ccaa5aa20422d61d4f75f1804387e8144cf3185526f07224a"


def test_the_figure_that_sizes_a_request_still_matches_the_eval_cost_model():
    assert plain.CHARS_PER_TOKEN == costs.CHARS_PER_TOKEN


def test_flag_and_flags_of_are_one_definition_whichever_module_asks():
    assert (
        scoring.Flag is Flag and scoring.flags_of is flags_of and scoring.contract_at is contract_at
    )


def test_production_code_does_not_import_from_the_eval_folder():
    import ast
    from pathlib import Path

    for module in ("review/plain.py", "review/layered.py", "verify/stage.py"):
        tree = ast.parse(Path(module).read_text())
        names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [n for n in names if n.startswith("evals")], module
