"""Record and replay JSON-RPC traffic so node-backed code can be tested offline.

A cassette holds only each request's method and params and the node's answer. It never holds the
endpoint URL (which contains an API key), so cassettes are safe to commit.

Replay matches on exact (method, params). Because every read is pinned to a block number, the
same run always sends the same requests. A request with no recording raises ReplayMiss.
"""

import json
from pathlib import Path
from typing import Any

from web3.providers.base import BaseProvider, JSONBaseProvider

# A well-formed address that points at nothing real. Slice 1 tests make no on-chain claims.
ADDR = "0x1111111111111111111111111111111111111111"


class ReplayMiss(BaseException):  # noqa: N818
    """Not an Exception, so the client's error handling cannot turn a missing recording into a
    normal-looking failure."""


def request_key(method: str, params: Any) -> str:
    return json.dumps([method, params], sort_keys=True, separators=(",", ":"))


def ok(method: str, params: Any, result: Any) -> dict:
    return {"method": method, "params": params, "response": {"result": result}}


def fail(method: str, params: Any, message: str, code: int = -32000) -> dict:
    return {
        "method": method,
        "params": params,
        "response": {"error": {"code": code, "message": message}},
    }


class ReplayProvider(JSONBaseProvider):
    def __init__(self, interactions: list[dict]) -> None:
        super().__init__()
        self._responses = {
            request_key(i["method"], i["params"]): i["response"] for i in interactions
        }
        self.requests: list[tuple[str, Any]] = []

    @classmethod
    def from_file(cls, path: Path) -> "ReplayProvider":
        return cls(json.loads(path.read_text())["interactions"])

    def make_request(self, method, params):
        self.requests.append((method, params))
        try:
            response = self._responses[request_key(method, params)]
        except KeyError:
            raise ReplayMiss(f"no recording for {request_key(method, params)}") from None
        return {"jsonrpc": "2.0", "id": next(self.request_counter), **response}


class RecordingProvider(JSONBaseProvider):
    """Passes requests to a real provider and remembers the answers."""

    def __init__(self, inner: BaseProvider) -> None:
        super().__init__()
        self._inner = inner
        self._interactions: dict[str, dict] = {}

    def make_request(self, method, params):
        response = self._inner.make_request(method, params)
        answer = {k: response[k] for k in ("result", "error") if k in response}
        self._interactions[request_key(method, params)] = {
            "method": method,
            "params": params,
            "response": answer,
        }
        return response

    def save(self, path: Path) -> None:
        data = {"interactions": list(self._interactions.values())}
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
