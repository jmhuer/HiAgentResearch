from __future__ import annotations

import argparse
import os
import json
import shlex
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hiagentresearch.src.agents.agent_backends import AgentBackendError, run_cursor_agent_cycle
from hiagentresearch.src.lineage.resolve import BranchBootstrap
from hiagentresearch.src.core.artifact_schema import (
    ArtifactParseError,
    classify_non_json_failure,
    normalize_eval,
)
from hiagentresearch.src.core.config import load_config, resolve_group_id_for_branch
from hiagentresearch.src.core.models import (
    EvaluationSpec,
    IntentPacket,
    ResearchGroup,
    TransitionEvent,
    utc_now_iso,
)
from hiagentresearch.src.runtime.quality import classify_research_outcome
from hiagentresearch.src.registry.store import Registry


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
        group = ResearchGroup(
            id=raw["id"],
            branch=raw["branch"],
            objective=raw["objective"],
            policy_mode=raw["policy_mode"],
            allowed_paths=list(raw.get("allowed_paths", [])),
            evaluation=EvaluationSpec(**raw["evaluation"]),
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
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()

    changed: set[str] = set()
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        changed.add(path.strip('"'))
    return changed


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

    frozen = [path for path in cycle_changes if _is_generated_path(path, group.frozen_paths)]
    if frozen:
        return False, f"agent cycle modified frozen paths: {frozen}", cycle_changes

    run_prefix = f".hiagentresearch/runs/{run_id}/"
    allowed = set(group.allowed_paths)
    outside = [
        path
        for path in cycle_changes
        if path not in allowed
        and not path.startswith(run_prefix)
        and not _is_generated_path(path, group.generated_paths)
    ]
    if outside:
        return False, f"changed files outside configured edit contract: {outside}", cycle_changes

    core_paths = set(_core_allowed_paths(group))
    if core_paths and not core_paths.intersection(cycle_changes):
        return False, "agent cycle produced no changed core experiment file", cycle_changes
    return True, "", cycle_changes


def _is_generated_path(path: str, generated_paths: list[str]) -> bool:
    normalized = path.rstrip("/")
    for generated in generated_paths:
        generated_normalized = generated.rstrip("/")
        if normalized == generated_normalized or normalized.startswith(f"{generated_normalized}/"):
            return True
    return False


def _normalize_python_command(command: str) -> list[str]:
    tokens = shlex.split(command)
    if tokens and tokens[0] == "python":
        tokens[0] = sys.executable
    return tokens


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
        "group": asdict(group),
        "group_id": group.id,
        "branch": group.branch,
        "status": status,
        "failure_class": failure_class,
    }
    payload.update(extra)
    return payload


def _execution_blocked_outcome(*, reason: str, next_action: str) -> dict[str, Any]:
    return {
        "research_outcome": "execution_blocked",
        "next_action": next_action,
        "reason": reason,
    }


def _core_allowed_paths(group: ResearchGroup) -> list[str]:
    supporting = set(group.supporting_artifacts)
    return [path for path in group.allowed_paths if path not in supporting]


def _validate_agent_intent_contract(*, run_dir: Path, group: ResearchGroup, run_id: str) -> tuple[bool, str]:
    intent_path = run_dir / "experiment_intent.json"
    plan_path = run_dir / "experiment_plan.md"
    if not intent_path.exists():
        return False, f"missing planning artifact: {intent_path}"
    if not plan_path.exists():
        return False, f"missing planning artifact: {plan_path}"

    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"invalid experiment_intent.json: {exc}"

    required_keys = {
        "run_id",
        "group_id",
        "objective",
        "hypothesis_id",
        "hypothesis",
        "evidence_refs",
        "planned_code_changes",
        "target_files",
        "success_criteria",
        "rollback_plan",
    }
    missing = sorted(k for k in required_keys if k not in intent)
    if missing:
        return False, f"experiment_intent.json missing keys: {missing}"
    if intent.get("run_id") != run_id:
        return False, "experiment_intent.json run_id does not match orchestrator run_id"
    if intent.get("group_id") != group.id:
        return False, "experiment_intent.json group_id does not match group"

    target_files = intent.get("target_files")
    if not isinstance(target_files, list) or not target_files:
        return False, "experiment_intent.json target_files must be a non-empty list"
    core_paths = set(_core_allowed_paths(group))
    if core_paths and not core_paths.intersection(set(target_files)):
        return False, "experiment_intent.json must target at least one core allowed file"
    outside_allowed = sorted(path for path in target_files if path not in set(group.allowed_paths))
    if outside_allowed:
        return False, f"experiment_intent.json target_files outside allowed paths: {outside_allowed}"

    plan_text = plan_path.read_text(encoding="utf-8")
    for heading in ("## Evidence", "## Planned Edit", "## Risk and Rollback", "## Eval Expectations"):
        if heading not in plan_text:
            return False, f"experiment_plan.md missing heading: {heading}"
    if len(plan_text.strip()) < 200:
        return False, "experiment_plan.md is too short to qualify as pre-code planning"

    return True, ""


def _apply_agent_intent_update(*, run_dir: Path, prior: IntentPacket) -> IntentPacket:
    intent_path = run_dir / "experiment_intent.json"
    if not intent_path.exists():
        return prior
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return prior

    hypothesis_id = intent.get("hypothesis_id")
    hypothesis_text = intent.get("hypothesis")
    if isinstance(hypothesis_id, str) and hypothesis_id.strip():
        prior.active_hypothesis_id = hypothesis_id.strip()
    if isinstance(hypothesis_text, str) and hypothesis_text.strip():
        prior.hypothesis_text = hypothesis_text.strip()
    return prior


def _seed_intent(group: ResearchGroup) -> IntentPacket:
    return IntentPacket(
        group_id=group.id,
        active_hypothesis_id=f"{group.id}-h1",
        hypothesis_text=f"Initial phase-1 hypothesis for {group.id}.",
        attempt_count=0,
        last_failure_class="none",
        next_action="continue",
        rollback_anchor_sha="",
        key_evidence_refs=[],
    )


def init_state() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    registry = Registry(STATE_DIR)
    registry.init()
    groups = _load_groups(CONFIG_PATH)
    print(json.dumps({"ok": True, "groups": sorted(groups.keys())}, indent=2))
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
        payload["experiment"] = registry.experiment_for_run(run_id)
        payload["artifacts"] = registry.artifacts_for_run(run_id)
        packet = registry.read_intent_packet(str(latest["group_id"]))
        payload["intent_packet"] = packet.to_dict() if packet else None
    print(json.dumps(payload, indent=2))
    return 0


def resolve_group(branch: str) -> int:
    print(resolve_group_id_for_branch(branch, load_config(CONFIG_PATH)))
    return 0


def run_group(
    group_id: str,
    workdir: Path,
    quick: bool,
    agent_model: str,
    lineage_bootstrap: BranchBootstrap | None = None,
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
        reason="manual phase-1 run-group command",
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
    _append_jsonl(actions_path, {"step": "run_agent_backend", "backend": "cursor_sdk", "model": agent_model})
    try:
        record = run_cursor_agent_cycle(
            workdir=workdir,
            run_dir=run_dir,
            group=group,
            intent_packet=prior_intent,
            run_id=run_id,
            model=agent_model,
            lineage_bootstrap=lineage_bootstrap,
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
        research_outcome = _execution_blocked_outcome(reason=str(exc), next_action="continue")
        _write_json(run_dir / "research_outcome.json", research_outcome)
        _write_json(
            run_dir / "failure_class.json",
            {
                "failure_class": failure_class,
                "exit_code": 1,
                "error": str(exc),
                "cursor_run_status": cursor_run_status,
                "sdk_run_id": sdk_run_id,
                "agent_id": agent_id,
                "stream_error": stream_error,
            },
        )
        _write_json(
            metadata_path,
            _metadata_payload(
                run_id=run_id,
                group=group,
                status="error",
                failure_class=failure_class,
                correlation_id=correlation_id,
                error=str(exc),
                agent_backend="cursor_sdk",
                cursor_run_status=cursor_run_status,
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
        registry.record_research_outcome(run_id=run_id, outcome=research_outcome)
        registry.record_transition(
            TransitionEvent(
                run_id=run_id,
                group_id=group.id,
                from_state="running_agent_cycle",
                to_state="blocked",
                reason=f"cursor_agent_backend_failed:{failure_class}",
                actor="orchestrator",
            )
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "run_id": run_id,
                    "status": "error",
                    "failure_class": failure_class,
                    "error": str(exc),
                    "cursor_run_status": cursor_run_status,
                    "sdk_run_id": sdk_run_id,
                    "agent_id": agent_id,
                    "stream_error": stream_error,
                    "run_dir": _path_relative_to(run_dir, checkout_root),
                },
                indent=2,
            )
        )
        return 1

    _append_jsonl(actions_path, {"step": "validate_plan_before_eval"})
    valid_contract, contract_error = _validate_agent_intent_contract(run_dir=run_dir, group=group, run_id=run_id)
    if not valid_contract:
        research_outcome = _execution_blocked_outcome(reason=contract_error, next_action="repair")
        _write_json(run_dir / "research_outcome.json", research_outcome)
        _write_json(
            metadata_path,
                _metadata_payload(
                    run_id=run_id,
                    group=group,
                    status="error",
                    failure_class="invalid_cycle",
                    correlation_id=correlation_id,
                    error=contract_error,
                    agent_backend="cursor_sdk",
                ),
        )
        _append_jsonl(actions_path, {"step": "plan_validation_failed", "error": contract_error})
        registry.record_run(
            run_id=run_id,
            group_id=group.id,
            branch=group.branch,
            status="error",
            failure_class="invalid_cycle",
            metrics={},
            correlation_id=correlation_id,
        )
        registry.record_research_outcome(run_id=run_id, outcome=research_outcome)
        registry.record_transition(
            TransitionEvent(
                run_id=run_id,
                group_id=group.id,
                from_state="running_agent_cycle",
                to_state="blocked",
                reason="planning_contract_failed",
                actor="orchestrator",
            )
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "run_id": run_id,
                    "status": "error",
                    "failure_class": "invalid_cycle",
                    "error": contract_error,
                    "run_dir": _path_relative_to(run_dir, checkout_root),
                },
                indent=2,
            )
        )
        return 1
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
        research_outcome = _execution_blocked_outcome(reason=edit_error, next_action="repair")
        _write_json(run_dir / "research_outcome.json", research_outcome)
        _write_json(
            metadata_path,
            _metadata_payload(
                run_id=run_id,
                group=group,
                status="error",
                failure_class="invalid_cycle",
                correlation_id=correlation_id,
                error=edit_error,
                agent_backend="cursor_sdk",
            ),
        )
        registry.record_run(
            run_id=run_id,
            group_id=group.id,
            branch=group.branch,
            status="error",
            failure_class="invalid_cycle",
            metrics={},
            correlation_id=correlation_id,
        )
        registry.record_research_outcome(run_id=run_id, outcome=research_outcome)
        registry.record_transition(
            TransitionEvent(
                run_id=run_id,
                group_id=group.id,
                from_state="running_agent_cycle",
                to_state="blocked",
                reason="edit_boundary_failed",
                actor="orchestrator",
            )
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "run_id": run_id,
                    "status": "error",
                    "failure_class": "invalid_cycle",
                    "error": edit_error,
                    "run_dir": _path_relative_to(run_dir, checkout_root),
                },
                indent=2,
            )
        )
        return 1

    cmd = group.evaluation.command
    _append_jsonl(actions_path, {"step": "run_evaluation", "command": cmd})

    proc = subprocess.run(
        _normalize_python_command(cmd),
        cwd=workdir,
        env={**os.environ, "HIAGENTRESEARCH_RUN_DIR": str(run_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    (run_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")

    failure_class = "infra_failure"
    passed = False
    metrics: dict[str, float] = {}
    parsed: dict[str, Any] = {}
    research_outcome = {
        "research_outcome": "execution_blocked",
        "next_action": "continue",
        "reason": "evaluation did not complete",
    }
    try:
        normalized = normalize_eval(
            parser=group.evaluation.parser,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
        )
        failure_class = normalized.failure_class
        passed = normalized.passed
        metrics = normalized.to_metrics()
        if normalized.raw.get("duration_sec") is not None:
            metrics["duration_sec"] = float(normalized.raw["duration_sec"])
        parsed = normalized.raw
        eval_config = config.group_by_id(group.id).evaluation or config.evaluation
        outcome = classify_research_outcome(
            execution_failure_class=failure_class,
            eval_passed=passed,
            metrics=metrics,
            targets=eval_config.targets,
        )
        research_outcome = outcome.to_dict()
        parsed["research_outcome"] = research_outcome["research_outcome"]
        _write_json(run_dir / "metrics.json", metrics)
        _write_json(
            run_dir / "failure_class.json",
            {"failure_class": failure_class, "exit_code": proc.returncode},
        )
        _write_json(run_dir / "research_outcome.json", research_outcome)
        registry.record_research_outcome(run_id=run_id, outcome=research_outcome)
        _write_json(run_dir / "parsed_eval.json", parsed)
    except ArtifactParseError as exc:
        failure_class = classify_non_json_failure(proc.stderr, proc.returncode)
        research_outcome = {
            "research_outcome": "execution_blocked",
            "next_action": "repair" if failure_class == "code_failure" else "continue",
            "reason": str(exc),
        }
        _write_json(
            run_dir / "failure_class.json",
            {"failure_class": failure_class, "exit_code": proc.returncode, "error": str(exc)},
        )
        _write_json(run_dir / "research_outcome.json", research_outcome)
        registry.record_research_outcome(run_id=run_id, outcome=research_outcome)
        _append_jsonl(actions_path, {"step": "parse_failure", "error": str(exc)})

    status = "finished" if failure_class == "none" else "error"
    registry.record_run(
        run_id=run_id,
        group_id=group.id,
        branch=group.branch,
        status=status,
        failure_class=failure_class,
        metrics=metrics,
        correlation_id=correlation_id,
    )

    prior = _apply_agent_intent_update(run_dir=run_dir, prior=prior_intent)
    if failure_class != "infra_failure":
        prior.attempt_count += 1
    prior.last_failure_class = failure_class if failure_class != "none" else "none"
    prior.next_action = str(research_outcome["next_action"])
    prior.key_evidence_refs = [run_id]
    prior.updated_at = utc_now_iso()
    registry.write_intent_packet(prior)

    registry.record_transition(
        TransitionEvent(
            run_id=run_id,
            group_id=group.id,
            from_state="running_agent_cycle",
            to_state="ready_for_wake" if failure_class in {"none", "code_failure", "eval_failure"} else "blocked",
            reason=f"eval_completed:{failure_class}:{research_outcome['research_outcome']}",
            actor="orchestrator",
        )
    )

    _write_json(
        metadata_path,
        _metadata_payload(
            run_id=run_id,
            group=group,
            status=status,
            failure_class=failure_class,
            correlation_id=correlation_id,
            exit_code=proc.returncode,
            passed=passed,
            research_outcome=research_outcome["research_outcome"],
            next_action=research_outcome["next_action"],
        ),
    )
    registry.record_artifacts(
        run_id=run_id,
        artifact_paths=[run_dir / name for name in config.artifact_contract.required + config.artifact_contract.optional],
        artifact_type="local_eval",
        base_dir=run_dir,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "run_id": run_id,
                "status": status,
                "failure_class": failure_class,
                "research_outcome": research_outcome["research_outcome"],
                "next_action": research_outcome["next_action"],
                "run_dir": _path_relative_to(run_dir, checkout_root),
            },
            indent=2,
        )
    )
    return 0 if failure_class == "none" else 2


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
    run.add_argument("--quick", action="store_true")
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
            quick=args.quick,
            agent_model=args.agent_model,
        )
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
