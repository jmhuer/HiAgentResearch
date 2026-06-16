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
    def __init__(self, repo_root: Path, *, repo: str | None = None) -> None:
        self.repo_root = repo_root.resolve()
        # gh --repo target ([HOST/]OWNER/REPO). When set, every gh call acts on this
        # repo explicitly, so the tool targets the configured remote regardless of how
        # many git remotes exist locally. None => gh's own default-repo resolution.
        self.repo = repo or None

    def _repo_args(self) -> list[str]:
        return ["--repo", self.repo] if self.repo else []

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
                ["gh", "run", "view", run_id, "--json", "status,conclusion", *self._repo_args()],
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
        # actions/upload-artifact@v4 artifacts can take a while to become downloadable after
        # the run completes — `gh run download` then reports "no valid artifacts found" for a
        # run that actually succeeded. That marker is transient, so retry on a longer budget
        # here (~2.5 min) than the default gh call to ride out propagation lag and recover the
        # real result, rather than burning the cycle as an infra_failure.
        self._run(
            ["run", "download", run_id, "--dir", str(target_dir)],
            retries=8,
            backoff_sec=4.0,
        )
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
                ["gh", *args, *self._repo_args()],
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
    "no valid artifacts found",
)


def _is_transient_gh_error(stderr: str) -> bool:
    """True for network/server hiccups that are safe to retry (not bad-command/auth errors)."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _TRANSIENT_GH_ERROR_MARKERS)


def gh_repo_slug(repo_root: Path, remote: str) -> str:
    """Resolve the ``gh --repo`` target from a git remote's URL.

    Returns ``OWNER/REPO`` for github.com or ``HOST/OWNER/REPO`` for GitHub
    Enterprise (e.g. ``github.disney.com/InformationAdvantage-AIML/HiAgentResearch``),
    so the tool acts on the configured repo no matter how many remotes exist. This
    is for LOCAL orchestration; CI uses gh's own default repo (the configured remote
    name does not exist in a CI checkout). Fails fast if the remote is unknown.
    """
    proc = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GitHubActionsError(
            f"git remote get-url {remote!r} failed (is github.remote configured and the "
            f"remote added?): {proc.stderr.strip()}"
        )
    host, owner_repo = _parse_remote_url(proc.stdout.strip())
    if not owner_repo:
        raise GitHubActionsError(f"could not parse owner/repo from remote {remote!r} url")
    if host in ("", "github.com"):
        return owner_repo
    return f"{host}/{owner_repo}"


def _parse_remote_url(url: str) -> tuple[str, str]:
    """Split a git remote URL into (host, owner/repo). Handles scp-style SSH,
    ssh://, and https:// forms, with or without a trailing .git."""
    u = url.strip()
    if u.endswith(".git"):
        u = u[:-4]
    if u.startswith("git@"):  # scp-style: git@host:owner/repo
        host, _, path = u[len("git@"):].partition(":")
        return host, path.strip("/")
    for scheme in ("ssh://", "https://", "http://"):
        if u.startswith(scheme):
            u = u[len(scheme):]
            break
    if u.startswith("git@"):  # ssh://git@host/owner/repo
        u = u[len("git@"):]
    if "@" in u.split("/", 1)[0]:  # user@host/owner/repo
        u = u.split("@", 1)[1]
    host, _, path = u.partition("/")
    return host, path.strip("/")


def load_run_meta(artifact_dir: Path) -> dict[str, Any]:
    return json.loads((artifact_dir / "run_meta.json").read_text(encoding="utf-8"))
