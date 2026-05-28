from pathlib import Path

from hiagentresearch.src.paths import (
    REPO_ROOT,
    is_linked_git_worktree,
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


def test_is_linked_git_worktree_detects_git_file(tmp_path: Path) -> None:
    checkout = tmp_path / "wt"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: /path/to/main/.git/worktrees/wt\n", encoding="utf-8")
    assert is_linked_git_worktree(checkout) is True
    assert is_linked_git_worktree(tmp_path) is False
