"""profile: every chain tool's answer for one address, side by side, each with its provenance.

This is the Component 1 done criterion: one command that prints a full JSON profile of any
address. It adds no analysis of its own. It runs the tools in a fixed order, keeps each tool's
`{provenance, data}` envelope as it is, and records what it skipped and why. One tool failing
never hides the others; only a failed code read stops the run, because nothing else can be
trusted without the chain.

The tools read the chain at one pinned block, so the code, the proxy checks and the selectors all
describe the same state. The explorer tools answer for now (their provenance has no block).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel

from chain.code import CodeData, CodeStore, get_code, read_code
from chain.db import Database, PostgresCodeStore, PostgresSelectorStore
from chain.errors import ChainError, ToolError
from chain.explorer import (
    DeploymentData,
    ExplorerClient,
    SourceData,
    get_deployment,
    get_verified_source,
)
from chain.identify import Identity, identify_contract
from chain.models import Provenance, ToolResult, normalize_address
from chain.proxy import ProxyData, resolve_proxy
from chain.rpc import RpcClient
from chain.selectors import SelectorsData, run_extract_selectors
from chain.signatures import (
    MAX_SELECTORS,
    Fetcher,
    LookupData,
    SelectorStore,
    fetch_sourcify,
    lookup_selector,
)

log = logging.getLogger(__name__)


class Profile(BaseModel):
    address: str
    chain: str
    chain_id: int | None
    block: int | None
    code: ToolResult[CodeData]
    proxy: ToolResult[ProxyData] | None = None
    identity: ToolResult[Identity] | None = None
    selectors: ToolResult[SelectorsData] | None = None
    implementation_selectors: ToolResult[SelectorsData] | None = None
    signatures: ToolResult[LookupData] | None = None
    verified_source: ToolResult[SourceData] | None = None
    implementation_source: ToolResult[SourceData] | None = None
    deployment: ToolResult[DeploymentData] | None = None
    skipped: dict[str, str]  # tool -> why it did not run


@dataclass(frozen=True, slots=True)
class Services:
    """What the profile may use besides the node. Each is optional: a missing one is reported,
    not fatal."""

    code_store: CodeStore | None = None
    selector_store: SelectorStore | None = None
    explorer: ExplorerClient | None = None
    fetch_names: Fetcher = fetch_sourcify

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Services":
        """Postgres if DATABASE_URL is set, the explorer if ETHERSCAN_API_KEY is."""
        db = Database.from_env(env)
        return cls(
            code_store=PostgresCodeStore(db) if db else None,
            selector_store=PostgresSelectorStore(db) if db else None,
            explorer=ExplorerClient.from_env(env),
        )


def _failed(result: ToolResult) -> bool:
    return isinstance(result.data, ToolError)


def build_profile(client: RpcClient, address: str, services: Services | None = None) -> Profile:
    services = services or Services()
    address = normalize_address(address)
    skipped: dict[str, str] = {}

    code = get_code(client, address, services.code_store)

    def finish(**parts: ToolResult | None) -> Profile:
        provenance = code.provenance
        return Profile(
            address=address,
            chain=provenance.chain,
            chain_id=provenance.chain_id,
            block=provenance.block,
            code=code,
            skipped=skipped,
            **parts,
        )

    if _failed(code):
        skipped["everything else"] = "The code could not be read, so nothing else was tried."
        return finish()

    proxy = resolve_proxy(client, address, services.code_store)
    identity = identify_contract(client, address, services.code_store)

    selectors = implementation_selectors = signatures = None
    implementation = None if _failed(proxy) else proxy.data.implementation
    if not code.data.has_code:
        skipped["extract_selectors"] = "There is no code at this address."
        skipped["lookup_selector"] = "There is no code at this address."
    else:
        raw = read_code(client, address, services.code_store).code
        selectors = run_extract_selectors(raw, code.provenance)
        names = set() if _failed(selectors) else {s.selector for s in selectors.data.selectors}
        if implementation is not None:
            behind = read_code(client, implementation.address, services.code_store).code
            implementation_selectors = run_extract_selectors(
                behind, client.provenance(implementation.address)
            )
            if not _failed(implementation_selectors):
                names |= {s.selector for s in implementation_selectors.data.selectors}
        if names:
            wanted = sorted(names)
            if len(wanted) > MAX_SELECTORS:
                skipped["lookup_selector (partial)"] = (
                    f"Only the first {MAX_SELECTORS} of {len(wanted)} selectors were looked up."
                )
            signatures = lookup_selector(
                wanted[:MAX_SELECTORS],
                code.provenance,
                store=services.selector_store,
                fetch=services.fetch_names,
            )
        else:
            skipped["lookup_selector"] = "No selectors were found to look up."

    verified_source = get_verified_source(client, address, services.explorer)
    deployment = get_deployment(client, address, services.explorer)
    implementation_source = None
    if implementation is not None:
        implementation_source = get_verified_source(
            client, implementation.address, services.explorer
        )
    if services.explorer is None:
        skipped["explorer tools"] = "No ETHERSCAN_API_KEY is configured."

    return finish(
        proxy=proxy,
        identity=identity,
        selectors=selectors,
        implementation_selectors=implementation_selectors,
        signatures=signatures,
        verified_source=verified_source,
        implementation_source=implementation_source,
        deployment=deployment,
    )


def failed_profile(chain: str, address: str, err: ChainError) -> Profile:
    """A profile for when no client could be made (unknown chain, bad address)."""
    failed = Provenance(chain=chain, chain_id=None, address=None, block=None, source="rpc")
    return Profile(
        address=address[:60],
        chain=chain,
        chain_id=None,
        block=None,
        code=ToolResult[CodeData](provenance=failed, data=err.to_tool_error()),
        skipped={"everything else": "No chain connection could be made."},
    )


def failed_tools(profile: Profile) -> list[str]:
    """Names of the tools whose answer is an error, for a summary line."""
    parts: dict[str, ToolResult | None] = {
        name: getattr(profile, name) for name in type(profile).model_fields
    }
    return [
        name
        for name, part in parts.items()
        if isinstance(part, ToolResult) and isinstance(part.data, ToolError)
    ]
