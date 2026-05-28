from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_STATE_DIR = REPO_ROOT / ".hiagentresearch" / "state"
DEFAULT_RUNS_DIR = REPO_ROOT / ".hiagentresearch" / "runs"
DEFAULT_WORKTREES_DIR = REPO_ROOT / ".hiagentresearch" / "worktrees"
RUNS_RELATIVE = Path(".hiagentresearch") / "runs"


def resolve_state_dir() -> Path:
    return Path(os.environ.get("HIAGENTRESEARCH_STATE_DIR", str(DEFAULT_STATE_DIR))).resolve()


def resolve_execution_root(workdir: Path | None = None) -> Path:
    """Git checkout root where a cycle runs (main repo or parallel worktree)."""
    if workdir is not None:
        return workdir.resolve()
    override = os.environ.get("HIAGENTRESEARCH_WORKTREE", "").strip()
    if override:
        return Path(override).resolve()
    return REPO_ROOT


def resolve_runs_dir(checkout_root: Path | None = None) -> Path:
    """Ephemeral run artifacts for the active checkout (worktree or main repo)."""
    explicit = os.environ.get("HIAGENTRESEARCH_RUNS_DIR", "").strip()
    if checkout_root is None:
        return Path(explicit or str(DEFAULT_RUNS_DIR)).resolve()
    return (checkout_root.resolve() / RUNS_RELATIVE).resolve()


def resolve_config_path() -> Path:
    return Path(os.environ.get("HIAGENTRESEARCH_CONFIG", str(DEFAULT_CONFIG_PATH))).resolve()


def is_linked_git_worktree(path: Path) -> bool:
    """True when path is a linked git worktree (not the primary repo checkout)."""
    return (path / ".git").is_file()
