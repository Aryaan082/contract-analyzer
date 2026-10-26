"""The variants the quiz compares. Every one is a full configuration of the search, so a row in
results.md names exactly what was run. (Keyword and hybrid variants existed until 2026-10-26; see
the last decision note in results.md.)
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
    recipe: str = "default"
    rerank_n: int = 50

    @property
    def needs_embedder(self) -> bool:
        return self.mode != "random"

    @property
    def needs_reranker(self) -> bool:
        return self.mode == "dense_rerank"


def _v(name: str, mode: str, note: str, **options) -> Variant:
    return Variant(name=name, mode=mode, note=note, **options)  # type: ignore[arg-type]


VARIANTS: dict[str, Variant] = {
    v.name: v
    for v in [
        _v("random", "random", "ranks the eligible chunks in a random order (fixed seed)"),
        _v("dense-small", "dense", "meaning search, bge-small"),
        _v(
            "rerank-small", "dense_rerank", "dense-small, then MiniLM cross-encoder over the top 50"
        ),
        _v("rerank-small-n10", "dense_rerank", "as rerank-small, top 10 only", rerank_n=10),
        _v("rerank-small-n20", "dense_rerank", "as rerank-small, top 20 only", rerank_n=20),
        _v("dense-base", "dense", "meaning search, bge-base", embedder="base"),
        _v(
            "rerank-bigreranker-n20",
            "dense_rerank",
            "dense-small, then bge-reranker-base over the top 20 (50 takes 15 s a query)",
            reranker="base",
            rerank_n=20,
        ),
        _v(
            "stack-base-n20",
            "dense_rerank",
            "bge-base + bge-reranker-base over the top 20",
            embedder="base",
            reranker="base",
            rerank_n=20,
        ),
        _v(
            "dense-qwen8b",
            "dense",
            "meaning search, Qwen3-Embedding-8B via OpenRouter (the product default)",
            embedder="qwen8b",
        ),
        _v(
            "dense-openai-large",
            "dense",
            "meaning search, text-embedding-3-large via OpenRouter",
            embedder="openai-large",
        ),
        _v(
            "dense-gemini2",
            "dense",
            "meaning search, gemini-embedding-2 via OpenRouter",
            embedder="gemini2",
        ),
        _v(
            "recipe-examples",
            "dense",
            "dense-small, chunk text with the code examples",
            recipe="with_examples",
        ),
        _v(
            "recipe-short",
            "dense",
            "dense-small, chunk text title + summary + description only",
            recipe="short",
        ),
    ]
}

# `core` needs only the small models: it is what CI runs. `all` adds the big-vs-small and
# chunk-text comparisons, which are run by hand.
GROUPS: dict[str, list[str]] = {
    "core": ["random", "dense-small", "rerank-small"],
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
    ("dense-small", "rerank-small"),
    ("rerank-small", "rerank-small-n10"),
    ("rerank-small", "rerank-small-n20"),
    ("dense-small", "dense-base"),
    ("rerank-small-n20", "rerank-bigreranker-n20"),
    ("rerank-small-n20", "stack-base-n20"),
    ("dense-base", "stack-base-n20"),
    ("dense-small", "dense-qwen8b"),
    ("dense-small", "dense-openai-large"),
    ("dense-small", "dense-gemini2"),
    ("dense-small", "recipe-examples"),
    ("dense-small", "recipe-short"),
]
