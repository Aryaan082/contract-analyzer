"""Runs the quiz against a variant of the search and scores it.

For each question the search is asked for its top 50 chunks (so the reciprocal rank is meaningful
beyond the first page); recall@5 looks at the first five. Time is measured around the search call,
after one warm-up call, so model loading is not counted; it does include embedding the query and
reranking, which are real costs.
"""

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from evals.metrics import Interval, bootstrap_ci, percentile, recall_at_k, reciprocal_rank
from evals.quiz import Question
from evals.variants import Variant
from retrieval.chunks import Chunk, build_chunks
from retrieval.embedders import Embedder, Reranker, get_embedder, get_reranker
from retrieval.filters import matches
from retrieval.index import ensure_index, keyword_index
from retrieval.search import Searcher

DEPTH = 50  # how many results are asked for; recall looks at the first five
K = 5


@dataclass(frozen=True)
class Scored:
    question: Question
    recall: float
    rr: float
    seconds: float


@dataclass(frozen=True)
class Run:
    variant: Variant
    split: str
    scored: tuple[Scored, ...]


@dataclass(frozen=True)
class Summary:
    n: int
    recall: Interval
    mrr: Interval
    p50_ms: float
    p95_ms: float


def summarize(scored: Sequence[Scored]) -> Summary:
    ms = [s.seconds * 1000 for s in scored]
    return Summary(
        len(scored),
        bootstrap_ci([s.recall for s in scored]),
        bootstrap_ci([s.rr for s in scored]),
        percentile(ms, 0.5),
        percentile(ms, 0.95),
    )


def breakdown(run: Run, by: Literal["style", "kind"]) -> dict[str, Summary]:
    groups: dict[str, list[Scored]] = {}
    for s in run.scored:
        groups.setdefault(getattr(s.question, by), []).append(s)
    return {name: summarize(group) for name, group in sorted(groups.items())}


class RetrievalRunner:
    """Builds and caches the models, indexes and searchers a set of variants needs. The factories
    can be replaced (the tests pass fakes); by default they are the real fastembed models."""

    def __init__(
        self,
        *,
        embedders: Callable[[str], Embedder] = get_embedder,
        rerankers: Callable[[str], Reranker] = get_reranker,
        chunks: Callable[[str], list[Chunk]] = build_chunks,
        index_dir: Path | None = None,
    ) -> None:
        self._embedders, self._rerankers, self._chunks = embedders, rerankers, chunks
        self._index_dir = index_dir
        self._embedder_cache: dict[str, Embedder] = {}
        self._reranker_cache: dict[str, Reranker] = {}
        self._searchers: dict[tuple, Searcher] = {}
        self.chunk_ids: dict[str, list[Chunk]] = {}

    def spend(self) -> dict[str, dict]:
        """What hosted embedders have cost so far, by model (empty if none were used)."""
        return {
            name: dict(embedder.spent)
            for name, embedder in self._embedder_cache.items()
            if hasattr(embedder, "spent")
        }

    def _chunks_for(self, recipe: str) -> list[Chunk]:
        if recipe not in self.chunk_ids:
            self.chunk_ids[recipe] = self._chunks(recipe)
        return self.chunk_ids[recipe]

    def searcher(self, variant: Variant) -> Searcher:
        key = (
            variant.embedder if variant.needs_embedder else None,
            variant.reranker if variant.needs_reranker else None,
            variant.tokenizer,
            variant.recipe,
            variant.rerank_n,
        )
        if key not in self._searchers:
            chunks = self._chunks_for(variant.recipe)
            embedder = reranker = None
            if variant.needs_embedder:
                embedder = self._embedder_cache.setdefault(
                    variant.embedder, self._embedders(variant.embedder)
                )
                index = ensure_index(
                    embedder, variant.recipe, chunks=chunks, index_dir=self._index_dir
                )
            else:
                index = keyword_index(variant.recipe, chunks=chunks)
            if variant.needs_reranker:
                reranker = self._reranker_cache.setdefault(
                    variant.reranker, self._rerankers(variant.reranker)
                )
            self._searchers[key] = Searcher(
                index,
                embedder=embedder,
                reranker=reranker,
                tokenizer=variant.tokenizer,
                rerank_n=variant.rerank_n,
            )
            if variant.mode != "random":  # warm-up: load the models before anything is timed
                self._searchers[key].search("warm-up", k=1, mode=variant.mode)  # type: ignore[arg-type]
        return self._searchers[key]

    def _ranked(self, variant: Variant, question: Question) -> tuple[list[str], float]:
        if variant.mode == "random":
            eligible = [
                c.id for c in self._chunks_for(variant.recipe) if matches(c, question.filters)
            ]
            random.Random(question.id).shuffle(eligible)
            return eligible, 0.0
        searcher = self.searcher(variant)
        started = time.perf_counter()
        result = searcher.search(
            question.text,
            k=DEPTH,
            filters=question.filters,
            mode=variant.mode,  # type: ignore[arg-type]
        )
        return [h.chunk.id for h in result.hits], time.perf_counter() - started

    def run(
        self, variant: Variant, items: Sequence[tuple[Question, Sequence[str]]], split: str
    ) -> Run:
        scored = []
        for question, relevant in items:
            ranked, seconds = self._ranked(variant, question)
            scored.append(
                Scored(
                    question,
                    recall_at_k(ranked, relevant, K),
                    reciprocal_rank(ranked, relevant),
                    seconds,
                )
            )
        return Run(variant, split, tuple(scored))
