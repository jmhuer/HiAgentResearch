"""Shared baseline-snapshot helpers used by the loop controller and dashboard build.

Computing the frozen L0 baseline (and installing eval dependencies) is a runtime/eval
concern, not a dashboard concern. Keeping it here lets both callers share one
implementation without the dashboard reaching into loop-controller internals.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from hiagentresearch.src.core.config import HiAgentResearchConfig
from hiagentresearch.src.core.outcomes import baseline_metrics_complete, required_baseline_metrics
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.github.actions import GitHubActionsService, gh_repo_slug
from hiagentresearch.src.github.ingest import record_baseline_snapshot_from_metrics
from hiagentresearch.src.paths import REPO_ROOT
from hiagentresearch.src.registry.store import Registry


def install_dependency_files(config: HiAgentResearchConfig) -> None:
    for dependency_file in config.dependency_file_paths(REPO_ROOT):
        if not dependency_file.exists():
            raise FileNotFoundError(f"configured dependency file does not exist: {dependency_file}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(dependency_file)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )


def ensure_baseline_snapshot(
    registry: Registry,
    config: HiAgentResearchConfig,
    *,
    github: GitHubActionsService | None = None,
    git: GitService | None = None,
) -> None:
    required = required_baseline_metrics(config.evaluation.targets)
    existing = registry.baseline_snapshot()
    if existing and baseline_metrics_complete(existing.get("metrics") or {}, required):
        return
    anchor_group = next((group.id for group in config.research_groups), "model_architecture")
    ref = config.orchestration.baseline_ref
    git_service = git or GitService(REPO_ROOT)
    github_service = github or GitHubActionsService(
        REPO_ROOT, repo=gh_repo_slug(REPO_ROOT, config.github.remote)
    )
    head_sha = git_service.resolve_ref(ref)
    known_run_ids = {
        run.database_id
        for run in github_service.list_runs(branch=ref)
        if run.name == config.github.workflow_name
    }
    correlation_id = f"baseline_{head_sha[:12]}"
    github_service.dispatch_workflow(
        workflow_name=config.github.workflow_name,
        ref=ref,
        inputs={
            "node_kind": "baseline",
            "group_id": anchor_group,
            "correlation_id": correlation_id,
        },
    )
    run = github_service.find_new_run_for_head(
        branch=ref,
        head_sha=head_sha,
        workflow_name=config.github.workflow_name,
        known_run_ids=known_run_ids,
        attempts=config.github.run_lookup_attempts,
        sleep_sec=config.github.run_lookup_sleep_sec,
    )
    if not github_service.watch_run(run.database_id):
        raise RuntimeError(f"baseline eval GitHub Actions run failed: {run.database_id}")
    with tempfile.TemporaryDirectory(prefix="hiagentresearch-baseline-") as tmp:
        download_dir = github_service.download_artifacts(
            run_id=run.database_id,
            target_dir=Path(tmp),
            clean=True,
        )
        artifact_dir = github_service.artifact_payload_dir(download_dir)
        failure = json.loads((artifact_dir / "failure_class.json").read_text(encoding="utf-8"))
        if failure.get("failure_class") != "none":
            raise RuntimeError(f"baseline eval failed with {failure.get('failure_class')}: {run.database_id}")
        metrics = json.loads((artifact_dir / "metrics.json").read_text(encoding="utf-8"))
        record_baseline_snapshot_from_metrics(
            registry, ref=ref, metrics=metrics, commit_sha=head_sha, required=required
        )
    if not registry.baseline_snapshot():
        raise RuntimeError(f"baseline eval did not produce complete baseline metrics: {run.database_id}")
