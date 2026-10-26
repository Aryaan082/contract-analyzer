"""Where retrieval keeps its files."""

import os
from pathlib import Path

RETRIEVAL_DIR = Path(__file__).resolve().parent
MODELS_CONFIG = RETRIEVAL_DIR / "models.toml"
RECIPES_CONFIG = RETRIEVAL_DIR / "recipes.toml"
# Built by `make index`; git-ignored. Holds one vectors file per (embedder, recipe).
INDEX_DIR = RETRIEVAL_DIR / "index"


def model_dir() -> Path:
    """Where model weights live: outside the repo, so every worktree shares one copy."""
    override = os.environ.get("RETRIEVAL_MODEL_DIR")
    return Path(override) if override else Path.home() / ".cache" / "contract-analyzer" / "models"


def env_files() -> list[Path]:
    """The `.env` files to read a key from: this checkout's, and, when this is a git worktree
    (its `.git` is a file pointing into the main checkout's `.git/worktrees/<name>`), the main
    checkout's too, since `.env` is git-ignored and a worktree starts without one."""
    root = RETRIEVAL_DIR.parent
    files = [root / ".env"]
    git = root / ".git"
    if git.is_file() and "gitdir:" in (pointer := git.read_text()):
        gitdir = Path(pointer.split("gitdir:", 1)[1].strip())
        files.append(gitdir.parent.parent.parent / ".env")
    return files
