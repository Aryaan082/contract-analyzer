"""The two models retrieval uses, behind small interfaces so any can be swapped.

An *embedder* turns a text into a list of numbers that stands for its meaning; texts with similar
meaning get lists that point the same way. A *reranker* reads a question and a chunk together and
scores how well the chunk answers it. Both run on the laptop through fastembed (ONNX Runtime, no
PyTorch). To use a different model, change a name in models.toml; to use one fastembed does not
have, write a class with the same methods as `Embedder` or `Reranker` and pass it in.

Nothing here downloads a model unless asked: `python -m retrieval models` is the only caller that
passes `allow_download=True`. Everywhere else a missing model raises `ModelMissing`.
"""

import http.client
import json
import os
import queue
import re
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Literal, Protocol

import numpy as np
from dotenv import load_dotenv
from pydantic import BaseModel

from retrieval.paths import MODELS_CONFIG, env_files, model_dir


class ModelMissing(Exception):
    """A model cannot be used (not downloaded, no API key, service refused). The message says
    what to do and never contains a key."""


class Embedder(Protocol):
    name: str  # identifies the vectors it made, so a different model never reuses them

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        """One row per text, each scaled to length 1."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """One vector of length 1."""
        ...


class Reranker(Protocol):
    name: str

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """A relevance score per passage, in the same order (higher is more relevant)."""
        ...


class EmbedderSpec(BaseModel, frozen=True):
    name: str
    query_prefix: str = ""
    provider: Literal["fastembed", "openrouter"] = "fastembed"


class RerankerSpec(BaseModel, frozen=True):
    name: str


class ModelsConfig(BaseModel, frozen=True):
    default_embedder: str
    default_reranker: str
    embedder: dict[str, EmbedderSpec]
    reranker: dict[str, RerankerSpec]


def load_models_config() -> ModelsConfig:
    return ModelsConfig.model_validate(tomllib.loads(MODELS_CONFIG.read_text()))


def normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def _missing(name: str, err: Exception) -> ModelMissing:
    return ModelMissing(
        f"model {name!r} is not on this machine ({type(err).__name__}). "
        "Download it with: python -m retrieval models"
    )


class FastEmbedEmbedder:
    def __init__(self, spec: EmbedderSpec, *, allow_download: bool = False) -> None:
        self.name = spec.name
        self._prefix = spec.query_prefix
        self._allow_download = allow_download
        self._model = None
        self._counter = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding  # imported here: it brings in onnxruntime

            try:
                self._model = TextEmbedding(
                    self.name,
                    cache_dir=str(model_dir()),
                    local_files_only=not self._allow_download,
                )
            except Exception as err:  # fastembed raises ValueError, OSError, HF errors
                raise _missing(self.name, err) from err
        return self._model

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        return normalize(np.array(list(self._load().embed(list(texts)))))

    def embed_query(self, text: str) -> np.ndarray:
        return normalize(np.array(list(self._load().embed([self._prefix + text]))))[0]

    def token_count(self, text: str) -> int:
        """How many tokens this text is, before the model cuts it at its maximum length.
        (fastembed's own `token_count` was found to spin for minutes, so this uses the model's
        tokenizer directly, on a copy with the cut switched off.)"""
        if self._counter is None:
            from tokenizers import Tokenizer

            self._counter = Tokenizer.from_str(self._load().model.tokenizer.to_str())
            self._counter.no_truncation()
            self._counter.no_padding()
        return len(self._counter.encode(text).ids)


class FastEmbedReranker:
    def __init__(self, spec: RerankerSpec, *, allow_download: bool = False) -> None:
        self.name = spec.name
        self._allow_download = allow_download
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            try:
                self._model = TextCrossEncoder(
                    self.name,
                    cache_dir=str(model_dir()),
                    local_files_only=not self._allow_download,
                )
            except Exception as err:
                raise _missing(self.name, err) from err
        return self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [float(s) for s in self._load().rerank(query, list(passages))]


OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"
KEY_VARIABLE = "OPENROUTER_API_KEY"
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})


def _within(seconds: float, call: Callable):
    """Run `call()` and give up after `seconds` of wall-clock time. urllib's own timeout restarts
    whenever a byte arrives, and OpenRouter sends keep-alive bytes while a provider stalls, so a
    request can otherwise hang for ever. The abandoned call finishes (or not) on a daemon thread."""
    done: queue.Queue = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            done.put((True, call()))
        except BaseException as err:  # handed to the caller, which decides what it means
            done.put((False, err))

    threading.Thread(target=run, daemon=True).start()
    try:
        ok, value = done.get(timeout=seconds)
    except queue.Empty:
        raise TimeoutError(f"no complete reply within {seconds:g}s") from None
    if ok:
        return value
    raise value


def _safe(text: str, limit: int = 200) -> str:
    """Text from a server, made safe to print: no control characters, bounded."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()[:limit]


class OpenRouterEmbedder:
    """Hosted embedding models through OpenRouter's OpenAI-style `/embeddings` endpoint.

    The text sent is the chunk text (public audit text and hand-written entries) and the queries.
    The key is read from the environment or a `.env` file when the first request is made, goes
    only into the Authorization header, and is never put in a message. Costs are small (cents for
    this whole corpus) and are totalled in `spent`.
    """

    def __init__(
        self,
        spec: EmbedderSpec,
        *,
        api_key: str | None = None,
        url: str = OPENROUTER_URL,
        batch_size: int = 32,
        retries: int = 4,
        timeout: float = 30.0,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = f"openrouter:{spec.name}"
        self._model, self._prefix = spec.name, spec.query_prefix
        self._api_key, self._url = api_key, url
        self._batch_size, self._retries, self._timeout = batch_size, retries, timeout
        self._opener, self._sleep = opener, sleep
        self.spent = {"requests": 0, "tokens": 0, "cost": 0.0}

    def _key(self) -> str:
        if self._api_key:
            return self._api_key
        for path in env_files():
            load_dotenv(path)  # variables already set in the shell win
        key = os.environ.get(KEY_VARIABLE, "").strip()
        if not key:
            raise ModelMissing(
                f"{KEY_VARIABLE} is not set. Add it to .env (see .env.example) or export it."
            )
        return key

    def _describe(self, status: int, detail: str) -> str:
        reasons = {
            400: "the request was refused as invalid",
            401: f"OpenRouter rejected the key: check {KEY_VARIABLE}",
            402: "the OpenRouter account has no credits",
            404: f"model {self._model!r} does not exist or is not an embedding model",
        }
        return (
            f"OpenRouter error {status}: {reasons.get(status, 'request failed')}. {detail}".strip()
        )

    def _post(self, texts: list[str]) -> list[list[float]]:
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"model": self._model, "input": texts}).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": "application/json",
            },
        )
        for attempt in range(self._retries + 1):
            try:

                def fetch(request=request):
                    with self._opener(request, timeout=self._timeout) as response:
                        return json.load(response)

                payload = _within(self._timeout, fetch)
                break
            except urllib.error.HTTPError as err:
                try:
                    detail = _safe(json.loads(err.read()).get("error", {}).get("message", ""))
                except (ValueError, AttributeError, OSError):
                    detail = ""
                if err.code in _RETRY_STATUSES and attempt < self._retries:
                    self._sleep(2.0**attempt)
                    continue
                raise ModelMissing(self._describe(err.code, detail)) from None
            except (OSError, http.client.HTTPException) as err:
                # a dropped connection (RemoteDisconnected), a timeout, no route: all retried
                if attempt < self._retries:
                    self._sleep(2.0**attempt)
                    continue
                raise ModelMissing(f"could not reach OpenRouter ({type(err).__name__})") from None
        if not isinstance(payload, dict) or "error" in payload or "data" not in payload:
            message = (
                payload.get("error", {}).get("message", "") if isinstance(payload, dict) else ""
            )
            raise ModelMissing(f"OpenRouter returned no embeddings. {_safe(str(message))}".strip())
        rows = [d["embedding"] for d in sorted(payload["data"], key=lambda d: d["index"])]
        if len(rows) != len(texts):
            raise ModelMissing(f"OpenRouter returned {len(rows)} embeddings for {len(texts)} texts")
        usage = payload.get("usage") or {}
        self.spent["requests"] += 1
        self.spent["tokens"] += int(usage.get("total_tokens") or 0)
        self.spent["cost"] += float(usage.get("cost") or 0.0)
        return rows

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        rows: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            rows += self._post(list(texts[start : start + self._batch_size]))
        return normalize(np.array(rows))

    def embed_query(self, text: str) -> np.ndarray:
        return normalize(np.array(self._post([self._prefix + text])))[0]


def get_embedder(
    tier: str | None = None, *, allow_download: bool = False
) -> FastEmbedEmbedder | OpenRouterEmbedder:
    """A tier from models.toml ("small", "base", "qwen8b", ...), or a fastembed model name used as
    it is. Hosted tiers (provider = "openrouter") need OPENROUTER_API_KEY when first used."""
    config = load_models_config()
    tier = tier or config.default_embedder
    spec = config.embedder.get(tier) or EmbedderSpec(name=tier)
    if spec.provider == "openrouter":
        return OpenRouterEmbedder(spec)
    return FastEmbedEmbedder(spec, allow_download=allow_download)


def get_reranker(tier: str | None = None, *, allow_download: bool = False) -> FastEmbedReranker:
    config = load_models_config()
    tier = tier or config.default_reranker
    spec = config.reranker.get(tier) or RerankerSpec(name=tier)
    return FastEmbedReranker(spec, allow_download=allow_download)
