from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GitHubActionsError(RuntimeError):
    """Raised when a GitHub Actions operation fails."""


@dataclass(slots=True)
class GitHubRun:
    database_id: str
    head_sha: str
    name: str
    status: str
    created_at: str = ""


class GitHubActionsService:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()

    def find_run_for_head(
        self,
        *,
        branch: str,
        head_sha: str,
        workflow_name: str,
        attempts: int,
        sleep_sec: float,
    ) -> GitHubRun:
        for _ in range(attempts):
            for run in self.list_runs(branch=branch):
                if run.head_sha == head_sha and run.name == workflow_name:
                    return run
            time.sleep(sleep_sec)
        raise GitHubActionsError(f"no GitHub Actions run found for {head_sha} on {branch}")

    def list_runs(self, *, branch: str, limit: int = 20) -> list[GitHubRun]:
        result = self._run(
            [
                "run",
                "list",
                "--branch",
                branch,
                "--limit",
                str(limit),
                "--json",
                "databaseId,headSha,name,status,createdAt",
            ]
        )
        payload = json.loads(result)
        if not isinstance(payload, list):
            raise GitHubActionsError("gh run list returned non-list JSON")
        return [
            GitHubRun(
                database_id=str(item.get("databaseId", "")),
                head_sha=str(item.get("headSha", "")),
                name=str(item.get("name", "")),
                status=str(item.get("status", "")),
                created_at=str(item.get("createdAt", "")),
            )
            for item in payload
        ]

    def dispatch_workflow(self, *, workflow_name: str, ref: str, inputs: dict[str, str]) -> None:
        args = ["workflow", "run", workflow_name, "--ref", ref]
        for key, value in inputs.items():
            args.extend(["-f", f"{key}={value}"])
        self._run(args)

    def find_new_run_for_head(
        self,
        *,
        branch: str,
        head_sha: str,
        workflow_name: str,
        known_run_ids: set[str],
        attempts: int,
        sleep_sec: float,
    ) -> GitHubRun:
        for _ in range(attempts):
            candidates = [
                run
                for run in self.list_runs(branch=branch)
                if run.head_sha == head_sha
                and run.name == workflow_name
                and run.database_id not in known_run_ids
            ]
            if candidates:
                return candidates[0]
            time.sleep(sleep_sec)
        raise GitHubActionsError(f"no new GitHub Actions run found for {head_sha} on {branch}")

    def watch_run(self, run_id: str, *, poll_sec: float = 10.0, max_wait_sec: float = 2700.0) -> bool:
        """Block until a run reaches a terminal status, returning True on success.

        Polls ``gh run view`` rather than ``gh run watch``: the streaming watch can hang
        indefinitely even after a run has completed, which stalls the whole loop.
        """
        deadline = time.monotonic() + max_wait_sec
        while time.monotonic() < deadline:
            proc = subprocess.run(
                ["gh", "run", "view", run_id, "--json", "status,conclusion"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                payload = json.loads(proc.stdout or "{}")
                if payload.get("status") == "completed":
                    return payload.get("conclusion") == "success"
            time.sleep(poll_sec)
        return False

    def download_artifacts(self, *, run_id: str, target_dir: Path, clean: bool = True) -> Path:
        if clean and target_dir.exists():
            import shutil

            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        self._run(["run", "download", run_id, "--dir", str(target_dir)])
        return target_dir

    def artifact_payload_dir(self, download_dir: Path) -> Path:
        candidates = sorted(path for path in download_dir.glob("hiagentresearch-*") if path.is_dir())
        if not candidates:
            raise GitHubActionsError(f"no hiagentresearch artifact directory found in {download_dir}")
        return candidates[0]

    def _run(self, args: list[str], *, retries: int = 4, backoff_sec: float = 3.0) -> str:
        last_error = ""
        for attempt in range(retries + 1):
            proc = subprocess.run(
                ["gh", *args],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                return proc.stdout
            last_error = proc.stderr.strip()
            if attempt < retries and _is_transient_gh_error(last_error):
                time.sleep(backoff_sec * (attempt + 1))
                continue
            break
        raise GitHubActionsError(f"gh {' '.join(args)} failed: {last_error}")


_TRANSIENT_GH_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "tls handshake",
    "connection reset",
    "connection refused",
    "temporary failure",
    "eof",
    "no such host",
    "i/o timeout",
    "503",
    "502",
    "server error",
)


def _is_transient_gh_error(stderr: str) -> bool:
    """True for network/server hiccups that are safe to retry (not bad-command/auth errors)."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _TRANSIENT_GH_ERROR_MARKERS)


def load_run_meta(artifact_dir: Path) -> dict[str, Any]:
    return json.loads((artifact_dir / "run_meta.json").read_text(encoding="utf-8"))
