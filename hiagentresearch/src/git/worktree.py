from __future__ import annotations

import subprocess
from pathlib import Path

from hiagentresearch.src.git.service import GitService, GitServiceError
from hiagentresearch.src.paths import DEFAULT_WORKTREES_DIR, REPO_ROOT


class WorktreeManager:
    def __init__(self, repo_root: Path | None = None, worktree_root: str | Path | None = None) -> None:
        self.repo_root = (repo_root or REPO_ROOT).resolve()
        relative = Path(worktree_root) if worktree_root is not None else DEFAULT_WORKTREES_DIR
        self.worktree_root = relative if relative.is_absolute() else (self.repo_root / relative).resolve()
        self.git = GitService(self.repo_root)

    def path_for(self, group_id: str) -> Path:
        return self.worktree_root / group_id

    def ensure(self, group_id: str, branch: str, *, start_ref: str | None = None) -> Path:
        path = self.path_for(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.remove(group_id)
        if self.git.branch_exists(branch):
            self._run(["worktree", "add", str(path), branch])
        else:
            ref = start_ref or "main"
            self._run(["worktree", "add", "-b", branch, str(path), ref])
        return path

    def remove(self, group_id: str) -> None:
        path = self.path_for(group_id)
        if not path.exists():
            return
        self._run(["worktree", "remove", "--force", str(path)])
        self._run(["worktree", "prune"])

    def remove_all(self) -> None:
        if not self.worktree_root.exists():
            return
        for entry in sorted(self.worktree_root.iterdir()):
            if entry.is_dir():
                self.remove(entry.name)

    def _run(self, args: list[str]) -> None:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitServiceError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
