"""Local inspector: a read-only web page for checking what the chain tools return.

This is a development aid, not the product UI (that is Component 7). It listens on 127.0.0.1
only, answers GET only, rejects requests whose Host header is not local (DNS rebinding), and only
talks to chains configured in .env. Each tool gets a panel here as its slice lands.

The page renders data from the chain, which is untrusted, so it only ever uses textContent.
"""

import json
import logging
import sys
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel
from web3 import Web3

from chain.config import DEFAULT_CHAIN
from chain.errors import ChainError, scrub
from chain.models import Provenance, ToolResult, normalize_address
from chain.rpc import RpcClient, probe_chains
from chain.slots import PRESETS

log = logging.getLogger(__name__)

MAX_SLOTS = 8
PAGE = Path(__file__).with_name("inspector.html")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}


# -- what the inspector returns ---------------------------------------------------------------
# Until slice 2 builds the real `get_code` tool, the inspector derives size and hash itself from
# the raw bytes. The point is the shape: small, and cited by chain, address and block.


class CodeSummary(BaseModel):
    has_code: bool
    size: int
    keccak256: str


class SlotRead(BaseModel):
    slot: str
    name: str | None
    value: str


class Inspection(BaseModel):
    code: CodeSummary
    storage: list[SlotRead]


def parse_block(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        raise ChainError("invalid_block", "Block must be a whole number.") from None


def parse_slot(raw: str) -> int:
    text = raw.strip()
    try:
        slot = int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        slot = -1
    if not 0 <= slot < 2**256:
        raise ChainError(
            "invalid_slot", "Slot must be a number from 0 to 2^256 - 1 (decimal or 0x hex)."
        )
    return slot


def parse_slots(raw: str | None) -> list[tuple[str | None, int]]:
    """Comma-separated slots, each a number or a preset name (see chain.slots). Returns
    (preset name or None, slot) pairs without duplicates."""
    slots: list[tuple[str | None, int]] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        name = token.lower()
        entry = (name, PRESETS[name]) if name in PRESETS else (None, parse_slot(token))
        if entry not in slots:
            slots.append(entry)
    if len(slots) > MAX_SLOTS:
        raise ChainError("invalid_slot", f"At most {MAX_SLOTS} slots per request.")
    return slots


def describe_slot(value: bytes) -> dict[str, Any]:
    """Human-readable guesses for a 32-byte storage value. They are guesses: a slot has no type."""
    as_address = None
    if len(value) == 32 and value[:12] == bytes(12) and any(value[12:]):
        as_address = Web3.to_checksum_address(value[12:])

    # Solidity keeps a string of up to 31 bytes in one slot: the text in the high bytes and
    # length * 2 in the last byte.
    as_short_string = None
    if len(value) == 32 and value[-1] and value[-1] % 2 == 0:
        length = value[-1] // 2
        if length <= 31 and not any(value[length:31]):
            try:
                text = value[:length].decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            as_short_string = text if text.isprintable() and text else None

    return {
        "value": Web3.to_hex(value),
        "as_address": as_address,
        "as_short_string": as_short_string,
        "as_integer": str(int.from_bytes(value, "big")),
    }


def inspect(env: Mapping[str, str], query: Mapping[str, str]) -> dict[str, Any]:
    """Run the reads for one address and return `{tool_result, raw}`.

    `tool_result` is the `{provenance, data}` envelope (what an agent would see). `raw` carries
    the full bytes so a person can compare them with an independent source, or is None on failure.
    """
    chain = (query.get("chain") or DEFAULT_CHAIN).strip().lower()[:40]
    address = client = None
    try:
        address = normalize_address(query.get("address", "").strip())
        block = parse_block(query.get("block"))
        slots = parse_slots(query.get("slots"))
        client = RpcClient.connect(chain, env, block)
        code = client.get_code(address)
        values = [client.get_storage_at(address, slot) for _, slot in slots]
    except ChainError as err:
        provenance = (
            client.provenance(address)
            if client
            else Provenance(chain=chain, chain_id=None, address=address, block=None, source="rpc")
        )
        failure = ToolResult[Inspection](provenance=provenance, data=err.to_tool_error())
        return {"tool_result": failure.model_dump(mode="json"), "raw": None}

    result = ToolResult[Inspection](
        provenance=client.provenance(address),
        data=Inspection(
            code=CodeSummary(
                has_code=bool(code),
                size=len(code),
                keccak256=Web3.to_hex(Web3.keccak(code)),
            ),
            storage=[
                SlotRead(slot=hex(slot), name=name, value=Web3.to_hex(value))
                for (name, slot), value in zip(slots, values, strict=True)
            ],
        ),
    )
    return {
        "tool_result": result.model_dump(mode="json"),
        "raw": {
            "bytecode": Web3.to_hex(code),
            "storage": [
                {**describe_slot(value), "slot": hex(slot), "name": name}
                for (name, slot), value in zip(slots, values, strict=True)
            ],
        },
    }


def chains_payload(env: Mapping[str, str]) -> dict[str, Any]:
    rows = []
    for name, (chain_id, err) in probe_chains(env).items():
        status = (
            "ok" if err is None else "unreachable" if err.code == "rpc_unreachable" else "error"
        )
        rows.append(
            {
                "name": name,
                "chain_id": chain_id,
                "status": status,
                "message": err.message if err else None,
            }
        )
    return {"chains": rows}


# -- the server --------------------------------------------------------------------------------


class InspectorServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], env: Mapping[str, str]) -> None:
        super().__init__(address, Handler)
        self.env = env
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}


class Handler(BaseHTTPRequestHandler):
    server: InspectorServer
    server_version = "chain-inspector"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        if self.headers.get("Host") not in self.server.allowed_hosts:
            return self._json({"error": "forbidden host"}, HTTPStatus.FORBIDDEN)
        url = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                self._send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/chains":
                self._json(chains_payload(self.server.env))
            elif url.path == "/api/inspect":
                self._json(inspect(self.server.env, query))
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            log.warning("inspector error: %s", scrub(repr(exc)))
            self._json({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def serve(env: Mapping[str, str], port: int = 8765) -> int:
    try:
        server = InspectorServer(("127.0.0.1", port), env)
    except OSError as exc:
        print(f"Cannot listen on 127.0.0.1:{port}: {exc.strerror}", file=sys.stderr)
        return 1
    print(f"Inspector running at http://127.0.0.1:{server.server_port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
