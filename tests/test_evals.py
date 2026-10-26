"""The eval slice: metrics, the quiz files and the runner (fake models)."""

import json

import pytest

from evals import run as run_module
from evals.metrics import bootstrap_ci, paired_difference, recall_at_k, reciprocal_rank
from evals.quiz import KINDS, STYLES, load_labels, load_questions, load_split
from evals.retrieval_eval import RetrievalRunner, breakdown, summarize
from evals.variants import CHAIN, GROUPS, VARIANTS, resolve
from retrieval.chunks import build_chunks
from retrieval.filters import matches
from tests.retrieval_support import HashingEmbedder, OverlapReranker, tiny_corpus


def test_recall_divides_by_the_right_answers_up_to_k_and_rr_by_the_first_hit():
    assert recall_at_k(["a", "b", "c"], ["b", "z"], k=3) == 0.5
    assert recall_at_k(["a", "b"], ["a"], k=1) == 1.0
    assert recall_at_k(list("abcdefgh"), list("abcdefg"), k=5) == 1.0  # divisor capped at k
    assert reciprocal_rank(["x", "y", "b"], ["b"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], ["b"]) == 0.0
    with pytest.raises(ValueError):
        recall_at_k(["a"], [])


def test_the_interval_is_repeatable_and_contains_the_mean():
    values = [1.0] * 6 + [0.0] * 4
    first, second = bootstrap_ci(values), bootstrap_ci(values)
    assert first == second
    assert first.low <= first.mean == 0.6 <= first.high
    assert bootstrap_ci([1.0] * 5).low == 1.0


def test_a_fewer_questions_interval_is_wider():
    wide = bootstrap_ci([1.0, 0.0] * 5)
    narrow = bootstrap_ci([1.0, 0.0] * 50)
    assert wide.high - wide.low > narrow.high - narrow.low


def test_a_paired_difference_counts_wins_and_says_whether_it_is_shown():
    better = paired_difference([1.0] * 20, [0.0] * 20)
    assert (better.wins, better.losses, better.ties) == (20, 0, 0) and better.shown
    noise = paired_difference([1, 0, 1, 0, 1, 0], [0, 1, 0, 1, 0, 1])
    assert not noise.shown and noise.interval.mean == 0.0
    with pytest.raises(ValueError):
        paired_difference([1.0], [1.0, 0.0])


# --- the committed quiz ---------------------------------------------------------------------


def test_the_quiz_has_125_questions_split_without_overlap():
    questions = load_questions()
    dev, held = load_labels("dev"), load_labels("heldout")
    assert len(questions) == 125 and len({q.id for q in questions}) == 125
    assert set(dev) | set(held) == {q.id for q in questions}
    assert not set(dev) & set(held)
    assert (len(dev), len(held)) == (87, 38)


def test_every_right_answer_is_a_real_chunk_and_lies_inside_its_own_filter():
    chunks = {c.id: c for c in build_chunks()}
    for question in load_questions():
        labels = {**load_labels("dev"), **load_labels("heldout")}[question.id]
        assert labels, question.id
        for chunk_id in labels:
            assert chunk_id in chunks, (question.id, chunk_id)
            assert matches(chunks[chunk_id], question.filters), (question.id, chunk_id)


def test_every_chunk_is_a_right_answer_somewhere():
    answers = {
        c
        for labels in (load_labels("dev"), load_labels("heldout"))
        for r in labels.values()
        for c in r
    }
    assert answers == {c.id for c in build_chunks()}


def test_styles_and_kinds_are_known_and_a_case_is_never_split_across_dev_and_held_out():
    questions = load_questions()
    assert {q.style for q in questions} == set(STYLES) and {q.kind for q in questions} == set(KINDS)
    held = set(load_labels("heldout"))
    sides: dict[str, set[bool]] = {}
    for q in questions:
        if q.case:
            sides.setdefault(q.case, set()).add(q.id in held)
    assert all(len(side) == 1 for side in sides.values())


def test_every_kind_has_held_out_questions():
    held = set(load_labels("heldout"))
    assert {q.kind for q in load_questions() if q.id in held} == set(KINDS)


def test_quoted_fragments_name_their_source_and_are_labelled_by_what_they_show():
    labels = {**load_labels("dev"), **load_labels("heldout")}
    fragments = [q for q in load_questions() if q.kind in ("snippet", "unseen")]
    assert len(fragments) == 25
    for q in fragments:
        assert q.style == "code" and q.source and "[code listing omitted]" not in q.text, q.id
        ids = labels[q.id]
        if q.kind == "unseen":  # from a report that is not in the index: only a pattern can match
            assert all(i.startswith("taxonomy:") for i in ids), q.id
        else:  # from a finding that is in the index: that finding is always a right answer
            assert any(not i.startswith("taxonomy:") for i in ids), q.id
            assert q.source.startswith(ids[0].split(":")[0]), q.id


def test_a_paired_case_has_the_same_right_answers_in_every_style():
    labels = {**load_labels("dev"), **load_labels("heldout")}
    by_case: dict[str, set[tuple[str, ...]]] = {}
    for q in load_questions():
        if q.case:
            by_case.setdefault(q.case, set()).add(labels[q.id])
    assert all(len(v) == 1 for v in by_case.values())


# --- variants and the runner ----------------------------------------------------------------


def test_variants_and_groups_are_consistent():
    assert set(GROUPS["core"]) <= set(VARIANTS)
    assert all(a in VARIANTS and b in VARIANTS for a, b in CHAIN)
    assert [v.name for v in resolve("random,dense-small")] == ["random", "dense-small"]
    assert not any("bm25" in n or "hybrid" in n for n in VARIANTS)  # word scoring is gone
    with pytest.raises(ValueError, match="unknown variant"):
        resolve("nope")
    core_models = {(v.embedder, v.reranker) for v in resolve("core") if v.needs_reranker}
    assert core_models == {("small", "small")}  # CI needs only the small models


@pytest.fixture
def runner(tmp_path):
    corpus = tiny_corpus()
    return RetrievalRunner(
        embedders=lambda tier: HashingEmbedder(f"fake-{tier}"),
        rerankers=lambda tier: OverlapReranker(),
        chunks=lambda recipe: corpus,
        index_dir=tmp_path,
    )


def _items():
    from evals.quiz import Question

    def q(i, text, **kw):
        return Question(id=i, text=text, style="question", kind="paraphrase", **kw)

    return [
        (q("q1", "is tx.origin used"), ("taxonomy:tx-origin-auth",)),
        (q("q2", "create new tokens dilute"), ("taxonomy:privileged-mint",)),
        (q("q3", "nothing matches here at all"), ("r2:F-1",)),
    ]


def test_each_variant_runs_and_the_random_baseline_is_repeatable(runner):
    items = _items()
    scores = {}
    for name in ("random", "dense-small", "rerank-small"):
        run = runner.run(VARIANTS[name], items, "dev")
        scores[name] = [s.recall for s in run.scored]
        assert len(run.scored) == 3
    assert scores["dense-small"][1] == 1.0
    again = runner.run(VARIANTS["random"], items, "dev")
    assert [s.recall for s in again.scored] == scores["random"]


def test_summary_and_breakdown_report_counts_and_timing(runner):
    run = runner.run(VARIANTS["dense-small"], _items(), "dev")
    summary = summarize(run.scored)
    assert summary.n == 3 and summary.p95_ms >= summary.p50_ms >= 0
    assert set(breakdown(run, "kind")) == {"paraphrase"}


def test_variants_that_share_models_share_one_searcher(runner):
    dense = runner.searcher(VARIANTS["dense-small"])
    assert runner.searcher(VARIANTS["dense-small"]) is dense
    assert runner.searcher(VARIANTS["rerank-small"]) is not dense  # adds a reranker
    assert runner.searcher(VARIANTS["rerank-small"]) is not runner.searcher(
        VARIANTS["rerank-small-n10"]
    )
    assert runner.searcher(VARIANTS["dense-base"]) is not dense  # another embedder


def test_the_committed_quiz_loads_for_the_runner():
    assert len(load_split("dev")) == 87 and len(load_split("heldout")) == 38


def test_the_baseline_check_fails_only_when_confidently_worse(tmp_path, monkeypatch, runner):
    from evals.quiz import Question

    items = [
        (Question(id=f"q{i}", text="zzzz qqqq", style="question", kind="paraphrase"), ("nope:1",))
        for i in range(30)
    ]
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(run_module, "BASELINE", baseline)
    monkeypatch.setattr(run_module, "load_split", lambda split: items)
    run = runner.run(
        VARIANTS["dense-small"], items, "dev"
    )  # answer not in corpus: recall 0 everywhere
    now = {s.question.id: s.recall for s in run.scored}
    baseline.write_text(json.dumps({"variant": "dense-small", "recall": now}))
    assert run_module._check_baseline(runner, {"dense-small": run}) == 0
    baseline.write_text(json.dumps({"variant": "dense-small", "recall": {k: 1.0 for k in now}}))
    assert run_module._check_baseline(runner, {"dense-small": run}) == 1
    baseline.write_text(json.dumps({"variant": "dense-small", "recall": {"other": 1.0}}))
    assert run_module._check_baseline(runner, {"dense-small": run}) == 1
