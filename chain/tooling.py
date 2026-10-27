"""The wrapper every address-based tool shares: normalise the address, run the work, and turn any
failure into the `{provenance, data: {error}}` envelope with a message we wrote."""

import logging
from collections.abc import Callable

from pydantic import BaseModel

from chain.errors import ChainError, scrub
from chain.models import Provenance, ToolResult, normalize_address
from chain.rpc import RpcClient

log = logging.getLogger(__name__)


def run_tool[T: BaseModel](
    result_type: type[ToolResult[T]],
    client: RpcClient,
    address: str,
    source: str,
    work: Callable[[str], T],
    *,
    pinned: bool = True,
) -> ToolResult[T]:
    """`work` receives the checksummed address and returns the tool's data. Provenance is taken
    after the work, so it names the chain id and block the reads were pinned to; fields the run
    never learned stay None. `pinned=False` is for data that is not "as of" the pinned block (an
    explorer's answer is for now): provenance then leaves the block empty."""
    shown: str | None = None
    try:
        shown = normalize_address(address)
        data = work(shown)
    except ChainError as err:
        return result_type(
            provenance=_provenance(client, shown, source, pinned), data=err.to_tool_error()
        )
    except Exception as exc:
        # Not log.exception: a traceback prints the exception text, which may hold a URL and key.
        log.warning("tool failed (%s): %s", type(exc).__name__, scrub(repr(exc)))
        error = ChainError("internal_error", "Unexpected internal error; see the log.")
        return result_type(
            provenance=_provenance(client, shown, source, pinned), data=error.to_tool_error()
        )
    return result_type(provenance=_provenance(client, shown, source, pinned), data=data)


def _provenance(client: RpcClient, address: str | None, source: str, pinned: bool) -> Provenance:
    provenance = client.provenance(address, source=source)
    return provenance if pinned else provenance.model_copy(update={"block": None})
