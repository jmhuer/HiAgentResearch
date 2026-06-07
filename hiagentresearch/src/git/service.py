from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from hiagentresearch.src.core.pathspec import is_under_any, is_within


class GitServiceError(RuntimeError):
    """Raised when a git operation fails."""


def _is_workspace_bytecode_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return "/__pycache__/" in normalized or normalized.endswith(".pyc")


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

    def discard_worktree_changes(self) -> None:
        """Restore the working tree to HEAD: revert tracked modifications and remove
        untracked files. Ignored paths (e.g. generated data/checkpoints) are preserved
        (`clean` without `-x`). Used to get a clean slate before retrying a cycle whose
        agent run failed transiently and may have left a partial edit behind."""
        self._run(["reset", "--hard", "HEAD"])
        self._run(["clean", "-fd"])

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

    def stage_research_commit(
        self,
        *,
        workdir: str,
        manifest_path: str,
        excluded_paths: list[str],
    ) -> None:
        """Stage workspace edits and the cycle manifest without generated artifacts.

        Unstages config-derived excluded prefixes, stages tracked updates and
        untracked files that respect ``.gitignore``, then adds the manifest.
        Rejects commits if data, checkpoints, bytecode, or eval-zone paths remain
        in the index (including after agent ``git add -f``).
        """
        workdir_normalized = workdir.rstrip("/") or "."
        self.assert_no_excluded_staged_paths(excluded_paths=excluded_paths)
        self._unstage_excluded_paths(excluded_paths)
        if workdir_normalized in ("", "."):
            self._run(["add", "-u", "--", "."])
            untracked_scope = ["--others", "--exclude-standard"]
        else:
            self._run(["add", "-u", "--", workdir_normalized])
            untracked_scope = ["--others", "--exclude-standard", workdir_normalized]
        proc = subprocess.run(
            ["git", "ls-files", *untracked_scope],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            for path in proc.stdout.splitlines():
                candidate = path.strip()
                if not candidate:
                    continue
                if is_under_any(candidate, excluded_paths) or _is_workspace_bytecode_artifact(candidate):
                    continue
                self._run(["add", "--", candidate])
        self.assert_no_excluded_staged_paths(excluded_paths=excluded_paths)
        if manifest_path:
            manifest = Path(self.repo_root) / manifest_path
            if manifest.is_file():
                self._run(["add", "-f", manifest_path])

    def _unstage_excluded_paths(self, excluded_paths: list[str]) -> None:
        for excluded in excluded_paths:
            prefix = excluded.rstrip("/")
            if not prefix:
                continue
            subprocess.run(
                ["git", "reset", "HEAD", "--", prefix],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

    def assert_no_excluded_staged_paths(self, *, excluded_paths: list[str]) -> None:
        blocked = [
            path
            for path in self.changed_files(staged=True)
            if is_under_any(path, excluded_paths) or _is_workspace_bytecode_artifact(path)
        ]
        if blocked:
            raise GitServiceError(
                "staged paths include generated or read-only artifacts: "
                f"{sorted(blocked)}"
            )

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
            if not is_within(path, workdir):
                continue
            if is_under_any(path, excluded):
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
