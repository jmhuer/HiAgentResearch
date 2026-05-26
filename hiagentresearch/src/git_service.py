from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitServiceError(RuntimeError):
    """Raised when a git operation fails."""


@dataclass(slots=True)
class GitCommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class GitService:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()

    def checkout(self, branch: str) -> None:
        self._run(["checkout", branch])

    def changed_files(self, *, staged: bool = False) -> list[str]:
        args = ["diff", "--name-only", "--cached"] if staged else ["status", "--porcelain"]
        result = self._run(args)
        if staged:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        changed: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.rsplit(" -> ", 1)[1]
            changed.append(path.strip('"'))
        return sorted(set(changed))

    def stage_paths(self, paths: list[str]) -> None:
        if not paths:
            raise GitServiceError("no paths provided to stage")
        self._run(["add", *paths])

    def has_core_staged_change(self, *, allowed_paths: list[str], supporting_paths: list[str]) -> bool:
        staged = set(self.changed_files(staged=True))
        supporting = set(supporting_paths)
        core_paths = {path for path in allowed_paths if path not in supporting}
        return bool(staged.intersection(core_paths))

    def commit(self, *, subject: str, body: str) -> str:
        self._run(["commit", "-m", subject, "-m", body])
        return self.head_sha()

    def push(self, *, remote: str, branch: str) -> None:
        self._run(["push", remote, f"HEAD:{branch}"])

    def head_sha(self) -> str:
        return self._run(["rev-parse", "HEAD"]).stdout.strip()

    def _run(self, args: list[str]) -> GitCommandResult:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        result = GitCommandResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
        if proc.returncode != 0:
            raise GitServiceError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return result
