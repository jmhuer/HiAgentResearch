from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from hiagentresearch.src.config import HiAgentResearchConfig, load_config
from hiagentresearch.src.gh_ingest import ingest
from hiagentresearch.src.git_service import GitService
from hiagentresearch.src.github_actions import GitHubActionsService, load_run_meta
from hiagentresearch.src.orchestrator import REPO_ROOT, init_state, run_group


class GitLike(Protocol):
    def checkout(self, branch: str) -> None: ...
    def checkout_or_create(self, branch: str, *, base_branch: str = "main") -> None: ...
    def stage_paths(self, paths: list[str]) -> None: ...
    def changed_files(self, *, staged: bool = False) -> list[str]: ...
    def has_core_staged_change(self, *, allowed_paths: list[str], supporting_paths: list[str]) -> bool: ...
    def commit(self, *, subject: str, body: str) -> str: ...
    def push(self, *, remote: str, branch: str) -> None: ...


class GitHubActionsLike(Protocol):
    def find_run_for_head(
        self,
        *,
        branch: str,
        head_sha: str,
        workflow_name: str,
        attempts: int,
        sleep_sec: float,
    ): ...
    def watch_run(self, run_id: str) -> bool: ...
    def download_artifacts(self, *, run_id: str, target_dir: Path, clean: bool = True) -> Path: ...
    def artifact_payload_dir(self, download_dir: Path) -> Path: ...


@dataclass(slots=True)
class CycleResult:
    loop_index: int
    local_run_id: str
    local_failure_class: str
    commit_sha: str
    github_run_id: str
    github_failure_class: str
    artifact_dir: str
    decision: str


@dataclass(slots=True)
class LoopSummary:
    ok: bool
    group_id: str
    branch: str
    cycles: list[CycleResult] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "group_id": self.group_id,
            "branch": self.branch,
            "reason": self.reason,
            "cycles": [asdict(cycle) for cycle in self.cycles],
        }


RunGroupCallable = Callable[..., int]
IngestCallable = Callable[[str, str, str, Path], int]


def run_loops(
    *,
    group_id: str,
    branch: str | None,
    loops: int,
    workdir: Path,
    quick: bool,
    evidence_path: Path | None,
    agent_model: str,
    config: HiAgentResearchConfig | None = None,
    git: GitLike | None = None,
    github: GitHubActionsLike | None = None,
    run_group_func: RunGroupCallable = run_group,
    ingest_func: IngestCallable = ingest,
    stop_on_success: bool = True,
) -> LoopSummary:
    loaded_config = config or load_config()
    group_config = loaded_config.group_by_id(group_id)
    target_branch = branch or group_config.branch
    git_service = git or GitService(REPO_ROOT)
    github_service = github or GitHubActionsService(REPO_ROOT)

    with contextlib.redirect_stdout(io.StringIO()):
        init_state()
    git_service.checkout_or_create(target_branch, base_branch="main")

    cycles: list[CycleResult] = []
    for loop_index in range(1, loops + 1):
        local = _run_group_capture(
            run_group_func,
            group_id=group_id,
            workdir=workdir,
            quick=quick,
            evidence_path=evidence_path,
            agent_model=agent_model,
        )
        local_run_id = str(local.get("run_id", ""))
        local_failure = str(local.get("failure_class", "invalid_cycle"))
        if not local_run_id:
            return LoopSummary(
                ok=False,
                group_id=group_id,
                branch=target_branch,
                cycles=cycles,
                reason="local cycle did not return a run_id",
            )
        if local_failure in {"invalid_cycle", "infra_failure"}:
            return LoopSummary(
                ok=False,
                group_id=group_id,
                branch=target_branch,
                cycles=cycles,
                reason=f"local cycle blocked with {local_failure}",
            )

        supporting_paths = [artifact.path for artifact in loaded_config.agent_contract.supporting_artifacts]
        git_service.stage_paths(list(group_config.allowed_paths))
        if not git_service.has_core_staged_change(
            allowed_paths=list(group_config.allowed_paths),
            supporting_paths=supporting_paths,
        ):
            return LoopSummary(
                ok=False,
                group_id=group_id,
                branch=target_branch,
                cycles=cycles,
                reason="cycle produced no staged core change",
            )

        subject = f"Phase1 loop {loop_index}: {group_id} planned experiment update."
        body = f"HiAgentResearch-Run-ID: {local_run_id}"
        commit_sha = git_service.commit(subject=subject, body=body)
        git_service.push(remote=loaded_config.github.remote, branch=target_branch)

        gh_run = github_service.find_run_for_head(
            branch=target_branch,
            head_sha=commit_sha,
            workflow_name=loaded_config.github.workflow_name,
            attempts=loaded_config.github.run_lookup_attempts,
            sleep_sec=loaded_config.github.run_lookup_sleep_sec,
        )
        github_service.watch_run(gh_run.database_id)
        download_dir = REPO_ROOT / ".hiagentresearch" / "state" / "github_runs" / gh_run.database_id
        github_service.download_artifacts(run_id=gh_run.database_id, target_dir=download_dir)
        artifact_dir = github_service.artifact_payload_dir(download_dir)
        meta = load_run_meta(artifact_dir)
        if str(meta.get("correlation_id", "")) != local_run_id:
            return LoopSummary(
                ok=False,
                group_id=group_id,
                branch=target_branch,
                cycles=cycles,
                reason="github artifact correlation_id did not match local run_id",
            )

        ingest_code = ingest_func(f"gh_{gh_run.database_id}", group_id, target_branch, artifact_dir)
        if ingest_code != 0:
            return LoopSummary(
                ok=False,
                group_id=group_id,
                branch=target_branch,
                cycles=cycles,
                reason="github artifact ingest failed",
            )

        failure = json.loads((artifact_dir / "failure_class.json").read_text(encoding="utf-8"))
        github_failure = str(failure.get("failure_class", "infra_failure"))
        decision = "done" if github_failure == "none" else ("repair" if github_failure == "code_failure" else "pivot")
        cycles.append(
            CycleResult(
                loop_index=loop_index,
                local_run_id=local_run_id,
                local_failure_class=local_failure,
                commit_sha=commit_sha,
                github_run_id=gh_run.database_id,
                github_failure_class=github_failure,
                artifact_dir=str(artifact_dir),
                decision=decision,
            )
        )
        if stop_on_success and decision == "done":
            return LoopSummary(ok=True, group_id=group_id, branch=target_branch, cycles=cycles, reason="quality met")

    ok = bool(cycles) and cycles[-1].github_failure_class == "none"
    reason = "max loops reached" if not ok else "requested loops completed"
    return LoopSummary(ok=ok, group_id=group_id, branch=target_branch, cycles=cycles, reason=reason)


def _run_group_capture(run_group_func: RunGroupCallable, **kwargs) -> dict:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = run_group_func(**kwargs)
    text = stdout.getvalue().strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {"ok": False, "error": "could not parse run_group output", "raw_stdout": text}
    payload["exit_code"] = exit_code
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run HiAgentResearch loops with backend-owned git/GitHub orchestration.")
    parser.add_argument("--group-id", default="model_architecture")
    parser.add_argument("--branch", default=None)
    parser.add_argument("--loops", type=int, default=3)
    parser.add_argument("--workdir", type=Path, default=REPO_ROOT)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--evidence-json", type=Path, default=REPO_ROOT / ".hiagentresearch/state/evidence/model_architecture.json")
    parser.add_argument("--agent-model", default="composer-2.5")
    parser.add_argument("--run-exact-loops", action="store_true", help="Do not stop early when quality is met.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_loops(
        group_id=args.group_id,
        branch=args.branch,
        loops=args.loops,
        workdir=args.workdir.resolve(),
        quick=args.quick,
        evidence_path=args.evidence_json.resolve() if args.evidence_json else None,
        agent_model=args.agent_model,
        stop_on_success=not args.run_exact_loops,
    )
    print(json.dumps(summary.to_dict(), indent=2))
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
