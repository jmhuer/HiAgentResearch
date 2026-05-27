from pathlib import Path

from hiagentresearch.src.paths import (
    REPO_ROOT,
    resolve_execution_root,
    resolve_runs_dir,
)


def test_resolve_runs_dir_scoped_to_checkout(tmp_path: Path) -> None:
    worktree = tmp_path / "worktrees" / "model"
    worktree.mkdir(parents=True)
    assert resolve_runs_dir(worktree) == worktree / ".hiagentresearch" / "runs"


def test_resolve_execution_root_prefers_workdir(monkeypatch, tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setenv("HIAGENTRESEARCH_WORKTREE", str(tmp_path / "env-wt"))
    assert resolve_execution_root(worktree) == worktree.resolve()


def test_resolve_runs_dir_defaults_to_repo_when_no_checkout() -> None:
    assert resolve_runs_dir() == (REPO_ROOT / ".hiagentresearch" / "runs").resolve()
