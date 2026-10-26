"""The saved side of the index: one vectors file per (embedder, recipe).

Chunks always come fresh from Component 3's loaders, which are the source of truth. What is saved
is only what is slow to make: the embedding of each chunk, next to a fingerprint of the chunk
texts and the model name. If a record changes, the fingerprint no longer matches, `load_index`
says the index is stale, and `make index` rebuilds it in one command.

The vectors are kept in memory and compared with every chunk (exact search). At tens to hundreds
of chunks that takes microseconds; a database index would only add setup.
"""

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from retrieval.chunks import Chunk, build_chunks, fingerprint
from retrieval.embedders import Embedder
from retrieval.paths import INDEX_DIR


class IndexUnavailable(Exception):
    """There is no usable index; the message says what to run."""


@dataclass(frozen=True)
class Index:
    recipe: str
    chunks: tuple[Chunk, ...]
    vectors: np.ndarray | None  # one row per chunk, length 1; None for a keyword-only index
    embedder: str | None = None
    build_seconds: float | None = None


def keyword_index(recipe: str = "default", *, chunks: Sequence[Chunk] | None = None) -> Index:
    """Chunks only, for the keyword search: needs no model."""
    return Index(recipe, tuple(chunks if chunks is not None else build_chunks(recipe)), None)


def _path(embedder: Embedder, recipe: str, index_dir: Path | None) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", embedder.name)
    return (index_dir or INDEX_DIR) / f"vectors-{slug}-{recipe}.npz"


def build_index(
    embedder: Embedder,
    recipe: str = "default",
    *,
    chunks: Sequence[Chunk] | None = None,
    index_dir: Path | None = None,
) -> Index:
    """Embed every chunk and save the vectors, replacing any earlier file."""
    chunks = tuple(chunks if chunks is not None else build_chunks(recipe))
    started = time.perf_counter()
    vectors = embedder.embed_passages([c.text for c in chunks])
    seconds = time.perf_counter() - started
    path = _path(embedder, recipe, index_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        vectors=vectors,
        ids=np.array([c.id for c in chunks]),
        fingerprint=np.array(fingerprint(chunks, embedder.name, recipe)),
    )
    return Index(recipe, chunks, vectors, embedder.name, seconds)


def load_index(
    embedder: Embedder,
    recipe: str = "default",
    *,
    chunks: Sequence[Chunk] | None = None,
    index_dir: Path | None = None,
) -> Index:
    """The saved vectors, if they still match the records; otherwise `IndexUnavailable`."""
    chunks = tuple(chunks if chunks is not None else build_chunks(recipe))
    path = _path(embedder, recipe, index_dir)
    if not path.exists():
        raise IndexUnavailable(f"no index for {embedder.name} / {recipe}. Run: make index")
    with np.load(path, allow_pickle=False) as saved:
        stored = str(saved["fingerprint"])
        vectors = saved["vectors"]
    if stored != fingerprint(chunks, embedder.name, recipe) or len(vectors) != len(chunks):
        raise IndexUnavailable(
            f"the index for {embedder.name} / {recipe} is out of date: the records changed. "
            "Run: make index"
        )
    return Index(recipe, chunks, vectors, embedder.name)


def ensure_index(
    embedder: Embedder,
    recipe: str = "default",
    *,
    chunks: Sequence[Chunk] | None = None,
    index_dir: Path | None = None,
) -> Index:
    """Load the saved vectors, or build them if missing or stale (used by the evals)."""
    try:
        return load_index(embedder, recipe, chunks=chunks, index_dir=index_dir)
    except IndexUnavailable:
        return build_index(embedder, recipe, chunks=chunks, index_dir=index_dir)
