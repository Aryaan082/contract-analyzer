"""The variants the quiz compares. Every one is a full configuration of the search, so a row in
results.md names exactly what was run. Defaults are untuned: BM25 k1 1.5 / b 0.75, fusion k 60.
"""

from typing import Literal

from pydantic import BaseModel

from retrieval.search import Mode


class Variant(BaseModel, frozen=True):
    name: str
    mode: Mode | Literal["random"]
    note: str
    embedder: str = "small"
    reranker: str = "small"
    tokenizer: Literal["identifier", "plain"] = "identifier"
    recipe: str = "default"
    rerank_n: int = 50

    @property
    def needs_embedder(self) -> bool:
        return self.mode in ("dense", "hybrid", "hybrid_rerank")

    @property
    def needs_reranker(self) -> bool:
        return self.mode == "hybrid_rerank"


def _v(name: str, mode: str, note: str, **options) -> Variant:
    return Variant(name=name, mode=mode, note=note, **options)  # type: ignore[arg-type]


VARIANTS: dict[str, Variant] = {
    v.name: v
    for v in [
        _v("random", "random", "ranks the eligible chunks in a random order (fixed seed)"),
        _v("bm25-plain", "bm25", "keyword search, plain words only", tokenizer="plain"),
        _v("bm25", "bm25", "keyword search, identifier-aware tokenizer"),
        _v("dense-small", "dense", "meaning search, bge-small"),
        _v("hybrid-small", "hybrid", "keyword + meaning (bge-small), reciprocal rank fusion"),
        _v("rerank-small", "hybrid_rerank", "hybrid, then MiniLM cross-encoder over the top 50"),
        _v("rerank-small-n10", "hybrid_rerank", "as rerank-small, top 10 only", rerank_n=10),
        _v("rerank-small-n20", "hybrid_rerank", "as rerank-small, top 20 only", rerank_n=20),
        _v("dense-base", "dense", "meaning search, bge-base", embedder="base"),
        _v("hybrid-base", "hybrid", "keyword + meaning (bge-base)", embedder="base"),
        _v(
            "rerank-bigreranker-n20",
            "hybrid_rerank",
            "hybrid-small, then bge-reranker-base over the top 20 (50 takes 15 s a query)",
            reranker="base",
            rerank_n=20,
        ),
        _v(
            "stack-base-n20",
            "hybrid_rerank",
            "bge-base + bge-reranker-base over the top 20",
            embedder="base",
            reranker="base",
            rerank_n=20,
        ),
        _v(
            "dense-qwen8b",
            "dense",
            "meaning search, Qwen3-Embedding-8B via OpenRouter",
            embedder="qwen8b",
        ),
        _v("hybrid-qwen8b", "hybrid", "keyword + Qwen3-Embedding-8B", embedder="qwen8b"),
        _v(
            "dense-openai-large",
            "dense",
            "meaning search, text-embedding-3-large via OpenRouter",
            embedder="openai-large",
        ),
        _v(
            "hybrid-openai-large",
            "hybrid",
            "keyword + text-embedding-3-large",
            embedder="openai-large",
        ),
        _v(
            "dense-gemini2",
            "dense",
            "meaning search, gemini-embedding-2 via OpenRouter",
            embedder="gemini2",
        ),
        _v("hybrid-gemini2", "hybrid", "keyword + gemini-embedding-2", embedder="gemini2"),
        _v(
            "recipe-examples",
            "hybrid",
            "hybrid-small, chunk text with the code examples",
            recipe="with_examples",
        ),
        _v(
            "recipe-short",
            "hybrid",
            "hybrid-small, chunk text title + summary + description only",
            recipe="short",
        ),
    ]
}

# `core` needs only the small models: it is what CI runs. `all` adds the big-vs-small and
# chunk-text comparisons, which are run by hand.
GROUPS: dict[str, list[str]] = {
    "core": ["random", "bm25-plain", "bm25", "dense-small", "hybrid-small", "rerank-small"],
    # Hosted embedders: need OPENROUTER_API_KEY and cost cents. Never run by CI.
    "hosted": [n for n in VARIANTS if n.endswith(("qwen8b", "openai-large", "gemini2"))],
    "all": list(VARIANTS),
}


def resolve(spec: str) -> list[Variant]:
    names = GROUPS.get(spec) or [n.strip() for n in spec.split(",") if n.strip()]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise ValueError(
            f"unknown variant(s) {unknown}; have {list(VARIANTS)} and groups {list(GROUPS)}"
        )
    return [VARIANTS[n] for n in names]


# Pairs compared head to head (second minus first, on the same questions) when both were run.
CHAIN: list[tuple[str, str]] = [
    ("bm25-plain", "bm25"),
    ("dense-small", "hybrid-small"),
    ("bm25", "hybrid-small"),
    ("hybrid-small", "rerank-small"),
    ("rerank-small", "rerank-small-n10"),
    ("rerank-small", "rerank-small-n20"),
    ("dense-small", "dense-base"),
    ("hybrid-small", "hybrid-base"),
    ("rerank-small-n20", "rerank-bigreranker-n20"),
    ("rerank-small-n20", "stack-base-n20"),
    ("hybrid-base", "stack-base-n20"),
    ("dense-small", "dense-qwen8b"),
    ("hybrid-small", "hybrid-qwen8b"),
    ("dense-small", "dense-openai-large"),
    ("hybrid-small", "hybrid-openai-large"),
    ("dense-small", "dense-gemini2"),
    ("hybrid-small", "hybrid-gemini2"),
    ("hybrid-small", "recipe-examples"),
    ("hybrid-small", "recipe-short"),
]
