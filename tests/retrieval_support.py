"""Test doubles for retrieval: no model, no download, no network.

HashingEmbedder puts each word in one of 64 buckets (a bag of words), so texts that share words
point the same way, which is all the tests need from "meaning". OverlapReranker counts shared
words. Both implement the same interfaces as the real fastembed models.
"""

import hashlib
import re
from collections.abc import Sequence

import numpy as np

from retrieval.chunks import Chunk, Citation
from retrieval.embedders import normalize


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


class HashingEmbedder:
    def __init__(self, name: str = "fake-hash-64", dim: int = 64) -> None:
        self.name, self._dim = name, dim

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self._dim, dtype=np.float32)
        for token in words(text):
            vector[int(hashlib.sha1(token.encode()).hexdigest()[:8], 16) % self._dim] += 1.0
        return vector

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        return normalize(np.array([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return normalize(self._vector(text))


class OverlapReranker:
    name = "fake-overlap"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        wanted = set(words(query))
        return [float(len(wanted & set(words(p)))) for p in passages]


def chunk(
    chunk_id: str,
    title: str,
    text: str,
    *,
    record_type: str = "taxonomy",
    **metadata,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        record_type=record_type,  # type: ignore[arg-type]
        title=title,
        text=text,
        citation=Citation(document="test", id=chunk_id),
        **metadata,
    )


def tiny_corpus() -> list[Chunk]:
    """Six chunks: three entries and three findings, with distinct vocabulary."""
    return [
        chunk(
            "taxonomy:tx-origin-auth",
            "Authorization with tx.origin",
            "The contract checks tx.origin instead of msg.sender so a phishing call can pass.",
            group="admin_power",
            applies_to=("any",),
        ),
        chunk(
            "taxonomy:privileged-mint",
            "Privileged minting",
            "An owner can create new tokens at will and dilute every other holder.",
            group="token_tricks",
            applies_to=("erc20", "erc721"),
        ),
        chunk(
            "taxonomy:erc4626-share-inflation",
            "ERC-4626 share price inflation",
            "The first depositor can donate assets so later depositors receive zero shares.",
            group="defi",
            applies_to=("erc4626",),
        ),
        chunk(
            "r1:F-1",
            "Anyone can drain the vault",
            "A permissionless function leaves the reserves out of date so anyone can drain funds.",
            record_type="finding",
            severity="high",
            protocol="Alpha",
            date="2024-12",
            report="r1",
        ),
        chunk(
            "r1:F-2",
            "Rounding loses precision",
            "Dividing before multiplying loses precision in the debt calculation.",
            record_type="finding",
            severity="medium",
            protocol="Alpha",
            date="2022-06",
            report="r1",
        ),
        chunk(
            "r2:F-1",
            "Owner has excessive privileges",
            "The owner can change every component, a single point of failure for the system.",
            record_type="finding",
            severity="high",
            protocol="Beta",
            date="2021-11",
            report="r2",
        ),
    ]
