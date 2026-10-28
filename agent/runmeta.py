"""Facts about where and with what a run happened, for the trace and for Component 6's results
files: the code version, the settings file, the machine. Nothing here touches a run's behaviour,
and nothing secret is read (no environment variables except one free-text label).

Each fact is looked up once per process and a failure is `None`, never an error: a missing `git`
or `forge` must not stop an analysis.
"""

import hashlib
import os
import platform
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.config import MODELS_CONFIG

ROOT = Path(__file__).resolve().parent.parent
NETWORK_LABEL_VARIABLE = "AGENT_NETWORK_LABEL"  # free text such as "home wi-fi"; optional


def digest(text: str | bytes) -> str:
    """A short, stable fingerprint: the first 16 hex characters of the SHA-256."""
    data = text.encode() if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()[:16]


def _output(command: list[str]) -> str | None:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


@lru_cache(maxsize=1)
def environment() -> dict[str, Any]:
    status = _output(["git", "-C", str(ROOT), "status", "--porcelain"])
    forge = _output(["forge", "--version"])
    try:
        settings = digest(MODELS_CONFIG.read_bytes())
    except OSError:
        settings = None
    return {
        "git_commit": _output(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
        "git_dirty": None if status is None else bool(status),
        "models_toml_sha": settings,
        "forge": forge.splitlines()[0] if forge else None,
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "network_label": os.environ.get(NETWORK_LABEL_VARIABLE) or None,
    }
