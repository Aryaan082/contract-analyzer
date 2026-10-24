"""Adding a deployed contract to the library: fetch its code, check its ABI against the code, save.

Run as `python -m references add`, never during an analysis: a reference is a fixed yardstick, so
its bytes are fetched once, pinned to a block and committed, not looked up on demand.

Sourcify (a free public service, no API key) supplies the ABI of verified contracts. Everything it
returns is untrusted: only function entries are used, their signatures must be plain signatures,
and every selector must appear in the code we fetched, or nothing is saved.
"""

import json
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from chain.models import normalize_address
from chain.rpc import RpcClient
from references.abi import abi_signature, is_signature, keccak_hex, selector_of
from references.bytecode import push4_values
from references.library import DATA_DIR
from references.models import DeployedOrigin, Function, Reference

SOURCIFY_URL = "https://sourcify.dev/server/v2/contract/{chain_id}/{address}"
MAX_RESPONSE_BYTES = 5_000_000
REQUEST_TIMEOUT_SECONDS = 20


class AcquireError(Exception):
    """Something stopped a reference from being added. The message is safe to print."""


def _get_json(url: str) -> dict:
    # Only ever called with a URL built from the constants above, an int and a checksummed address.
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except OSError as err:
        raise AcquireError(f"Sourcify request failed: {type(err).__name__}") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise AcquireError("Sourcify response is too large.")
    try:
        data = json.loads(body)
    except ValueError:
        raise AcquireError("Sourcify did not return JSON.") from None
    if not isinstance(data, dict):
        raise AcquireError("Sourcify returned an unexpected shape.")
    return data


def fetch_verified_abi(chain_id: int, address: str) -> tuple[list[Function], str]:
    """The function list of a Sourcify-verified contract, and how well its code matched."""
    base = SOURCIFY_URL.format(chain_id=chain_id, address=address)
    status = _get_json(base).get("runtimeMatch")
    if status not in ("match", "exact_match"):
        raise AcquireError(f"Sourcify has no runtime match for this address (got {status!r}).")
    abi = _get_json(f"{base}?fields=abi").get("abi")
    if not isinstance(abi, list):
        raise AcquireError("Sourcify returned no ABI.")
    functions = {}
    for entry in abi:
        if not isinstance(entry, dict) or entry.get("type") != "function":
            continue
        try:
            signature = abi_signature(entry)
        except (KeyError, TypeError):
            raise AcquireError("Sourcify returned a malformed ABI entry.") from None
        if not is_signature(signature):
            raise AcquireError("Sourcify returned a function signature that is not plain.")
        functions[selector_of(signature)] = signature
    return [Function(selector=s, signature=sig) for s, sig in sorted(functions.items())], status


def build_deployed_reference(
    *,
    reference_id: str,
    name: str,
    family: str,
    chain: str,
    address: str,
    env: Mapping[str, str],
    block: int | None = None,
) -> Reference:
    address = normalize_address(address)
    client = RpcClient.connect(chain, env, block)
    code = client.get_code(address)
    if not code:
        raise AcquireError(f"There is no code at {address} on {chain} at block {client.block}.")
    functions, status = fetch_verified_abi(client.chain_id, address)
    missing = {f.selector for f in functions} - push4_values(code)
    if missing:
        raise AcquireError(
            f"The verified ABI lists selectors that are not in the code: {sorted(missing)}."
        )
    return Reference(
        id=reference_id,
        name=name,
        family=family,
        kind="contract",
        origin=DeployedOrigin(
            chain=chain,
            chain_id=client.chain_id,
            address=address,
            block=client.block,
            abi_source=f"sourcify:{status}",
        ),
        source_url=SOURCIFY_URL.format(chain_id=client.chain_id, address=address),
        functions=tuple(functions),
        code="0x" + code.hex(),
        code_hash=keccak_hex(code),
    )


def save_reference(reference: Reference, directory: Path = DATA_DIR) -> Path:
    """Write the record. An existing id is never overwritten: delete the file to replace it."""
    path = directory / f"{reference.id}.json"
    if path.exists():
        raise AcquireError(f"{path.name} already exists. Delete it first to replace it.")
    path.write_text(reference.model_dump_json(indent=2) + "\n")
    return path
