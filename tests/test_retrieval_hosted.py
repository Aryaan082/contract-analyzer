"""The OpenRouter embedder, against a fake HTTP layer: no key, no network."""

import http.client
import io
import json
import threading
import time
import urllib.error

import numpy as np
import pytest

from retrieval import embedders as embedders_module
from retrieval import paths
from retrieval.embedders import (
    KEY_VARIABLE,
    EmbedderSpec,
    ModelMissing,
    OpenRouterEmbedder,
    get_embedder,
    load_models_config,
)

SECRET = "sk-or-v1-this-must-never-appear-in-a-message"


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self, *args) -> bytes:
        return self._body


class FakeServer:
    """Plays OpenRouter: records requests, answers from a script of responses or errors."""

    def __init__(self, script=None, dim: int = 4) -> None:
        self.requests: list = []
        self.script = list(script or [])
        self.dim = dim

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        texts = json.loads(request.data)["input"]
        data = [
            {"object": "embedding", "index": i, "embedding": [float(len(t)), 1.0, 0.0, float(i)]}
            for i, t in enumerate(texts)
        ][::-1]  # a server may answer in any order; `index` says which is which
        return FakeResponse({"data": data, "usage": {"total_tokens": 10, "cost": 0.0001}})


def http_error(code: int, message: str = "") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))  # type: ignore[arg-type]


def embedder(server, **kw) -> OpenRouterEmbedder:
    spec = EmbedderSpec(name="vendor/model-x", query_prefix="Q: ", provider="openrouter")
    return OpenRouterEmbedder(spec, api_key=SECRET, opener=server, sleep=lambda s: None, **kw)


def test_a_request_goes_to_the_embeddings_endpoint_with_a_bearer_key_and_the_texts():
    server = FakeServer()
    embedder(server).embed_passages(["alpha", "beta"])
    request = server.requests[0]
    assert request.full_url == "https://openrouter.ai/api/v1/embeddings"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {SECRET}"
    assert json.loads(request.data) == {"model": "vendor/model-x", "input": ["alpha", "beta"]}


def test_rows_come_back_in_input_order_even_if_the_server_shuffles_them():
    vectors = embedder(FakeServer()).embed_passages(["a", "bbb", "cc"])
    assert vectors.shape == (3, 4)
    third = [float(len("cc")), 1.0, 0.0, 2.0]
    assert np.allclose(vectors[2], np.array(third) / np.linalg.norm(third))
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_texts_are_sent_in_batches():
    server = FakeServer()
    vectors = embedder(server, batch_size=2).embed_passages(list("abcde"))
    assert len(server.requests) == 3 and len(vectors) == 5
    assert [len(json.loads(r.data)["input"]) for r in server.requests] == [2, 2, 1]


def test_only_queries_get_the_instruction_prefix():
    server = FakeServer()
    e = embedder(server)
    e.embed_query("how?")
    e.embed_passages(["how?"])
    assert json.loads(server.requests[0].data)["input"] == ["Q: how?"]
    assert json.loads(server.requests[1].data)["input"] == ["how?"]


def test_cost_and_tokens_are_totalled():
    e = embedder(FakeServer(), batch_size=1)
    e.embed_passages(["a", "b", "c"])
    assert e.spent["requests"] == 3 and e.spent["tokens"] == 30
    assert e.spent["cost"] == pytest.approx(0.0003)


def test_a_busy_server_is_retried_and_then_succeeds():
    server = FakeServer([http_error(429), http_error(503)])
    assert len(embedder(server).embed_passages(["a"])) == 1
    assert len(server.requests) == 3


def test_giving_up_after_the_retries_names_the_problem_and_never_the_key():
    server = FakeServer([http_error(429, "slow down")] * 10)
    with pytest.raises(ModelMissing) as err:
        embedder(server, retries=2).embed_passages(["a"])
    assert "429" in str(err.value) and SECRET not in str(err.value)
    assert len(server.requests) == 3


@pytest.mark.parametrize(
    ("code", "fragment"),
    [(401, KEY_VARIABLE), (402, "no credits"), (404, "not an embedding model")],
)
def test_these_errors_are_not_retried_and_say_what_to_do(code, fragment):
    server = FakeServer([http_error(code, "detail\x1b[31m text")])
    with pytest.raises(ModelMissing) as err:
        embedder(server).embed_passages(["a"])
    assert fragment in str(err.value) and SECRET not in str(err.value)
    assert "\x1b" not in str(err.value)  # server text is printed without control characters
    assert len(server.requests) == 1


def test_a_dropped_connection_is_retried_like_any_network_failure():
    server = FakeServer([http.client.RemoteDisconnected("closed without a reply"), TimeoutError()])
    assert len(embedder(server).embed_passages(["a"])) == 1
    assert len(server.requests) == 3


def test_a_request_that_never_finishes_is_abandoned_and_retried():
    gate = threading.Event()  # never set: the first call hangs, as if keep-alive bytes kept coming
    hung = []

    def server(request, timeout=None):
        if not hung:
            hung.append(request)
            gate.wait(5)
            raise AssertionError("the hung call should have been abandoned")
        return FakeServer()(request)

    started = time.perf_counter()
    assert len(embedder(server, timeout=0.2).embed_passages(["a"])) == 1
    assert time.perf_counter() - started < 3
    gate.set()


def test_a_network_failure_is_a_clear_message():
    server = FakeServer([urllib.error.URLError("no route")] * 10)
    with pytest.raises(ModelMissing, match="could not reach OpenRouter"):
        embedder(server, retries=1).embed_passages(["a"])


def test_an_error_inside_a_200_reply_and_a_wrong_count_are_refused():
    with pytest.raises(ModelMissing, match="no embeddings"):
        embedder(FakeServer([FakeResponse({"error": {"message": "provider down"}})])).embed_query(
            "q"
        )
    short = FakeResponse({"data": [{"index": 0, "embedding": [1.0]}]})
    with pytest.raises(ModelMissing, match="1 embeddings for 2 texts"):
        embedder(FakeServer([short])).embed_passages(["a", "b"])


def test_a_missing_key_says_where_to_put_it(monkeypatch):
    monkeypatch.delenv(KEY_VARIABLE, raising=False)
    monkeypatch.setattr(embedders_module, "env_files", lambda: [])
    spec = EmbedderSpec(name="vendor/model-x", provider="openrouter")
    with pytest.raises(ModelMissing, match=f"{KEY_VARIABLE} is not set"):
        OpenRouterEmbedder(spec, opener=FakeServer()).embed_query("q")


def test_the_key_is_read_from_an_env_file_and_the_shell_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"{KEY_VARIABLE}=from-file\n")
    monkeypatch.delenv(KEY_VARIABLE, raising=False)
    monkeypatch.setattr(embedders_module, "env_files", lambda: [env])
    server = FakeServer()
    spec = EmbedderSpec(name="vendor/model-x", provider="openrouter")
    OpenRouterEmbedder(spec, opener=server).embed_query("q")
    assert server.requests[0].get_header("Authorization") == "Bearer from-file"
    monkeypatch.setenv(KEY_VARIABLE, "from-shell")
    OpenRouterEmbedder(spec, opener=server).embed_query("q")
    assert server.requests[1].get_header("Authorization") == "Bearer from-shell"


def test_a_worktree_also_looks_in_the_main_checkout_for_a_env_file(tmp_path, monkeypatch):
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    tree = tmp_path / "main" / ".claude" / "worktrees" / "wt"
    (tree / "retrieval").mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    monkeypatch.setattr(paths, "RETRIEVAL_DIR", tree / "retrieval")
    assert paths.env_files() == [tree / ".env", main / ".env"]
    plain = tmp_path / "plain"
    (plain / "retrieval").mkdir(parents=True)
    (plain / ".git").mkdir()
    monkeypatch.setattr(paths, "RETRIEVAL_DIR", plain / "retrieval")
    assert paths.env_files() == [plain / ".env"]


def test_the_hosted_tiers_are_configured_and_make_no_request_until_used():
    config = load_models_config()
    hosted = {t: s for t, s in config.embedder.items() if s.provider == "openrouter"}
    assert set(hosted) == {"qwen8b", "openai-large", "gemini2"}
    e = get_embedder("qwen8b")  # no key needed to construct it
    assert isinstance(e, OpenRouterEmbedder) and e.name == "openrouter:qwen/qwen3-embedding-8b"
    assert hosted["qwen8b"].query_prefix.startswith("Instruct:")


def test_vectors_from_a_hosted_model_are_kept_apart_from_local_ones(tmp_path):
    from retrieval.index import build_index
    from tests.retrieval_support import tiny_corpus

    e = embedder(FakeServer())
    build_index(e, chunks=tiny_corpus(), index_dir=tmp_path)
    names = [p.name for p in tmp_path.iterdir()]
    assert names == ["vectors-openrouter_vendor_model-x-default.npz"]
