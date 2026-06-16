from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hiagentresearch.src.core.artifact_schema import normalize_eval
from hiagentresearch.src.core.config import EvaluationConfig
from hiagentresearch.src.core.diagnostics_schema import (
    DiagnosticsValidationError,
    diagnostics_from_dict,
)


@dataclass(frozen=True)
class EvalNodeArtifacts:
    metrics: dict[str, float]
    failure_class: str
    exit_code: int
    passed: bool
    parsed: dict[str, Any]
    research_outcome: dict[str, Any]
    diagnostics: dict[str, Any] | None = None


def normalize_eval_node(
    *,
    stdout: str,
    stderr: str,
    exit_code: int,
    eval_config: EvaluationConfig,
) -> EvalNodeArtifacts:
    from hiagentresearch.src.runtime.quality import classify_research_outcome

    metric_names = list(eval_config.targets)
    normalized = normalize_eval(
        stdout=stdout, stderr=stderr, exit_code=exit_code, metric_names=metric_names
    )
    metrics = normalized.to_metrics()
    outcome = classify_research_outcome(
        execution_failure_class=normalized.failure_class,
        eval_passed=normalized.passed,
        metrics=metrics,
        targets=eval_config.targets,
    )
    parsed = dict(normalized.raw)
    research_outcome = outcome.to_dict()
    parsed["research_outcome"] = research_outcome["research_outcome"]

    execution_blocked = not bool(parsed.get("execution_passed", False))
    diagnostics_payload = parsed.get("diagnostics")
    diagnostics: dict[str, Any] | None = None
    if isinstance(diagnostics_payload, dict):
        diagnostics = diagnostics_from_dict(diagnostics_payload, execution_blocked=execution_blocked).to_dict()
    elif execution_blocked:
        raise DiagnosticsValidationError("diagnostics is required when execution_passed is false")

    return EvalNodeArtifacts(
        metrics=metrics,
        failure_class=normalized.failure_class,
        exit_code=exit_code,
        passed=normalized.passed,
        parsed=parsed,
        research_outcome=research_outcome,
        diagnostics=diagnostics,
    )


def write_eval_node_artifacts(*, output_dir: Path, artifacts: EvalNodeArtifacts) -> None:
    failure_payload: dict[str, Any] = {
        "failure_class": artifacts.failure_class,
        "exit_code": artifacts.exit_code,
    }
    if artifacts.diagnostics and artifacts.diagnostics.get("primary_failure"):
        failure_payload["primary_error"] = artifacts.diagnostics["primary_failure"]
    _write_json(output_dir / "metrics.json", artifacts.metrics)
    _write_json(output_dir / "failure_class.json", failure_payload)
    _write_json(output_dir / "research_outcome.json", artifacts.research_outcome)
    parsed = dict(artifacts.parsed)
    if artifacts.diagnostics is not None:
        parsed["diagnostics"] = artifacts.diagnostics
    _write_json(output_dir / "parsed_eval.json", parsed)
    if artifacts.diagnostics is not None:
        _write_json(output_dir / "diagnostics.json", artifacts.diagnostics)


def write_parse_failure_artifacts(
    *,
    output_dir: Path,
    failure_class: str,
    exit_code: int,
    error: str,
) -> dict[str, Any]:
    research_outcome = {
        "research_outcome": "execution_blocked",
        "next_action": "repair",
        "reason": error,
    }
    diagnostics = {
        "schema_version": 1,
        "summary": f"CI eval blocked execution with {failure_class}: {error[:500]}",
        "primary_failure": error,
        "coverage": None,
        "phases": [{"name": "adapter", "exit_code": exit_code, "error": error}],
        "attachments": [
            {
                "name": "stdout.txt",
                "role": "adapter_stdout",
                "description": "Frozen eval adapter stdout",
            },
            {
                "name": "stderr.txt",
                "role": "adapter_stderr",
                "description": "Frozen eval adapter stderr",
            },
        ],
    }
    _write_json(
        output_dir / "failure_class.json",
        {"failure_class": failure_class, "exit_code": exit_code, "error": error, "primary_error": error},
    )
    _write_json(output_dir / "research_outcome.json", research_outcome)
    _write_json(output_dir / "diagnostics.json", diagnostics)
    return research_outcome


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
