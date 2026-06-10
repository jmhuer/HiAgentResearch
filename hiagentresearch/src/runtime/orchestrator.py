from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from hiagentresearch.src.agents.agent_backends import AgentBackendError, run_cursor_agent_cycle
from hiagentresearch.src.agents.task_contract import task_contract
from hiagentresearch.src.lineage.resolve import BranchBootstrap
from hiagentresearch.src.core.artifacts import local_run_index_names
from hiagentresearch.src.core.config import load_config, resolve_group_id_for_branch
from hiagentresearch.src.core.models import (
    EvaluationSpec,
    IntentPacket,
    ResearchGroup,
    ScoreContext,
    TransitionEvent,
    utc_now_iso,
)
from hiagentresearch.src.core.pathspec import is_under_any, is_within
from hiagentresearch.src.git.service import GitService, GitServiceError
from hiagentresearch.src.registry.store import Registry
from hiagentresearch.src.runtime.quality import ResearchOutcome


from hiagentresearch.src.paths import (
    DEFAULT_RUNS_DIR,
    DEFAULT_STATE_DIR,
    REPO_ROOT,
    resolve_config_path,
    resolve_runs_dir,
    resolve_state_dir,
)

STATE_DIR = resolve_state_dir()
RUNS_DIR = resolve_runs_dir()
CONFIG_PATH = resolve_config_path()


def _load_groups(path: Path) -> dict[str, ResearchGroup]:
    if path.suffix in {".yaml", ".yml"}:
        return load_config(path).research_groups_by_id()
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[str, ResearchGroup] = {}
    for raw in payload.get("groups", []):
        evaluation = raw["evaluation"]
        command = evaluation["command"] if isinstance(evaluation, dict) else str(evaluation)
        group = ResearchGroup(
            id=raw["id"],
            branch=raw["branch"],
            objective=raw["objective"],
            policy_mode=raw["policy_mode"],
            evaluation=EvaluationSpec(command=command),
            task_kind=str(raw.get("task_kind", "metric_experiment")),
            workdir=str(raw.get("workdir", ".")),
            reference_paths=[],
            generated_paths=list(raw.get("generated_paths", [])),
            hidden_paths=list(raw.get("hidden_paths", [])),
        )
        groups[group.id] = group
    return groups


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _path_relative_to(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def _git_changed_files(workdir: Path) -> set[str]:
    try:
        return set(GitService(workdir).changed_files())
    except GitServiceError:
        return set()


def _git_head_sha(workdir: Path) -> str:
    try:
        return GitService(workdir).head_sha()
    except GitServiceError:
        return ""


def _validate_edit_boundary(
    *,
    workdir: Path,
    group: ResearchGroup,
    run_id: str,
    before_changes: set[str],
) -> tuple[bool, str, list[str]]:
    after_changes = _git_changed_files(workdir)
    cycle_changes = sorted(after_changes - before_changes)
    if not cycle_changes:
        return False, "agent cycle produced no changed files", []

    touched_reference = [path for path in cycle_changes if is_under_any(path, group.reference_paths)]
    if touched_reference:
        return (
            False,
            f"agent cycle modified read-only reference/eval paths: {touched_reference}",
            cycle_changes,
        )
    touched_hidden = [path for path in cycle_changes if is_under_any(path, group.hidden_paths)]
    if touched_hidden:
        return False, f"agent cycle modified hidden paths: {touched_hidden}", cycle_changes

    run_prefix = f".hiagentresearch/runs/{run_id}/"
    workspace_changes: list[str] = []
    outside: list[str] = []
    for path in cycle_changes:
        if path.startswith(run_prefix) or _is_generated_path(path, group.generated_paths):
            continue
        if is_within(path, group.workdir):
            workspace_changes.append(path)
        else:
            outside.append(path)
    if outside:
        return False, f"changed files outside workspace ({group.workdir}): {outside}", cycle_changes
    if not workspace_changes:
        return False, "agent cycle produced no workspace source change", cycle_changes
    return True, "", cycle_changes


# Dependency lockfiles a package manager (e.g. `uv`) may auto-write at the repo root as a side
# effect of an agent running it. They are tool output, never source edits, and are never committed
# (stage_research_commit only stages within the workspace) — so a stray one outside the workspace
# must not invalidate a cycle.
_TOOL_LOCKFILES = frozenset({"uv.lock", "poetry.lock", "Pipfile.lock"})


def _is_generated_path(path: str, generated_paths: list[str]) -> bool:
    if is_under_any(path, generated_paths):
        return True
    if Path(path).name in _TOOL_LOCKFILES:
        return True
    # Experiment manifests are framework-generated bookkeeping and should not
    # count as source edits during workspace boundary validation.
    return is_under_any(path, [".hiagentresearch/cycles"])


def _metadata_payload(
    *,
    run_id: str,
    group: ResearchGroup,
    status: str,
    failure_class: str,
    correlation_id: str,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "correlation_id": correlation_id,
        "group": _agent_safe_group_payload(group),
        "group_id": group.id,
        "branch": group.branch,
        "status": status,
        "failure_class": failure_class,
    }
    payload.update(extra)
    return payload


def _agent_safe_group_payload(group: ResearchGroup) -> dict[str, Any]:
    return {
        "id": group.id,
        "branch": group.branch,
        "objective": group.objective,
        "policy_mode": group.policy_mode,
        "task_kind": group.task_kind,
        "workdir": group.workdir,
        "reference_paths": list(group.reference_paths),
        "generated_paths": list(group.generated_paths),
        "hidden_paths": list(group.hidden_paths),
        "research_output_expectations": list(group.research_output_expectations),
        "guidance_files": list(group.guidance_files),
    }


def _execution_blocked_outcome(*, reason: str, next_action: str) -> dict[str, Any]:
    return ResearchOutcome(
        research_outcome="execution_blocked",
        next_action=next_action,
        reason=reason,
    ).to_dict()


def _finalize_blocked_run(
    *,
    registry: Registry,
    run_dir: Path,
    metadata_path: Path,
    checkout_root: Path,
    run_id: str,
    group: ResearchGroup,
    correlation_id: str,
    failure_class: str,
    reason: str,
    next_action: str,
    transition_reason: str,
    failure_class_payload: dict[str, Any] | None = None,
    metadata_extra: dict[str, Any] | None = None,
    print_extra: dict[str, Any] | None = None,
) -> int:
    """Persist a blocked cycle (agent-fail / plan-fail / edit-fail) and return exit code 1.

    All three pre-eval failure paths share this skeleton; only the optional
    ``failure_class.json`` payload and a few report fields differ.
    """
    research_outcome = _execution_blocked_outcome(reason=reason, next_action=next_action)
    _write_json(run_dir / "research_outcome.json", research_outcome)
    if failure_class_payload is not None:
        _write_json(run_dir / "failure_class.json", failure_class_payload)
    _write_json(
        metadata_path,
        _metadata_payload(
            run_id=run_id,
            group=group,
            status="error",
            failure_class=failure_class,
            correlation_id=correlation_id,
            error=reason,
            agent_backend="cursor_sdk",
            **(metadata_extra or {}),
        ),
    )
    registry.record_run(
        run_id=run_id,
        group_id=group.id,
        branch=group.branch,
        status="error",
        failure_class=failure_class,
        metrics={},
        correlation_id=correlation_id,
    )
    registry.record_transition(
        TransitionEvent(
            run_id=run_id,
            group_id=group.id,
            from_state="running_agent_cycle",
            to_state="blocked",
            reason=transition_reason,
            actor="orchestrator",
        )
    )
    payload: dict[str, Any] = {
        "ok": False,
        "run_id": run_id,
        "status": "error",
        "failure_class": failure_class,
        "error": reason,
        "run_dir": _path_relative_to(run_dir, checkout_root),
    }
    if print_extra:
        payload.update(print_extra)
    print(json.dumps(payload, indent=2))
    return 1


def _validate_agent_intent_contract(*, run_dir: Path, group: ResearchGroup, run_id: str) -> tuple[bool, str]:
    intent_path = run_dir / "cycle_intent.json"
    plan_path = run_dir / "cycle_plan.md"
    if not intent_path.exists():
        return False, f"missing planning artifact: {intent_path}"
    if not plan_path.exists():
        return False, f"missing planning artifact: {plan_path}"

    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"invalid cycle_intent.json: {exc}"

    required_keys = {
        "run_id",
        "group_id",
        "objective",
        "goal_id",
        "goal",
        "evidence_refs",
        "planned_code_changes",
        "target_files",
        "success_criteria",
        "rollback_plan",
    }
    missing = sorted(k for k in required_keys if k not in intent)
    if missing:
        return False, f"cycle_intent.json missing keys: {missing}"
    if intent.get("run_id") != run_id:
        return False, "cycle_intent.json run_id does not match orchestrator run_id"
    if intent.get("group_id") != group.id:
        return False, "cycle_intent.json group_id does not match group"

    target_files = intent.get("target_files")
    if not isinstance(target_files, list) or not target_files:
        return False, "cycle_intent.json target_files must be a non-empty list"
    outside_workspace = sorted(
        path
        for path in target_files
        if not is_within(str(path), group.workdir)
        or is_under_any(str(path), group.reference_paths)
        or is_under_any(str(path), group.generated_paths)
        or is_under_any(str(path), group.hidden_paths)
    )
    if outside_workspace:
        return (
            False,
            f"cycle_intent.json target_files must be workspace source files under "
            f"{group.workdir}: {outside_workspace}",
        )

    plan_text = plan_path.read_text(encoding="utf-8")
    for heading in _required_plan_headings(group):
        if heading not in plan_text:
            return False, f"cycle_plan.md missing heading: {heading}"
    if len(plan_text.strip()) < 200:
        return False, "cycle_plan.md is too short to qualify as pre-code planning"

    return True, ""


def _required_plan_headings(group: ResearchGroup) -> tuple[str, ...]:
    return task_contract(group.task_kind).required_headings


def _apply_agent_intent_update(*, run_dir: Path, prior: IntentPacket) -> IntentPacket:
    intent_path = run_dir / "cycle_intent.json"
    if not intent_path.exists():
        return prior
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return prior

    goal_id = intent.get("goal_id")
    goal_text = intent.get("goal")
    if isinstance(goal_id, str) and goal_id.strip():
        prior.active_goal_id = goal_id.strip()
    if isinstance(goal_text, str) and goal_text.strip():
        prior.goal_text = goal_text.strip()
    return prior


def _seed_intent(group: ResearchGroup) -> IntentPacket:
    # A fan-out leaf carries a specific seed goal (the author-fixed approach it must pursue).
    # An unseeded (linear) group instead gets an explicit cue that the agent OWNS the
    # direction this cycle — a specific change scoped to the objective above, not the broad
    # objective itself — which the agent replaces with its real goal on the first cycle.
    goal_text = group.seed_approach or "(no preset goal — you choose this cycle's specific change, within the objective above)"
    return IntentPacket(
        group_id=group.id,
        active_goal_id=f"{group.id}-g1",
        goal_text=goal_text,
        attempt_count=0,
        last_failure_class="none",
        next_action="continue",
    )


def init_state() -> int:
    from hiagentresearch.src.core.guidance import materialize_framework_guidance
    from hiagentresearch.src.project.docs import write_workspace_agents

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    registry = Registry(STATE_DIR)
    registry.init()
    groups = _load_groups(CONFIG_PATH)
    framework_agents = materialize_framework_guidance()
    if CONFIG_PATH.suffix in {".yaml", ".yml"}:
        workspace_agents = write_workspace_agents(load_config(CONFIG_PATH))
    else:
        workspace_agents = None
    print(
        json.dumps(
            {
                "ok": True,
                "groups": sorted(groups.keys()),
                "framework_agents": str(framework_agents),
                "workspace_agents": str(workspace_agents) if workspace_agents else None,
            },
            indent=2,
        )
    )
    return 0


def status_report(group_id: str | None = None) -> int:
    registry = Registry(STATE_DIR)
    registry.init()
    latest = registry.latest_run(group_id)
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": registry.schema_version(),
        "group_id": group_id,
        "latest_run": latest,
    }
    if latest:
        run_id = str(latest["run_id"])
        payload["metrics"] = registry.metrics_for_run(run_id)
        payload["research_outcome"] = registry.outcome_for_run(run_id)
        payload["cycle"] = registry.cycle_for_run(run_id)
        payload["artifacts"] = registry.artifacts_for_run(run_id)
        packet = registry.read_intent_packet(str(latest["group_id"]))
        payload["intent_packet"] = packet.to_dict() if packet else None
    print(json.dumps(payload, indent=2))
    return 0


def resolve_group(branch: str) -> int:
    print(resolve_group_id_for_branch(branch, load_config(CONFIG_PATH)))
    return 0


def _build_score_context(
    *,
    registry: Registry,
    config,
    group: ResearchGroup,
    lineage_bootstrap: BranchBootstrap | None,
    loop_index: int,
    loops: int,
) -> ScoreContext | None:
    """Assemble the numeric gradient for this cycle's prompt from data the registry
    already holds — no new SQL, no eval. The metric is the lineage's anchor metric when
    set (merge/inherit groups) else the first configured target; baseline + this group's
    own committed trajectory come from the registry; the inherited floor is the metric at
    the bootstrap's start commit (the best-so-far the agent must not regress below)."""
    targets = config.evaluation.targets
    metric_name = ""
    if lineage_bootstrap is not None:
        metric_name = getattr(lineage_bootstrap, "anchor_metric", "") or ""
    if not metric_name:
        metric_name = next(iter(targets), "")
    if not metric_name:
        return None

    snapshot = registry.baseline_snapshot() or {}
    baseline_metrics = snapshot.get("metrics") or {}
    baseline_value = baseline_metrics.get(metric_name)

    trajectory = tuple(
        (ordinal, float(row["metric_value"]))
        for ordinal, row in enumerate(
            registry.github_runs_with_metric(group.id, metric_name), start=1
        )
        if row.get("metric_value") is not None
    )

    inherited_floor = None
    if (
        lineage_bootstrap is not None
        and lineage_bootstrap.mode != "baseline"
        and lineage_bootstrap.parent_group_id
        and lineage_bootstrap.start_ref
    ):
        inherited_floor = registry.metric_for_group_commit(
            lineage_bootstrap.parent_group_id, lineage_bootstrap.start_ref, metric_name
        )

    return ScoreContext(
        metric_name=metric_name,
        minimize=config.evaluation.metric_minimizes(metric_name),
        baseline_value=float(baseline_value) if baseline_value is not None else None,
        trajectory=trajectory,
        inherited_floor=float(inherited_floor) if inherited_floor is not None else None,
        attempt_index=loop_index,
        total_attempts=loops,
    )


def run_group(
    group_id: str,
    workdir: Path,
    agent_model: str,
    lineage_bootstrap: BranchBootstrap | None = None,
    loop_index: int = 1,
    loops: int = 1,
) -> int:
    config = load_config(CONFIG_PATH)
    registry = Registry(STATE_DIR)
    registry.init()
    groups = config.research_groups_by_id()
    if group_id not in groups:
        print(json.dumps({"ok": False, "error": f"unknown group_id: {group_id}"}, indent=2))
        return 1

    group = groups[group_id]
    checkout_root = workdir.resolve()
    runs_dir = resolve_runs_dir(checkout_root)
    preexisting_changes = _git_changed_files(checkout_root)
    # The orchestrator is the SOLE committer: it commits the agent's edit after this cycle
    # returns. So HEAD must not move while the agent runs. We capture it here and assert it
    # is unchanged after the agent — if the agent commits/resets/checks out, we fail fast
    # with a clear `agent_moved_head` error instead of mis-reporting "no changed files"
    # (a self-committed edit leaves the working tree clean and looks empty to the boundary).
    head_before = _git_head_sha(checkout_root)
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    correlation_id = run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    actions_path = run_dir / "agent_actions.jsonl"
    metadata_path = run_dir / "run_meta.json"

    transition = TransitionEvent(
        run_id=run_id,
        group_id=group.id,
        from_state="idle",
        to_state="running_agent_cycle",
        reason="manual run-group command",
        actor="orchestrator",
    )
    registry.record_transition(transition)

    _append_jsonl(
        actions_path,
        {
            "step": "start_cycle",
            "group_id": group.id,
            "objective": group.objective,
            "policy_mode": group.policy_mode,
        },
    )

    prior_intent = registry.read_intent_packet(group.id) or _seed_intent(group)
    # Agent execution settings are config-driven; the CLI --agent-model overrides the
    # model only when provided. thinking/retries/timeouts come from config.agent.
    agent_cfg = config.agent
    effective_model = agent_model or agent_cfg.model
    score_context = _build_score_context(
        registry=registry,
        config=config,
        group=group,
        lineage_bootstrap=lineage_bootstrap,
        loop_index=loop_index,
        loops=loops,
    )
    _append_jsonl(
        actions_path,
        {"step": "run_agent_backend", "backend": "cursor_sdk", "model": effective_model, "thinking": agent_cfg.thinking},
    )
    try:
        record = run_cursor_agent_cycle(
            workdir=workdir,
            run_dir=run_dir,
            group=group,
            intent_packet=prior_intent,
            run_id=run_id,
            model=effective_model,
            thinking=agent_cfg.thinking,
            startup_attempts=agent_cfg.startup_attempts,
            startup_retry_backoff_sec=agent_cfg.startup_retry_backoff_sec,
            unary_timeout_sec=agent_cfg.unary_timeout_sec,
            stream_timeout_sec=agent_cfg.stream_timeout_sec,
            lineage_bootstrap=lineage_bootstrap,
            score_context=score_context,
        )
        (run_dir / "agent_stdout.txt").write_text(record.summary, encoding="utf-8")
        (run_dir / "agent_stderr.txt").write_text("", encoding="utf-8")
    except AgentBackendError as exc:
        failure_class = exc.failure_class
        cursor_run_status = ""
        sdk_run_id = ""
        agent_id = ""
        stream_error = ""
        if exc.record is not None:
            cursor_run_status = str(exc.record.raw_result.get("cursor_run_status") or exc.record.status)
            sdk_run_id = str(exc.record.raw_result.get("sdk_run_id") or exc.record.raw_result.get("id") or "")
            agent_id = str(exc.record.raw_result.get("agent_id") or "")
            stream_error = str(exc.record.raw_result.get("stream_error") or "")
        return _finalize_blocked_run(
            registry=registry,
            run_dir=run_dir,
            metadata_path=metadata_path,
            checkout_root=checkout_root,
            run_id=run_id,
            group=group,
            correlation_id=correlation_id,
            failure_class=failure_class,
            reason=str(exc),
            next_action="continue",
            transition_reason=f"cursor_agent_backend_failed:{failure_class}",
            failure_class_payload={
                "failure_class": failure_class,
                "exit_code": 1,
                "error": str(exc),
                "cursor_run_status": cursor_run_status,
                "sdk_run_id": sdk_run_id,
                "agent_id": agent_id,
                "stream_error": stream_error,
            },
            metadata_extra={"cursor_run_status": cursor_run_status},
            print_extra={
                "cursor_run_status": cursor_run_status,
                "sdk_run_id": sdk_run_id,
                "agent_id": agent_id,
                "stream_error": stream_error,
            },
        )

    head_after = _git_head_sha(checkout_root)
    if head_before and head_after and head_before != head_after:
        reason = (
            f"agent moved HEAD during the cycle ({head_before[:7]} -> {head_after[:7]}); "
            "the agent must not run git add/commit/merge/rebase/reset/stash/checkout/push — "
            "the orchestrator owns committing. Make edits in the working tree and leave them "
            "uncommitted."
        )
        _append_jsonl(
            actions_path,
            {"step": "head_guard_failed", "head_before": head_before, "head_after": head_after},
        )
        return _finalize_blocked_run(
            registry=registry,
            run_dir=run_dir,
            metadata_path=metadata_path,
            checkout_root=checkout_root,
            run_id=run_id,
            group=group,
            correlation_id=correlation_id,
            failure_class="agent_moved_head",
            reason=reason,
            next_action="repair",
            transition_reason="agent_moved_head",
            failure_class_payload={
                "failure_class": "agent_moved_head",
                "head_before": head_before,
                "head_after": head_after,
                "error": reason,
            },
        )

    _append_jsonl(actions_path, {"step": "validate_plan_before_eval"})
    valid_contract, contract_error = _validate_agent_intent_contract(run_dir=run_dir, group=group, run_id=run_id)
    if not valid_contract:
        _append_jsonl(actions_path, {"step": "plan_validation_failed", "error": contract_error})
        return _finalize_blocked_run(
            registry=registry,
            run_dir=run_dir,
            metadata_path=metadata_path,
            checkout_root=checkout_root,
            run_id=run_id,
            group=group,
            correlation_id=correlation_id,
            failure_class="invalid_cycle",
            reason=contract_error,
            next_action="repair",
            transition_reason="planning_contract_failed",
        )
    _append_jsonl(actions_path, {"step": "plan_validation_passed"})

    valid_edits, edit_error, cycle_changes = _validate_edit_boundary(
        workdir=workdir,
        group=group,
        run_id=run_id,
        before_changes=preexisting_changes,
    )
    _append_jsonl(
        actions_path,
        {"step": "edit_boundary_check", "valid": valid_edits, "changed_files": cycle_changes},
    )
    if not valid_edits:
        return _finalize_blocked_run(
            registry=registry,
            run_dir=run_dir,
            metadata_path=metadata_path,
            checkout_root=checkout_root,
            run_id=run_id,
            group=group,
            correlation_id=correlation_id,
            failure_class="invalid_cycle",
            reason=edit_error,
            next_action="repair",
            transition_reason="edit_boundary_failed",
        )

    # No local metric eval. The full eval is owned solely by the GitHub eval node
    # (the single, commit-bound source of truth). Locally we only verify that the
    # agent produced a contract-valid, bounded edit (the gates above); the loop
    # controller then commits/pushes this cycle and CI scores it. This removes a
    # redundant full eval per cycle — the loop already waits for CI and drives every
    # continue/stop/repair decision from the CI outcome, not a local one. The CI
    # result is written back into the intent packet (by the loop controller) so the
    # next cycle's agent prompt reflects how this change actually scored.
    failure_class = "none"
    _append_jsonl(actions_path, {"step": "cycle_validated", "note": "ci_owns_eval"})

    registry.record_run(
        run_id=run_id,
        group_id=group.id,
        branch=group.branch,
        status="finished",
        failure_class=failure_class,
        metrics={},
        correlation_id=correlation_id,
    )

    prior = _apply_agent_intent_update(run_dir=run_dir, prior=prior_intent)
    prior.attempt_count += 1
    prior.updated_at = utc_now_iso()
    registry.write_intent_packet(prior)

    registry.record_transition(
        TransitionEvent(
            run_id=run_id,
            group_id=group.id,
            from_state="running_agent_cycle",
            to_state="ready_for_wake",
            reason="cycle_validated:awaiting_ci_eval",
            actor="orchestrator",
        )
    )

    _write_json(
        metadata_path,
        _metadata_payload(
            run_id=run_id,
            group=group,
            status="finished",
            failure_class=failure_class,
            correlation_id=correlation_id,
            research_outcome="awaiting_ci_eval",
            next_action=prior.next_action,
        ),
    )
    registry.record_artifacts(
        run_id=run_id,
        artifact_paths=[
            run_dir / name for name in local_run_index_names() if (run_dir / name).exists()
        ],
        artifact_type="research_cycle",
        base_dir=run_dir,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "run_id": run_id,
                "status": "finished",
                "failure_class": failure_class,
                "research_outcome": "awaiting_ci_eval",
                "next_action": prior.next_action,
                "run_dir": _path_relative_to(run_dir, checkout_root),
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase-1 orchestrator for hiagentresearch.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Initialize the local registry.")
    status = sub.add_parser("status", help="Print registry-backed status.")
    status.add_argument("--group-id", default=None)
    resolve = sub.add_parser("resolve-group", help="Resolve group id for a branch.")
    resolve.add_argument("--branch", required=True)
    run = sub.add_parser("run-group", help="Run one research group evaluation cycle.")
    run.add_argument("--group-id", required=True)
    run.add_argument("--workdir", default=".")
    run.add_argument("--agent-model", default="composer-2.5")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "init":
        return init_state()
    if args.cmd == "status":
        return status_report(group_id=args.group_id)
    if args.cmd == "resolve-group":
        return resolve_group(branch=args.branch)
    if args.cmd == "run-group":
        return run_group(
            group_id=args.group_id,
            workdir=Path(args.workdir).resolve(),
            agent_model=args.agent_model,
        )
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
