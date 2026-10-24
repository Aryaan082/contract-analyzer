"""Runs the Foundry sandbox: one sell rehearsal on a fork of a live chain.

`chain/sandbox/` is a small Foundry project. This module starts `forge test` on it, as a child
process, with the parameters in environment variables, and reads the result back from a JSON file
the Solidity writes. Everything happens in Foundry's local EVM on a copy of the chain's state at
one block; nothing is sent to a real chain and no key is involved.

The RPC URL holds the provider's API key, so it travels only in the child's environment (never on
its command line) and nothing forge prints is passed on: output is scrubbed before it reaches a
log, and the agent only ever sees the parsed result.
"""

import json
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from chain.errors import scrub

log = logging.getLogger(__name__)

SANDBOX_DIR = Path(__file__).resolve().parent / "sandbox"
TIMEOUT_SECONDS = 180

Mode = Literal["live", "seed", "create"]
MODES: dict[Mode, int] = {"live": 0, "seed": 1, "create": 2}
Venue = Literal["v2", "v3"]
VENUES: dict[Venue, int] = {"v2": 0, "v3": 1}
STEPS = {"seed", "buy", "buy_empty", "approve", "sell"}


class SandboxUnavailable(Exception):
    """Foundry is not installed, or the sandbox project is missing."""


class SandboxFailed(Exception):
    """Foundry ran but produced no usable result (timeout, fork failure, compile error)."""


@dataclass(frozen=True, slots=True)
class RehearsalParams:
    token: str
    router: str
    factory: str
    wrapped_native: str
    mode: Mode
    trade_wei: int
    seed_wei: int
    gas_price: int
    venue: Venue = "v2"
    fee: int = 0  # v3 only: the pool's fee tier, in hundredths of a basis point

    def __post_init__(self) -> None:
        # A V3 pool is traded in as it is: adding liquidity to V3 is not rehearsed.
        if self.venue == "v3" and self.mode != "live":
            raise ValueError("A v3 rehearsal can only trade in a live pool.")


@dataclass(frozen=True, slots=True)
class RehearsalResult:
    ok: bool
    step: str  # when not ok: seed, buy, buy_empty, approve or sell
    revert: bytes
    pair: str
    provider: str  # who added liquidity: owner, invented or none
    native_reserve: int
    received: int
    pool_gave: int
    arrived: int
    native_back: int
    left: int


def forge_binary() -> str | None:
    found = shutil.which("forge")
    if found:
        return found
    default = Path.home() / ".foundry" / "bin" / "forge"
    return str(default) if default.exists() else None


def parse_result(raw: object) -> RehearsalResult:
    """Validate the JSON the Solidity wrote. Anything unexpected is a failure, not a guess."""
    try:
        assert isinstance(raw, dict)
        numbers = [
            raw[k]
            for k in ("native_reserve", "received", "pool_gave", "arrived", "native_back", "left")
        ]
        assert all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in numbers)
        assert isinstance(raw["ok"], bool) and isinstance(raw["step"], str)
        assert raw["ok"] or raw["step"] in STEPS
        assert raw["provider"] in ("owner", "invented", "none")
        revert = raw["revert"]
        assert isinstance(revert, str) and revert.startswith("0x") and len(revert) <= 2 + 2 * 4096
        assert isinstance(raw["pair"], str) and raw["pair"].startswith("0x")
        return RehearsalResult(
            ok=raw["ok"],
            step=raw["step"],
            revert=bytes.fromhex(revert[2:]),
            pair=raw["pair"],
            provider=raw["provider"],
            native_reserve=raw["native_reserve"],
            received=raw["received"],
            pool_gave=raw["pool_gave"],
            arrived=raw["arrived"],
            native_back=raw["native_back"],
            left=raw["left"],
        )
    except (AssertionError, KeyError, ValueError, TypeError):
        raise SandboxFailed("The sandbox wrote a result in an unexpected shape.") from None


def run_rehearsal(
    fork_url: str, block: int, params: RehearsalParams, *, timeout: int = TIMEOUT_SECONDS
) -> RehearsalResult:
    forge = forge_binary()
    if forge is None or not (SANDBOX_DIR / "foundry.toml").exists():
        raise SandboxUnavailable("Foundry (forge) is not installed.")
    run_id = uuid.uuid4().hex
    out_file = SANDBOX_DIR / "results" / f"{run_id}.json"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "FOUNDRY_ETH_RPC_URL": fork_url,
        "FOUNDRY_FORK_BLOCK_NUMBER": str(block),
        "SANDBOX_TOKEN": params.token,
        "SANDBOX_ROUTER": params.router,
        "SANDBOX_FACTORY": params.factory,
        "SANDBOX_WETH": params.wrapped_native,
        "SANDBOX_MODE": str(MODES[params.mode]),
        "SANDBOX_TRADE_WEI": str(params.trade_wei),
        "SANDBOX_SEED_WEI": str(params.seed_wei),
        "SANDBOX_GAS_PRICE": str(params.gas_price),
        "SANDBOX_VENUE": str(VENUES[params.venue]),
        "SANDBOX_FEE": str(params.fee),
        "SANDBOX_OUT": f"results/{run_id}.json",
    }
    command = [
        forge,
        "test",
        "--match-contract",
        "RehearseTest",
        "--match-test",
        "testRehearse",
    ]
    try:
        # Foundry occasionally aborts on a transient node error; one retry is cheap.
        for attempt in (1, 2):
            done = subprocess.run(
                command, cwd=SANDBOX_DIR, env=env, capture_output=True, text=True, timeout=timeout
            )
            if out_file.exists():
                break
            tail = scrub((done.stdout + done.stderr)[-600:])
            log.warning(
                "sandbox attempt %s gave no result (exit %s): %s", attempt, done.returncode, tail
            )
        else:
            raise SandboxFailed("The sandbox produced no result.")
        try:
            raw = json.loads(out_file.read_text())
        except ValueError:
            raise SandboxFailed("The sandbox wrote a result that is not JSON.") from None
        return parse_result(raw)
    except subprocess.TimeoutExpired:
        raise SandboxFailed("The sandbox timed out.") from None
    except OSError as err:
        raise SandboxFailed(f"The sandbox could not be started: {type(err).__name__}.") from None
    finally:
        out_file.unlink(missing_ok=True)
