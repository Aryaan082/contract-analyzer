"""Model and caps for the agent, read from models.toml."""

import tomllib
from pathlib import Path

from pydantic import BaseModel

AGENT_DIR = Path(__file__).resolve().parent
MODELS_CONFIG = AGENT_DIR / "models.toml"
CONTRACTS_FILE = AGENT_DIR / "contracts.toml"
# One JSON trace per run; git-ignored.
RUNS_DIR = AGENT_DIR / "runs"


class ModelSpec(BaseModel, frozen=True):
    name: str  # the slug OpenRouter knows it by
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0


class Limits(BaseModel, frozen=True):
    max_turns: int = 12
    max_tool_calls: int = 25
    max_output_tokens: int = 4096
    wall_clock_seconds: float = 420.0
    max_cost_usd: float = 0.75
    max_report_attempts: int = 3
    request_timeout_seconds: float = 90.0


class AgentConfig(BaseModel, frozen=True):
    default_model: str
    model: dict[str, ModelSpec]
    limits: Limits

    def resolve_model(self, tier_or_slug: str | None = None) -> ModelSpec:
        """A tier from models.toml, or a slug used as it is (its cost then comes only from what
        OpenRouter reports)."""
        wanted = tier_or_slug or self.default_model
        return self.model.get(wanted) or ModelSpec(name=wanted)


def load_config(path: Path = MODELS_CONFIG) -> AgentConfig:
    return AgentConfig.model_validate(tomllib.loads(path.read_text()))
