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

from hiagentresearch.src.agent_backends import AgentBackendError, run_cursor_agent_cycle
from hiagentresearch.src.artifact_schema import (
    ArtifactParseError,
    classify_non_json_failure,
    normalize_eval,
)
from hiagentresearch.src.models import (
    EvaluationSpec,
    IntentPacket,
    ResearchGroup,
    TransitionEvent,
    utc_now_iso,
)
from hiagentresearch.src.registry import Registry


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DIR = REPO_ROOT / ".hiagentresearch" / "state"
DEFAULT_RUNS_DIR = REPO_ROOT / ".hiagentresearch" / "runs"
STATE_DIR = Path(os.environ.get("HIAGENTRESEARCH_STATE_DIR", str(DEFAULT_STATE_DIR))).resolve()
RUNS_DIR = Path(os.environ.get("HIAGENTRESEARCH_RUNS_DIR", str(DEFAULT_RUNS_DIR))).resolve()
GROUPS_JSON = Path(os.environ.get("HIAGENTRESEARCH_GROUPS_JSON", str(STATE_DIR / "research_groups.json"))).resolve()


def _load_groups(path: Path) -> dict[str, ResearchGroup]:
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


def _normalize_python_command(command: str) -> list[str]:
    tokens = shlex.split(command)
    if tokens and tokens[0] == "python":
        tokens[0] = sys.executable
    return tokens


def _core_allowed_paths(group: ResearchGroup) -> list[str]:
    return [
        path
        for path in group.allowed_paths
        if not path.endswith("research_markers.py") and not path.endswith("research_hypotheses.py")
    ]


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

    plan_text = plan_path.read_text(encoding="utf-8")
    for heading in ("## Evidence", "## Planned Edit", "## Risk and Rollback", "## Eval Expectations"):
        if heading not in plan_text:
            return False, f"experiment_plan.md missing heading: {heading}"
    if len(plan_text.strip()) < 200:
        return False, "experiment_plan.md is too short to qualify as pre-code planning"

    return True, ""


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
    groups = _load_groups(GROUPS_JSON)
    for group in groups.values():
        if registry.read_intent_packet(group.id) is None:
            registry.write_intent_packet(_seed_intent(group))
    print(json.dumps({"ok": True, "groups_seeded": sorted(groups.keys())}, indent=2))
    return 0


def run_group(
    group_id: str,
    workdir: Path,
    quick: bool,
    evidence_path: Path | None,
    agent_backend: str,
    agent_model: str,
    agent_command: str | None,
    agent_timeout_sec: int,
) -> int:
    registry = Registry(STATE_DIR)
    registry.init()
    groups = _load_groups(GROUPS_JSON)
    if group_id not in groups:
        print(json.dumps({"ok": False, "error": f"unknown group_id: {group_id}"}, indent=2))
        return 1

    group = groups[group_id]
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    actions_path = run_dir / "agent_actions.jsonl"
    metadata_path = run_dir / "run_metadata.json"

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
    if agent_backend == "cursor_sdk":
        _append_jsonl(actions_path, {"step": "run_agent_backend", "backend": "cursor_sdk", "model": agent_model})
        try:
            record = run_cursor_agent_cycle(
                workdir=workdir,
                run_dir=run_dir,
                group=group,
                intent_packet=prior_intent,
                run_id=run_id,
                model=agent_model,
            )
            (run_dir / "agent_stdout.txt").write_text(record.summary, encoding="utf-8")
            (run_dir / "agent_stderr.txt").write_text("", encoding="utf-8")
        except AgentBackendError as exc:
            _write_json(
                metadata_path,
                {
                    "run_id": run_id,
                    "group": asdict(group),
                    "status": "error",
                    "failure_class": "invalid_cycle",
                    "error": str(exc),
                    "agent_backend": "cursor_sdk",
                },
            )
            registry.record_run(
                run_id=run_id,
                group_id=group.id,
                branch=group.branch,
                status="error",
                failure_class="invalid_cycle",
                metrics={},
            )
            registry.record_transition(
                TransitionEvent(
                    run_id=run_id,
                    group_id=group.id,
                    from_state="running_agent_cycle",
                    to_state="blocked",
                    reason="cursor_agent_backend_failed",
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
                        "run_dir": str(run_dir.relative_to(REPO_ROOT)),
                    },
                    indent=2,
                )
            )
            return 1
    elif agent_command:
        _append_jsonl(actions_path, {"step": "run_agent_backend", "backend": "command", "command": agent_command})
        agent_proc = subprocess.run(
            _normalize_python_command(agent_command),
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
            timeout=agent_timeout_sec,
            env={
                **os.environ,
                "HIAGENTRESEARCH_RUN_ID": run_id,
                "HIAGENTRESEARCH_GROUP_ID": group.id,
                "HIAGENTRESEARCH_STATE_DIR": str(STATE_DIR.resolve()),
            },
        )
        (run_dir / "agent_stdout.txt").write_text(agent_proc.stdout, encoding="utf-8")
        (run_dir / "agent_stderr.txt").write_text(agent_proc.stderr, encoding="utf-8")
        if agent_proc.returncode != 0:
            _write_json(
                metadata_path,
                {
                    "run_id": run_id,
                    "group": asdict(group),
                    "status": "error",
                    "failure_class": "invalid_cycle",
                    "error": "agent command failed before evaluation",
                    "agent_exit_code": agent_proc.returncode,
                },
            )
            registry.record_run(
                run_id=run_id,
                group_id=group.id,
                branch=group.branch,
                status="error",
                failure_class="invalid_cycle",
                metrics={},
            )
            registry.record_transition(
                TransitionEvent(
                    run_id=run_id,
                    group_id=group.id,
                    from_state="running_agent_cycle",
                    to_state="blocked",
                    reason="agent_command_failed",
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
                        "run_dir": str(run_dir.relative_to(REPO_ROOT)),
                    },
                    indent=2,
                )
            )
            return 1
    else:
        _append_jsonl(actions_path, {"step": "skip_agent_command", "reason": "no command provided"})

    _append_jsonl(actions_path, {"step": "validate_plan_before_eval"})
    valid_contract, contract_error = _validate_agent_intent_contract(run_dir=run_dir, group=group, run_id=run_id)
    if not valid_contract:
        _write_json(
            metadata_path,
            {
                "run_id": run_id,
                "group": asdict(group),
                "status": "error",
                "failure_class": "invalid_cycle",
                "error": contract_error,
                "agent_backend": agent_backend,
            },
        )
        _append_jsonl(actions_path, {"step": "plan_validation_failed", "error": contract_error})
        registry.record_run(
            run_id=run_id,
            group_id=group.id,
            branch=group.branch,
            status="error",
            failure_class="invalid_cycle",
            metrics={},
        )
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
                    "run_dir": str(run_dir.relative_to(REPO_ROOT)),
                },
                indent=2,
            )
        )
        return 1
    _append_jsonl(actions_path, {"step": "plan_validation_passed"})

    cmd = group.evaluation.command
    if quick and group.evaluation.parser == "mnist_json_stdout" and "--quick" not in cmd:
        cmd = f"{cmd} --quick"
    _append_jsonl(actions_path, {"step": "run_evaluation", "command": cmd})

    proc = subprocess.run(
        _normalize_python_command(cmd),
        cwd=workdir,
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
        if normalized.raw.get("tests_passed") is not None:
            metrics["tests_passed"] = float(normalized.raw["tests_passed"])
        if normalized.raw.get("tests_failed") is not None:
            metrics["tests_failed"] = float(normalized.raw["tests_failed"])
        if normalized.raw.get("duration_sec") is not None:
            metrics["duration_sec"] = float(normalized.raw["duration_sec"])
        parsed = normalized.raw
        _write_json(run_dir / "metrics.json", metrics)
        _write_json(
            run_dir / "failure_class.json",
            {"failure_class": failure_class, "exit_code": proc.returncode},
        )
        _write_json(run_dir / "parsed_eval.json", parsed)
    except ArtifactParseError as exc:
        failure_class = classify_non_json_failure(proc.stderr, proc.returncode)
        _write_json(
            run_dir / "failure_class.json",
            {"failure_class": failure_class, "exit_code": proc.returncode, "error": str(exc)},
        )
        _append_jsonl(actions_path, {"step": "parse_failure", "error": str(exc)})

    evidence = {"evidence": []}
    if evidence_path and evidence_path.exists():
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    _write_json(run_dir / "evidence.json", evidence)
    _append_jsonl(actions_path, {"step": "evidence_loaded", "count": len(evidence.get("evidence", []))})

    status = "finished" if passed else "error"
    registry.record_run(
        run_id=run_id,
        group_id=group.id,
        branch=group.branch,
        status=status,
        failure_class=failure_class,
        metrics=metrics,
    )

    prior = prior_intent
    if failure_class != "infra_failure":
        prior.attempt_count += 1
    prior.last_failure_class = failure_class if failure_class != "none" else "none"
    prior.next_action = (
        "continue"
        if passed or failure_class == "infra_failure"
        else ("repair" if failure_class == "code_failure" else "pivot")
    )
    prior.key_evidence_refs = [run_id]
    prior.updated_at = utc_now_iso()
    registry.write_intent_packet(prior)

    registry.record_transition(
        TransitionEvent(
            run_id=run_id,
            group_id=group.id,
            from_state="running_agent_cycle",
            to_state="ready_for_wake" if failure_class in {"none", "code_failure", "eval_failure"} else "blocked",
            reason=f"eval_completed:{failure_class}",
            actor="orchestrator",
        )
    )

    _write_json(
        metadata_path,
        {
            "run_id": run_id,
            "group": asdict(group),
            "status": status,
            "exit_code": proc.returncode,
            "failure_class": failure_class,
            "passed": passed,
        },
    )
    print(
        json.dumps(
            {
                "ok": True,
                "run_id": run_id,
                "status": status,
                "failure_class": failure_class,
                "run_dir": str(run_dir.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )
    return 0 if passed else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase-1 orchestrator for hiagentresearch.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Initialize registry and intent packets.")
    run = sub.add_parser("run-group", help="Run one research group evaluation cycle.")
    run.add_argument("--group-id", required=True)
    run.add_argument("--workdir", default=".")
    run.add_argument("--quick", action="store_true")
    run.add_argument("--evidence-json", type=Path, default=None)
    run.add_argument("--agent-backend", choices=["cursor_sdk", "command", "none"], default="cursor_sdk")
    run.add_argument("--agent-model", default="composer-2.5")
    run.add_argument("--agent-command", default=None, help="Optional command that runs the group agent loop once.")
    run.add_argument("--agent-timeout-sec", type=int, default=900)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "init":
        return init_state()
    if args.cmd == "run-group":
        return run_group(
            group_id=args.group_id,
            workdir=Path(args.workdir).resolve(),
            quick=args.quick,
            evidence_path=args.evidence_json,
            agent_backend=args.agent_backend,
            agent_model=args.agent_model,
            agent_command=args.agent_command,
            agent_timeout_sec=args.agent_timeout_sec,
        )
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
