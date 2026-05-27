from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_STATE_DIR = REPO_ROOT / ".hiagentresearch" / "state"
DEFAULT_RUNS_DIR = REPO_ROOT / ".hiagentresearch" / "runs"
DEFAULT_WORKTREES_DIR = REPO_ROOT / ".hiagentresearch" / "worktrees"


def resolve_state_dir() -> Path:
    return Path(os.environ.get("HIAGENTRESEARCH_STATE_DIR", str(DEFAULT_STATE_DIR))).resolve()


def resolve_runs_dir() -> Path:
    return Path(os.environ.get("HIAGENTRESEARCH_RUNS_DIR", str(DEFAULT_RUNS_DIR))).resolve()


def resolve_config_path() -> Path:
    return Path(os.environ.get("HIAGENTRESEARCH_CONFIG", str(DEFAULT_CONFIG_PATH))).resolve()
