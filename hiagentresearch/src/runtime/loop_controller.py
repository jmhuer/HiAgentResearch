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
from hiagentresearch.src.core.guidance import materialize_framework_guidance
from hiagentresearch.src.github.ingest import ingest
from hiagentresearch.src.agents.credentials import ensure_cursor_api_key
from hiagentresearch.src.agents.task_contract import task_contract
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.git.worktree import WorktreeManager
from hiagentresearch.src.lineage.resolve import BranchBootstrap, resolve_branch_bootstrap
from hiagentresearch.src.github.actions import GitHubActionsService, gh_repo_slug, load_run_meta
from hiagentresearch.src.runtime.baseline import ensure_baseline_snapshot, install_dependency_files
from hiagentresearch.src.project.docs import write_workspace_agents
from hiagentresearch.src.paths import (
    REPO_ROOT,
    is_linked_git_worktree,
    resolve_execution_root,
    resolve_runs_dir,
    resolve_state_dir,
)
from hiagentresearch.src.runtime.orchestrator import run_group
from hiagentresearch.src.core.ci_result import CIResult
from hiagentresearch.src.core.json_io import extract_last_json_object, read_json_object
from hiagentresearch.src.core.outcomes import (
    baseline_metrics_complete,
    normalize_research_outcome_name,
    required_baseline_metrics,
)
from hiagentresearch.src.registry.store import Registry
from hiagentresearch.src.core.models import utc_now_iso


# A cycle whose Cursor agent run fails TRANSIENTLY (the SDK run terminates with status=error —
# infra flakiness, not a research signal) is retried from a clean worktree this many times total,
# so one hiccup does not abort the leaf and cascade into aborting the whole parallel wave. Genuine
# blocks (agent_moved_head, deterministic invalid cycles) are never retried. Env-overridable.
_CYCLE_TRANSIENT_RETRIES = max(1, int(os.environ.get("HIAGENTRESEARCH_CYCLE_TRANSIENT_RETRIES", "3")))


def _is_transient_cycle_failure(local: dict, *, run_dir: Path | None = None) -> bool:
    """A transient agent-infra failure worth retrying: SDK status=error, or the agent
  finished without writing cycle_intent.json (empty run). Deterministic contract blocks
  that produced intent are not retried."""
    if str(local.get("failure_class", "")) != "invalid_cycle":
        return False
    if str(local.get("cursor_run_status", "")).strip().lower() == "error":
        return True
    return run_dir is not None and not (run_dir / "cycle_intent.json").is_file()


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
CYCLE_MANIFEST_ROOT = Path(".hiagentresearch") / "cycles"
FAILED_RUNS_ROOT = Path(".hiagentresearch") / "failed-runs"
CYCLE_MANIFEST_SCHEMA_VERSION = 1


@dataclass(slots=True)
class CycleManifest:
    """The per-cycle manifest committed to a research branch and ingested by the registry.

    This is the single source of truth for manifest field names; both the file on
    the branch and the registry row are derived from it via ``to_dict()``.
    """

    run_id: str
    group_id: str
    branch: str
    loop_index: int
    goal_id: str
    goal: str
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
    merge_plan: dict | None = None
    merge_cycle_provenance: dict | None = None
    lineage_baseline_snapshot: dict | None = None
    schema_version: int = CYCLE_MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict:
        payload = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "group_id": self.group_id,
            "branch": self.branch,
            "loop_index": self.loop_index,
            "goal_id": self.goal_id,
            "goal": self.goal,
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
        if self.merge_plan is not None:
            payload["merge_plan"] = self.merge_plan
        if self.merge_cycle_provenance is not None:
            payload["merge_cycle_provenance"] = self.merge_cycle_provenance
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
    # A group may override the run's loop budget (the area desugar sets a select collapse
    # to 0 — adopt the strongest leaf with no agent cycles).
    effective_loops = group_config.loops if group_config.loops is not None else loops
    target_branch = branch or group_config.branch
    git_root = resolve_execution_root(workdir)
    git_service = git or GitService(git_root)
    github_service = github or GitHubActionsService(
        REPO_ROOT, repo=gh_repo_slug(REPO_ROOT, loaded_config.github.remote)
    )

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        install_dependency_files(loaded_config)
        _init_execution_state(loaded_config, git_root)
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

    if effective_loops == 0:
        # Select collapse: no agent cycles. The branch points at the adopted result (the
        # strongest leaf's commit, or the baseline when no leaf beat it). Record that adopted
        # point as a first-class trajectory node so a downstream inherit resolves THROUGH the
        # collapse to the real commit/step/owner — otherwise the collapse is a ghost with no
        # registry trace and inherit falls back to baseline regardless of what was adopted.
        registry.record_cycle_manifest(
            run_id=f"collapse_{group_id}",
            manifest_path="",
            manifest={
                "group_id": group_id,
                "branch": target_branch,
                "loop_index": 0,
                "goal_id": f"{group_id}-adopt",
                "goal": "select collapse: adopted strongest leaf",
                "target_files": [],
                "planned_code_changes": [],
                "lineage_mode": "inherit",
                "lineage_parent_group_id": bootstrap.parent_group_id,
                "lineage_anchor_sha": bootstrap.start_ref,
                "lineage_anchor_policy": bootstrap.anchor_policy,
                "lineage_parent_anchor_step": bootstrap.parent_anchor_step,
                "lineage_anchor_source_group": bootstrap.anchor_source_group_id,
                "merge_plan": _merge_plan_snapshot(bootstrap, resolved_at=utc_now_iso()),
            },
        )
        return LoopSummary(
            ok=True,
            group_id=group_id,
            branch=target_branch,
            cycles=[],
            reason="select collapse: adopted strongest leaf (zero integration loops)",
        )

    cycles: list[CycleResult] = []
    for loop_index in range(1, effective_loops + 1):
        # Retry a transient agent-infra failure from a clean worktree before giving up — a
        # single Cursor SDK hiccup must not abort the leaf and cascade into a wave abort.
        for attempt in range(1, _CYCLE_TRANSIENT_RETRIES + 1):
            local = _run_group_capture(
                run_group_func,
                group_id=group_id,
                workdir=git_root,
                agent_model=agent_model,
                lineage_bootstrap=bootstrap,
                loop_index=loop_index,
                loops=loops,
            )
            transient_run_id = str(local.get("run_id", ""))
            transient_run_dir = (
                resolve_runs_dir(git_root) / transient_run_id if transient_run_id else None
            )
            if (
                not _is_transient_cycle_failure(local, run_dir=transient_run_dir)
                or attempt >= _CYCLE_TRANSIENT_RETRIES
            ):
                break
            # The failed attempt may have left a partial edit; reset to HEAD (the loop's start,
            # since a blocked cycle does not commit) so the retry runs on a clean slate.
            with contextlib.suppress(Exception):
                git_service.discard_worktree_changes()
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
        if local_failure in {"invalid_cycle", "infra_failure", "agent_moved_head"}:
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

        manifest_path, manifest = _write_cycle_manifest(
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
        registry.record_cycle_manifest(
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
            artifact_ref = f"github_actions:{gh_run.database_id}"
            if not (artifact_dir / "run_meta.json").is_file():
                # Incomplete CI bundle: the eval job crashed before writing a full result
                # (no run_meta.json) yet still uploaded a partial artifact. Treat it as an
                # infra_failure cycle — preserve what we got and steer repair — instead of
                # crashing run_loops on the unreadable bundle and aborting the whole wave.
                # This also lets later loops of this group retry rather than dying outright.
                _preserve_ci_artifacts(
                    checkout_root=git_root, local_run_id=local_run_id, artifact_dir=artifact_dir
                )
                github_failure = "infra_failure"
                github_outcome = "execution_blocked"
                met_targets = False
                decision = "repair"
                intent = registry.read_intent_packet(group_id)
                if intent is not None:
                    intent.last_failure_class = github_failure
                    intent.next_action = decision
                    intent.last_note = (
                        f"CI eval run {gh_run.database_id} produced an incomplete artifact bundle "
                        "(missing run_meta.json); the eval job likely crashed before reporting a "
                        "result. Repair the change so the eval runs to completion."
                    )
                    intent.updated_at = utc_now_iso()
                    registry.write_intent_packet(intent)
            else:
                meta = load_run_meta(artifact_dir)
                if str(meta.get("correlation_id", "")) != local_run_id:
                    return LoopSummary(
                        ok=False,
                        group_id=group_id,
                        branch=target_branch,
                        cycles=cycles,
                        reason="github artifact correlation_id did not match local run_id",
                    )

                ci_dir = _preserve_ci_artifacts(
                    checkout_root=git_root,
                    local_run_id=local_run_id,
                    artifact_dir=artifact_dir,
                )

                ingest_code = ingest_func(f"gh_{gh_run.database_id}", group_id, target_branch, ci_dir)
                if ingest_code != 0:
                    return LoopSummary(
                        ok=False,
                        group_id=group_id,
                        branch=target_branch,
                        cycles=cycles,
                        reason="github artifact ingest failed",
                    )

                ci = CIResult.from_ci_dir(ci_dir)
                github_failure = ci.failure_class
                github_outcome = ci.research_outcome
                met_targets = ci.met_targets
                decision = ci.decision()
                # Engineering tasks must PRESERVE the metrics they inherited (the score is a
                # guardrail, not the goal). If this commit dropped a metric below that floor it
                # is a regression: not a hard failure (the loop continues so a later cycle can
                # fix it), but we steer the next cycle to repair it with a specific note.
                regression_note = ""
                if task_contract(group_config.task_kind).preserve_metrics:
                    regression_note = _metric_regression_note(
                        registry=registry,
                        bootstrap=bootstrap,
                        metric=group_config.lineage.anchor_metric,
                        current=ci.metrics.get(group_config.lineage.anchor_metric),
                        minimize=loaded_config.evaluation.metric_minimizes(group_config.lineage.anchor_metric),
                    )
                    if regression_note:
                        decision = "repair"
                steering_note = _diagnostics_steering_note(ci_dir, ci)
                feedback_ref = _path_relative_to(ci_dir, git_root)
                # Feed the authoritative CI outcome back into the intent packet so the next
                # cycle's agent prompt reflects how this change actually scored (last failure
                # class, next action, and a pointer to diagnostic artifacts). The local cycle no
                # longer runs an eval, so CI is the only source of this feedback.
                intent = registry.read_intent_packet(group_id)
                if intent is not None:
                    intent.last_failure_class = github_failure
                    intent.next_action = decision
                    intent.last_note = regression_note or steering_note
                    intent.last_feedback_ref = feedback_ref
                    intent.updated_at = utc_now_iso()
                    registry.write_intent_packet(intent)
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

    # A group succeeds if it produced at least one clean, committable result. A
    # code_failure cycle is a discarded attempt (no metric, already excluded from lineage
    # by the registry's failure_class filter) that the next loop is steered to repair —
    # the same non-terminal treatment a metric regression already gets. Only an all-failure
    # group (no clean result left to inherit or merge) is a genuine dead-end that fails the
    # group and aborts the wave.
    ok = any(cycle.github_failure_class == "none" for cycle in cycles)
    reason = "requested loops completed" if ok else "max loops reached without a clean result"
    return LoopSummary(ok=ok, group_id=group_id, branch=target_branch, cycles=cycles, reason=reason)


def _init_execution_state(config: HiAgentResearchConfig, checkout_root: Path) -> None:
    resolve_state_dir().mkdir(parents=True, exist_ok=True)
    resolve_runs_dir(checkout_root).mkdir(parents=True, exist_ok=True)
    Registry(resolve_state_dir()).init()
    materialize_framework_guidance(root=checkout_root)
    write_workspace_agents(config, root=checkout_root)


def _metric_regression_note(
    *,
    registry: Registry,
    bootstrap: BranchBootstrap,
    metric: str,
    current: object,
    minimize: bool,
) -> str:
    """Return a repair note if ``current`` regressed below the inherited floor, else "".

    The floor an engineering group must preserve is the metric value of the commit it
    branched from: the inherited anchor for an inherit group, else the frozen baseline.
    """
    if not isinstance(current, (int, float)):
        return ""
    floor = None
    if bootstrap.mode == "inherit" and bootstrap.anchor_source_group_id and bootstrap.start_ref:
        floor = registry.metric_for_group_commit(
            bootstrap.anchor_source_group_id, bootstrap.start_ref, metric
        )
    if floor is None:
        floor = ((registry.baseline_snapshot() or {}).get("metrics") or {}).get(metric)
    if floor is None:
        return ""
    regressed = float(current) > float(floor) if minimize else float(current) < float(floor)
    if not regressed:
        return ""
    return (
        f"Your last change regressed {metric} ({float(floor):g} → {float(current):g}); this metric "
        "must be preserved. Restore it (revert or fix the offending change) while keeping the quality improvement."
    )


def _preserve_ci_artifacts(
    *,
    checkout_root: Path,
    local_run_id: str,
    artifact_dir: Path,
) -> Path:
    ci_dir = resolve_runs_dir(checkout_root) / local_run_id / "ci"
    if ci_dir.exists():
        shutil.rmtree(ci_dir)
    shutil.copytree(artifact_dir, ci_dir)
    return ci_dir


def _diagnostics_steering_note(ci_dir: Path, ci: CIResult) -> str:
    # A clean-but-below-targets run has no failure to diagnose; summarize its outcome.
    if not ci.execution_blocked and ci.research_outcome == "below_targets":
        return _ci_feedback_summary(ci, reason=ci.reason)
    diagnostics_path = ci_dir / "diagnostics.json"
    if diagnostics_path.is_file():
        diagnostics = read_json_object(diagnostics_path)
        summary = str(diagnostics.get("summary") or "").strip()
        if summary:
            return _trim_feedback_reason(summary)
    return _ci_feedback_summary(ci, reason=ci.first_reason())


def _ci_feedback_summary(ci: CIResult, *, reason: str) -> str:
    if ci.execution_blocked:
        base = f"CI eval blocked execution with {ci.failure_class}"
    else:
        base = f"CI eval completed with outcome {ci.research_outcome}"
    if reason:
        return f"{base}: {_trim_feedback_reason(reason)}"
    return f"{base}."


def _trim_feedback_reason(reason: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(reason)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _path_relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _run_group_capture(run_group_func: RunGroupCallable, **kwargs) -> dict:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = run_group_func(**kwargs)
    text = stdout.getvalue().strip()
    payload = extract_last_json_object(text)
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


def _write_cycle_manifest(
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
    intent = read_json_object(run_dir / "cycle_intent.json")
    baseline_metrics = ((baseline_snapshot or {}).get("metrics") or {})
    required = required_metrics or required_baseline_metrics(None)
    lineage_baseline_snapshot = None
    if baseline_metrics_complete(baseline_metrics, required):
        lineage_baseline_snapshot = {
            "ref": str((baseline_snapshot or {}).get("ref") or "main"),
            "commit_sha": str((baseline_snapshot or {}).get("commit_sha") or ""),
            "metrics": {str(name): float(value) for name, value in baseline_metrics.items()},
        }
    manifest = CycleManifest(
        run_id=local_run_id,
        group_id=group_id,
        branch=branch,
        loop_index=loop_index,
        goal_id=intent.get("goal_id", ""),
        goal=intent.get("goal", ""),
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
        merge_plan=_merge_plan_snapshot(bootstrap, resolved_at=utc_now_iso()),
        merge_cycle_provenance=_merge_cycle_provenance(bootstrap, group_id=group_id, loop_index=loop_index),
        lineage_baseline_snapshot=lineage_baseline_snapshot,
    ).to_dict()
    path = CYCLE_MANIFEST_ROOT / group_id / f"{local_run_id}.json"
    absolute_path = checkout_root / path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(path), manifest


def _merge_plan_snapshot(bootstrap: BranchBootstrap, *, resolved_at: str) -> dict | None:
    if not bootstrap.merge_base:
        return None
    return {
        "base": _merge_participant_payload(bootstrap.merge_base),
        "fold_ins": [_merge_participant_payload(source) for source in bootstrap.merge_sources],
        "no_ops": [_merge_participant_payload(source) for source in bootstrap.merge_no_ops],
        "ranking_metric": bootstrap.anchor_metric,
        "policy": bootstrap.anchor_policy,
        "resolved_at": resolved_at,
        "requested_sources": list(bootstrap.merge_requested_sources),
    }


def _merge_cycle_provenance(
    bootstrap: BranchBootstrap,
    *,
    group_id: str,
    loop_index: int,
) -> dict | None:
    if not bootstrap.merge_base:
        return None
    active = bootstrap.merge_sources[loop_index - 1] if loop_index <= len(bootstrap.merge_sources) else None
    return {
        "merge_group_id": group_id,
        "loop_index": loop_index,
        "active_source": _merge_participant_payload(active) if active else None,
        "phase": "fold_in" if active else "refine",
        "base_snapshot": _merge_participant_payload(bootstrap.merge_base),
    }


def _merge_participant_payload(source: dict | None) -> dict | None:
    if not source:
        return None
    payload = {
        "source_group_id": str(source.get("source_group_id") or ""),
        "group_id": str(source.get("group_id") or ""),
        "branch": str(source.get("branch") or ""),
        "source_branch": str(source.get("source_branch") or ""),
        "commit_sha": str(source.get("commit_sha") or ""),
        "trajectory_step": source.get("trajectory_step"),
    }
    metric_value = source.get("metric_value")
    if isinstance(metric_value, (int, float)):
        payload["metric_value"] = float(metric_value)
    if source.get("reason"):
        payload["reason"] = str(source.get("reason"))
    return payload


def _commit_subject(*, loop_index: int, manifest: dict) -> str:
    summary = _manifest_summary(manifest)
    return f"Phase 1, loop {loop_index}: {summary}"


def _commit_body(*, local_run_id: str, manifest_path: str, manifest: dict) -> str:
    lines = [f"HiAgentResearch-Run-ID: {local_run_id}", f"Experiment-Manifest: {manifest_path}"]
    goal_id = str(manifest.get("goal_id", "")).strip()
    if goal_id:
        lines.append(f"Goal-ID: {goal_id}")
    return "\n".join(lines)


def _manifest_summary(manifest: dict) -> str:
    planned = manifest.get("planned_code_changes", [])
    if isinstance(planned, list) and planned:
        text = str(planned[0])
    else:
        text = str(manifest.get("goal_id") or manifest.get("goal") or "cycle update")
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    text = re.sub(r"^in\s+[^:]+:\s*", "", text, flags=re.IGNORECASE)
    if len(text) > 72:
        text = text[:69].rstrip() + "..."
    return text or "cycle update"


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
        GitService(REPO_ROOT).checkout(loaded_config.orchestration.baseline_ref)
    try:
        for wave in loaded_config.execution_waves():
            pending_wave = _pending_wave_groups(
                wave,
                registry=registry,
                config=loaded_config,
                loops=loops,
            )
            if not pending_wave:
                print(json.dumps({"ok": True, "reason": "wave already complete", "groups": wave}, indent=2))
                continue
            if parallel:
                exit_code = _run_wave_parallel(
                    pending_wave,
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
            for group_id in pending_wave:
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
        # The loops-all command has returned (success, early-exit, or error): the
        # research session is no longer live. Stamp it so the dashboard renders the
        # run as complete rather than in-progress.
        registry.mark_session_complete()
        _build_dashboard_snapshot(loaded_config)
    print(json.dumps({"ok": True, "summaries": [item.to_dict() for item in summaries]}, indent=2))
    return 0


def _build_dashboard_snapshot(config: HiAgentResearchConfig) -> None:
    """Best-effort dashboard refresh for completed local orchestration runs."""

    if not config.dashboard.enabled:
        return
    try:
        from hiagentresearch.src.dashboard.build import build_from_registry

        build_from_registry(
            state_dir=resolve_state_dir(),
            output_dir=config.dashboard_output_path(REPO_ROOT),
            config=config,
            source_label="local_registry",
        )
    except Exception as exc:  # pragma: no cover - warning path, not control-flow critical.
        print(
            json.dumps(
                {
                    "ok": False,
                    "warning": "dashboard build failed",
                    "error": str(exc),
                },
                indent=2,
            ),
            file=sys.stderr,
        )


def _pending_wave_groups(
    wave: list[str],
    *,
    registry: Registry,
    config: HiAgentResearchConfig,
    loops: int,
) -> list[str]:
    return [
        group_id
        for group_id in wave
        if not _group_complete(registry=registry, config=config, group_id=group_id, loops=loops)
    ]


def _group_complete(
    *,
    registry: Registry,
    config: HiAgentResearchConfig,
    group_id: str,
    loops: int,
) -> bool:
    group = config.group_by_id(group_id)
    effective_loops = group.loops if group.loops is not None else loops
    if effective_loops == 0:
        return registry.has_cycle_manifest(group_id, 0)
    return registry.clean_github_cycle_count(group_id) >= effective_loops


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
        materialize_framework_guidance(root=worktree_path)
        write_workspace_agents(config, root=worktree_path)
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

    failed_groups: list[str] = []
    for group_id, proc in processes:
        returncode = proc.wait()
        if returncode != 0:
            failed_groups.append(group_id)
            print(f"[{group_id}] failed with exit {returncode}", flush=True)
    if failed_groups:
        # A failed leaf just drops out of its area's competition — the surviving leaves' results
        # stay in the registry and the collapse adopts from them (lineage resolution already
        # tolerates a missing leaf). So preserve ONLY the failed leaves' artifacts and keep going,
        # rather than discarding the whole wave's work and aborting the run. Abort only when EVERY
        # group in the wave failed, leaving the area with nothing for downstream to build on.
        preserved = _preserve_parallel_failure_artifacts(failed_groups, worktrees)
        all_failed = len(failed_groups) == len(processes)
        print(
            json.dumps(
                {
                    "ok": not all_failed,
                    "reason": (
                        "entire parallel wave failed"
                        if all_failed
                        else "continued past failed leaves; preserved their artifacts"
                    ),
                    "failed_groups": failed_groups,
                    "artifacts": preserved,
                },
                indent=2,
            ),
            flush=True,
        )
        if all_failed:
            return 1
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
    parser.add_argument("--agent-model", default="", help="Override config.agent.model; empty uses config.")
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
