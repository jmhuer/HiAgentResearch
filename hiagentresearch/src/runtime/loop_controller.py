from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from hiagentresearch.src.core.coerce import as_string_list
from hiagentresearch.src.core.config import HiAgentResearchConfig, load_config
from hiagentresearch.src.github.ingest import ingest
from hiagentresearch.src.agents.credentials import ensure_cursor_api_key
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.git.worktree import WorktreeManager
from hiagentresearch.src.lineage.resolve import BranchBootstrap, resolve_branch_bootstrap
from hiagentresearch.src.github.actions import GitHubActionsService, load_run_meta
from hiagentresearch.src.runtime.baseline import ensure_baseline_snapshot, install_dependency_files
from hiagentresearch.src.paths import (
    REPO_ROOT,
    is_linked_git_worktree,
    resolve_execution_root,
    resolve_runs_dir,
    resolve_state_dir,
)
from hiagentresearch.src.runtime.orchestrator import init_state, run_group
from hiagentresearch.src.core.outcomes import (
    baseline_metrics_complete,
    normalize_research_outcome_name,
    outcome_met_targets,
    required_baseline_metrics,
)
from hiagentresearch.src.registry.store import Registry


class GitLike(Protocol):
    def checkout(self, branch: str) -> None: ...
    def checkout_or_create(
        self, branch: str, *, base_branch: str = "main", start_ref: str | None = None
    ) -> None: ...
    def stage_research_commit(
        self,
        *,
        workdir: str,
        manifest_path: str,
        excluded_paths: list[str],
    ) -> None: ...
    def changed_files(self, *, staged: bool = False) -> list[str]: ...
    def has_staged_workspace_change(
        self,
        *,
        workdir: str,
        generated_paths: list[str],
        reference_paths: list[str],
        hidden_paths: list[str],
    ) -> bool: ...
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
FAILED_RUNS_ROOT = Path(".hiagentresearch") / "failed-runs"
EXPERIMENT_MANIFEST_SCHEMA_VERSION = 1


@dataclass(slots=True)
class ExperimentManifest:
    """The per-cycle manifest committed to a research branch and ingested by the registry.

    This is the single source of truth for manifest field names; both the file on
    the branch and the registry row are derived from it via ``to_dict()``.
    """

    run_id: str
    group_id: str
    branch: str
    loop_index: int
    hypothesis_id: str
    hypothesis: str
    planned_code_changes: list[str]
    target_files: list[str]
    success_criteria: list[str]
    rollback_plan: str
    local_status: str
    local_failure_class: str
    local_research_outcome: str
    lineage_mode: str
    lineage_parent_group_id: str | None
    lineage_anchor_sha: str
    lineage_anchor_policy: str | None
    lineage_parent_anchor_step: int | None
    lineage_anchor_source_group: str | None
    lineage_baseline_snapshot: dict | None = None
    schema_version: int = EXPERIMENT_MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict:
        payload = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "group_id": self.group_id,
            "branch": self.branch,
            "loop_index": self.loop_index,
            "hypothesis_id": self.hypothesis_id,
            "hypothesis": self.hypothesis,
            "planned_code_changes": list(self.planned_code_changes),
            "target_files": list(self.target_files),
            "success_criteria": list(self.success_criteria),
            "rollback_plan": self.rollback_plan,
            "local_status": self.local_status,
            "local_failure_class": self.local_failure_class,
            "local_research_outcome": self.local_research_outcome,
            "lineage_mode": self.lineage_mode,
            "lineage_parent_group_id": self.lineage_parent_group_id,
            "lineage_anchor_sha": self.lineage_anchor_sha,
            "lineage_anchor_policy": self.lineage_anchor_policy,
            "lineage_parent_anchor_step": self.lineage_parent_anchor_step,
            "lineage_anchor_source_group": self.lineage_anchor_source_group,
        }
        if self.lineage_baseline_snapshot is not None:
            payload["lineage_baseline_snapshot"] = self.lineage_baseline_snapshot
        return payload


def run_loops(
    *,
    group_id: str,
    branch: str | None,
    loops: int,
    workdir: Path,
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
    git_root = resolve_execution_root(workdir)
    git_service = git or GitService(git_root)
    github_service = github or GitHubActionsService(REPO_ROOT)

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        install_dependency_files(loaded_config)
        init_state()
    registry = Registry(resolve_state_dir())
    registry.init()
    bootstrap = resolve_branch_bootstrap(
        group_config,
        loaded_config,
        registry=registry,
        git=git_service,
    )
    if not is_linked_git_worktree(git_root):
        git_service.checkout_or_create(
            target_branch,
            start_ref=bootstrap.start_ref,
            sync_to_ref=bootstrap.mode == "inherit",
        )

    cycles: list[CycleResult] = []
    for loop_index in range(1, loops + 1):
        local = _run_group_capture(
            run_group_func,
            group_id=group_id,
            workdir=git_root,
            agent_model=agent_model,
            lineage_bootstrap=bootstrap,
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
        if local.get("error") == "could not parse run_group output":
            return LoopSummary(
                ok=False,
                group_id=group_id,
                branch=target_branch,
                cycles=cycles,
                reason="could not parse run_group output",
            )
        if local_failure in {"invalid_cycle", "infra_failure"}:
            detail = str(local.get("error", "")).strip()
            reason = f"local cycle blocked with {local_failure}"
            if detail:
                reason = f"{reason}: {detail}"
            return LoopSummary(
                ok=False,
                group_id=group_id,
                branch=target_branch,
                cycles=cycles,
                reason=reason,
            )

        manifest_path, manifest = _write_experiment_manifest(
            group_id=group_id,
            branch=target_branch,
            loop_index=loop_index,
            local_run_id=local_run_id,
            local_result=local,
            bootstrap=bootstrap,
            checkout_root=git_root,
            baseline_snapshot=registry.baseline_snapshot(),
            required_metrics=required_baseline_metrics(loaded_config.evaluation.targets),
        )
        registry.record_experiment_manifest(
            run_id=local_run_id,
            manifest_path=manifest_path,
            manifest=manifest,
        )
        excluded_paths = loaded_config.commit_excluded_paths()
        git_service.stage_research_commit(
            workdir=loaded_config.workdir,
            manifest_path=manifest_path,
            excluded_paths=excluded_paths,
        )
        if not git_service.has_staged_workspace_change(
            workdir=loaded_config.workdir,
            generated_paths=loaded_config.generated_paths_resolved(),
            reference_paths=loaded_config.all_reference_paths(),
            hidden_paths=list(loaded_config.hidden_paths),
        ):
            return LoopSummary(
                ok=False,
                group_id=group_id,
                branch=target_branch,
                cycles=cycles,
                reason="cycle produced no staged workspace change",
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
        with tempfile.TemporaryDirectory(prefix=f"hiagentresearch-gh-{gh_run.database_id}-") as tmp:
            download_dir = Path(tmp)
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
            artifact_ref = f"github_actions:{gh_run.database_id}"
        github_failure = str(failure.get("failure_class", "infra_failure"))
        github_outcome = normalize_research_outcome_name(str(outcome.get("research_outcome", "unknown")))
        met_targets = outcome_met_targets(github_outcome)
        decision = str(
            outcome.get(
                "next_action",
                "done" if met_targets else ("repair" if github_failure == "code_failure" else "continue"),
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
                artifact_dir=artifact_ref,
                decision=decision,
            )
        )
        if stop_on_success and met_targets:
            return LoopSummary(ok=True, group_id=group_id, branch=target_branch, cycles=cycles, reason="targets met")

    ok = bool(cycles) and all(cycle.github_failure_class == "none" for cycle in cycles)
    reason = "requested loops completed" if ok else "max loops reached with execution blockers"
    return LoopSummary(ok=ok, group_id=group_id, branch=target_branch, cycles=cycles, reason=reason)


def _extract_last_json_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    idx = len(text)
    while idx > 0:
        idx = text.rfind("{", 0, idx)
        if idx < 0:
            return None
        try:
            obj, _end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx -= 1
            continue
        if isinstance(obj, dict):
            return obj
        idx -= 1
    return None


def _run_group_capture(run_group_func: RunGroupCallable, **kwargs) -> dict:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = run_group_func(**kwargs)
    text = stdout.getvalue().strip()
    payload = _extract_last_json_object(text)
    if payload is None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {
                "ok": False,
                "error": "could not parse run_group output",
                "failure_class": "invalid_cycle",
                "raw_stdout": text[-4000:],
            }
    payload["exit_code"] = exit_code
    return payload


def _write_experiment_manifest(
    *,
    group_id: str,
    branch: str,
    loop_index: int,
    local_run_id: str,
    local_result: dict,
    bootstrap: BranchBootstrap,
    checkout_root: Path,
    baseline_snapshot: dict | None = None,
    required_metrics: tuple[str, ...] = (),
) -> tuple[str, dict]:
    run_dir = resolve_runs_dir(checkout_root) / local_run_id
    intent = _read_json(run_dir / "experiment_intent.json")
    baseline_metrics = ((baseline_snapshot or {}).get("metrics") or {})
    required = required_metrics or required_baseline_metrics(None)
    lineage_baseline_snapshot = None
    if baseline_metrics_complete(baseline_metrics, required):
        lineage_baseline_snapshot = {
            "ref": str((baseline_snapshot or {}).get("ref") or "main"),
            "metrics": {str(name): float(value) for name, value in baseline_metrics.items()},
        }
    manifest = ExperimentManifest(
        run_id=local_run_id,
        group_id=group_id,
        branch=branch,
        loop_index=loop_index,
        hypothesis_id=intent.get("hypothesis_id", ""),
        hypothesis=intent.get("hypothesis", ""),
        planned_code_changes=as_string_list(intent.get("planned_code_changes")),
        target_files=as_string_list(intent.get("target_files")),
        success_criteria=as_string_list(intent.get("success_criteria")),
        rollback_plan=intent.get("rollback_plan", ""),
        local_status=local_result.get("status", ""),
        local_failure_class=local_result.get("failure_class", ""),
        local_research_outcome=normalize_research_outcome_name(
            str(local_result.get("research_outcome", "")),
        ),
        lineage_mode=bootstrap.mode,
        lineage_parent_group_id=bootstrap.parent_group_id,
        lineage_anchor_sha=bootstrap.start_ref,
        lineage_anchor_policy=bootstrap.anchor_policy,
        lineage_parent_anchor_step=bootstrap.parent_anchor_step,
        lineage_anchor_source_group=bootstrap.anchor_source_group_id,
        lineage_baseline_snapshot=lineage_baseline_snapshot,
    ).to_dict()
    path = EXPERIMENT_MANIFEST_ROOT / group_id / f"{local_run_id}.json"
    absolute_path = checkout_root / path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(path), manifest


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def run_loops_all(
    *,
    loops: int,
    workdir: Path,
    agent_model: str,
    config: HiAgentResearchConfig | None = None,
    stop_on_success: bool = True,
    parallel: bool = False,
) -> int:
    ensure_cursor_api_key()
    loaded_config = config or load_config()
    registry = Registry(resolve_state_dir())
    registry.init()
    ensure_baseline_snapshot(registry, loaded_config)
    summaries: list[LoopSummary] = []
    worktrees = WorktreeManager(worktree_root=loaded_config.orchestration.worktree_root)
    if parallel:
        GitService(REPO_ROOT).checkout("main")
    try:
        for wave in loaded_config.execution_waves():
            if parallel and len(wave) > 1:
                exit_code = _run_wave_parallel(
                    wave,
                    loops=loops,
                    agent_model=agent_model,
                    config=loaded_config,
                    stop_on_success=stop_on_success,
                    worktrees=worktrees,
                )
                if exit_code != 0:
                    print(json.dumps({"ok": False, "reason": "parallel wave failed", "summaries": []}, indent=2))
                    return exit_code
                continue
            for group_id in wave:
                summary = run_loops(
                    group_id=group_id,
                    branch=None,
                    loops=loops,
                    workdir=workdir,
                    agent_model=agent_model,
                    config=loaded_config,
                    stop_on_success=stop_on_success,
                )
                summaries.append(summary)
                if not summary.ok:
                    print(json.dumps({"ok": False, "summaries": [item.to_dict() for item in summaries]}, indent=2))
                    return 1
    finally:
        if parallel:
            worktrees.remove_all()
    print(json.dumps({"ok": True, "summaries": [item.to_dict() for item in summaries]}, indent=2))
    return 0


def _run_wave_parallel(
    wave: list[str],
    *,
    loops: int,
    agent_model: str,
    config: HiAgentResearchConfig,
    stop_on_success: bool,
    worktrees: WorktreeManager,
) -> int:
    registry = Registry(resolve_state_dir())
    registry.init()
    git_main = GitService(REPO_ROOT)
    processes: list[tuple[str, subprocess.Popen[str]]] = []
    output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    reader_threads: list[threading.Thread] = []
    for group_id in wave:
        group_config = config.group_by_id(group_id)
        bootstrap = resolve_branch_bootstrap(
            group_config,
            config,
            registry=registry,
            git=git_main,
        )
        worktree_path = worktrees.ensure(
            group_id,
            group_config.branch,
            start_ref=bootstrap.start_ref,
            sync_to_ref=bootstrap.mode == "inherit",
        )
        cmd = [
            sys.executable,
            "-m",
            "hiagentresearch.cli",
            "loops",
            "--group-id",
            group_id,
            "--loops",
            str(loops),
            "--workdir",
            str(worktree_path),
            "--agent-model",
            agent_model,
        ]
        if not stop_on_success:
            cmd.append("--run-exact-loops")
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append((group_id, proc))
        thread = threading.Thread(target=_stream_process_output, args=(group_id, proc, output_queue), daemon=True)
        thread.start()
        reader_threads.append(thread)

    completed_streams = 0
    while completed_streams < len(processes):
        group_id, line = output_queue.get()
        if line is None:
            completed_streams += 1
            continue
        print(f"[{group_id}] {line}", flush=True)

    for thread in reader_threads:
        thread.join(timeout=1)

    first_failure = 0
    for group_id, proc in processes:
        returncode = proc.wait()
        if returncode != 0 and first_failure == 0:
            first_failure = returncode or 1
            print(f"[{group_id}] failed with exit {returncode}", flush=True)
    if first_failure:
        preserved = _preserve_parallel_failure_artifacts(wave, worktrees)
        if preserved:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason": "preserved failed parallel run artifacts",
                        "artifacts": preserved,
                    },
                    indent=2,
                ),
                flush=True,
            )
        return first_failure
    return 0


def _preserve_parallel_failure_artifacts(wave: list[str], worktrees: WorktreeManager) -> list[str]:
    preserved: list[str] = []
    root = REPO_ROOT / FAILED_RUNS_ROOT
    for group_id in wave:
        worktree_path = worktrees.path_for(group_id)
        runs_root = worktree_path / ".hiagentresearch" / "runs"
        if not runs_root.exists():
            continue
        for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
            target = root / group_id / run_dir.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(run_dir, target)
            _write_worktree_snapshot(worktree_path=worktree_path, target=target)
            preserved.append(str(target.relative_to(REPO_ROOT)))
    return preserved


def _write_worktree_snapshot(*, worktree_path: Path, target: Path) -> None:
    for filename, args in {
        "worktree_status.txt": ["status", "--short"],
        "worktree_diff.patch": ["diff", "--"],
    }.items():
        proc = subprocess.run(
            ["git", "-C", str(worktree_path), *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        content = proc.stdout if proc.returncode == 0 else proc.stderr
        (target / filename).write_text(content, encoding="utf-8")


def _stream_process_output(
    group_id: str,
    proc: subprocess.Popen[str],
    output_queue: queue.Queue[tuple[str, str | None]],
) -> None:
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            output_queue.put((group_id, line.rstrip("\n")))
    finally:
        proc.stdout.close()
        output_queue.put((group_id, None))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run HiAgentResearch loops with backend-owned git/GitHub orchestration.")
    parser.add_argument("--group-id", default="model_architecture")
    parser.add_argument("--branch", default=None)
    parser.add_argument("--loops", type=int, default=3)
    parser.add_argument("--workdir", type=Path, default=REPO_ROOT)
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
        agent_model=args.agent_model,
        stop_on_success=not args.run_exact_loops,
    )
    print(json.dumps(summary.to_dict(), indent=2))
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
