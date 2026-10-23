"""Chain configuration: one `RPC_URL_<NAME>=...` line per chain in `.env`."""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv

from chain.errors import ChainError

RPC_PREFIX = "RPC_URL_"
DEFAULT_CHAIN = "ethereum"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env() -> Mapping[str, str]:
    """Load `.env` into the process environment. Variables already set in the shell win."""
    load_dotenv(ENV_PATH)
    return os.environ


def configured_chains(env: Mapping[str, str]) -> dict[str, str]:
    """Map chain name (lowercase) to RPC URL. Lines left blank are ignored."""
    chains = {}
    for key, value in env.items():
        name = key.removeprefix(RPC_PREFIX).lower() if key.startswith(RPC_PREFIX) else ""
        if name and value.strip():
            chains[name] = value.strip()
    return dict(sorted(chains.items()))


def chain_url(chain: str, env: Mapping[str, str]) -> str:
    chains = configured_chains(env)
    url = chains.get(chain.strip().lower())
    if url is None:
        available = ", ".join(chains) or "none"
        raise ChainError(
            "chain_not_configured",
            f"No RPC URL configured for chain {chain[:40]!r}. Configured chains: {available}.",
        )
    return url
