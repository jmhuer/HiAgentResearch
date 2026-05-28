from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitServiceError(RuntimeError):
    """Raised when a git operation fails."""


def _is_within(path: str, root: str) -> bool:
    normalized = path.rstrip("/")
    root_normalized = root.rstrip("/")
    if root_normalized in ("", "."):
        return True
    return normalized == root_normalized or normalized.startswith(f"{root_normalized}/")


def _is_under_any(path: str, prefixes: list[str]) -> bool:
    normalized = path.rstrip("/")
    for prefix in prefixes:
        prefix_normalized = prefix.rstrip("/")
        if not prefix_normalized:
            continue
        if normalized == prefix_normalized or normalized.startswith(f"{prefix_normalized}/"):
            return True
    return False


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

    def checkout_or_create(
        self,
        branch: str,
        *,
        base_branch: str = "main",
        start_ref: str | None = None,
        sync_to_ref: bool = False,
    ) -> None:
        ref = start_ref or base_branch
        if self.branch_exists(branch):
            self.checkout(branch)
            if sync_to_ref and start_ref:
                self.sync_to_ref(start_ref)
            return
        self._run(["checkout", "-b", branch, ref])

    def sync_to_ref(self, ref: str) -> bool:
        target = self.resolve_ref(ref)
        if self.head_sha() == target:
            return False
        if self.changed_files():
            raise GitServiceError(f"cannot sync to {ref}: working tree has local changes")
        self._run(["reset", "--hard", target])
        return True

    def resolve_ref(self, ref: str) -> str:
        return self._run(["rev-parse", ref]).stdout.strip()

    def ref_exists(self, ref: str) -> bool:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    def branch_exists(self, branch: str) -> bool:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

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
        args = ["add"]
        if any(path.startswith(".hiagentresearch/experiments/") for path in paths):
            args.append("-f")
        args.extend(paths)
        self._run(args)

    def has_staged_workspace_change(
        self,
        *,
        workdir: str,
        generated_paths: list[str],
        reference_paths: list[str],
        hidden_paths: list[str],
    ) -> bool:
        excluded = [*generated_paths, *reference_paths, *hidden_paths]
        for path in self.changed_files(staged=True):
            if not _is_within(path, workdir):
                continue
            if _is_under_any(path, excluded):
                continue
            return True
        return False

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
