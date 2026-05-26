from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from hiagentresearch.src.config import HiAgentResearchConfig, load_config
from hiagentresearch.src.gh_ingest import ingest
from hiagentresearch.src.git_service import GitService
from hiagentresearch.src.github_actions import GitHubActionsService, load_run_meta
from hiagentresearch.src.orchestrator import REPO_ROOT, init_state, run_group
from hiagentresearch.src.registry import Registry


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
    github_research_outcome: str
    improved_baseline: bool
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
EXPERIMENT_MANIFEST_ROOT = Path(".hiagentresearch") / "experiments"


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

    _install_dependency_files(loaded_config)
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

        manifest_path, manifest = _write_experiment_manifest(
            group_id=group_id,
            branch=target_branch,
            loop_index=loop_index,
            local_run_id=local_run_id,
            local_result=local,
        )
        registry = Registry(REPO_ROOT / ".hiagentresearch" / "state")
        registry.init()
        registry.record_experiment_manifest(
            run_id=local_run_id,
            manifest_path=manifest_path,
            manifest=manifest,
        )
        supporting_paths = [artifact.path for artifact in loaded_config.agent_contract.supporting_artifacts]
        git_service.stage_paths([*list(group_config.allowed_paths), manifest_path])
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

        subject = _commit_subject(loop_index=loop_index, manifest=manifest)
        body = _commit_body(local_run_id=local_run_id, manifest_path=manifest_path, manifest=manifest)
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
        outcome = json.loads((artifact_dir / "research_outcome.json").read_text(encoding="utf-8"))
        github_failure = str(failure.get("failure_class", "infra_failure"))
        github_outcome = str(outcome.get("research_outcome", "unknown"))
        improved_baseline = bool(outcome.get("improved_baseline", False))
        decision = str(
            outcome.get(
                "next_action",
                "done" if improved_baseline else ("repair" if github_failure == "code_failure" else "continue"),
            )
        )
        cycles.append(
            CycleResult(
                loop_index=loop_index,
                local_run_id=local_run_id,
                local_failure_class=local_failure,
                commit_sha=commit_sha,
                github_run_id=gh_run.database_id,
                github_failure_class=github_failure,
                github_research_outcome=github_outcome,
                improved_baseline=improved_baseline,
                artifact_dir=str(artifact_dir),
                decision=decision,
            )
        )
        if stop_on_success and improved_baseline:
            return LoopSummary(ok=True, group_id=group_id, branch=target_branch, cycles=cycles, reason="baseline improved")

    ok = bool(cycles) and all(cycle.github_failure_class == "none" for cycle in cycles)
    reason = "requested loops completed" if ok else "max loops reached with execution blockers"
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


def _install_dependency_files(config: HiAgentResearchConfig) -> None:
    for dependency_file in config.dependency_file_paths(REPO_ROOT):
        if not dependency_file.exists():
            raise FileNotFoundError(f"configured dependency file does not exist: {dependency_file}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(dependency_file)],
            cwd=REPO_ROOT,
            check=True,
        )


def _write_experiment_manifest(
    *,
    group_id: str,
    branch: str,
    loop_index: int,
    local_run_id: str,
    local_result: dict,
) -> tuple[str, dict]:
    run_dir = REPO_ROOT / ".hiagentresearch" / "runs" / local_run_id
    intent = _read_json(run_dir / "experiment_intent.json")
    manifest = {
        "schema_version": 1,
        "run_id": local_run_id,
        "group_id": group_id,
        "branch": branch,
        "loop_index": loop_index,
        "hypothesis_id": intent.get("hypothesis_id", ""),
        "hypothesis": intent.get("hypothesis", ""),
        "planned_code_changes": _as_string_list(intent.get("planned_code_changes")),
        "target_files": _as_string_list(intent.get("target_files")),
        "success_criteria": _as_string_list(intent.get("success_criteria")),
        "rollback_plan": intent.get("rollback_plan", ""),
        "local_status": local_result.get("status", ""),
        "local_failure_class": local_result.get("failure_class", ""),
        "local_research_outcome": local_result.get("research_outcome", ""),
        "local_improved_baseline": bool(local_result.get("improved_baseline", False)),
    }
    path = EXPERIMENT_MANIFEST_ROOT / group_id / f"{local_run_id}.json"
    absolute_path = REPO_ROOT / path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(path), manifest


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _commit_subject(*, loop_index: int, manifest: dict) -> str:
    summary = _manifest_summary(manifest)
    return f"Phase 1, loop {loop_index}: {summary}"


def _commit_body(*, local_run_id: str, manifest_path: str, manifest: dict) -> str:
    lines = [f"HiAgentResearch-Run-ID: {local_run_id}", f"Experiment-Manifest: {manifest_path}"]
    hypothesis_id = str(manifest.get("hypothesis_id", "")).strip()
    if hypothesis_id:
        lines.append(f"Hypothesis-ID: {hypothesis_id}")
    return "\n".join(lines)


def _manifest_summary(manifest: dict) -> str:
    planned = manifest.get("planned_code_changes", [])
    if isinstance(planned, list) and planned:
        text = str(planned[0])
    else:
        text = str(manifest.get("hypothesis_id") or manifest.get("hypothesis") or "experiment update")
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    text = re.sub(r"^in\s+[^:]+:\s*", "", text, flags=re.IGNORECASE)
    if len(text) > 72:
        text = text[:69].rstrip() + "..."
    return text or "experiment update"


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
