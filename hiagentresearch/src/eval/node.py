from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hiagentresearch.src.core.artifact_schema import normalize_eval
from hiagentresearch.src.core.config import EvaluationConfig


@dataclass(frozen=True)
class EvalNodeArtifacts:
    metrics: dict[str, float]
    failure_class: str
    exit_code: int
    passed: bool
    parsed: dict[str, Any]
    research_outcome: dict[str, Any]


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
    if normalized.raw.get("duration_sec") is not None:
        metrics["duration_sec"] = float(normalized.raw["duration_sec"])
    outcome = classify_research_outcome(
        execution_failure_class=normalized.failure_class,
        eval_passed=normalized.passed,
        metrics=metrics,
        targets=eval_config.targets,
    )
    parsed = dict(normalized.raw)
    research_outcome = outcome.to_dict()
    parsed["research_outcome"] = research_outcome["research_outcome"]
    return EvalNodeArtifacts(
        metrics=metrics,
        failure_class=normalized.failure_class,
        exit_code=exit_code,
        passed=normalized.passed,
        parsed=parsed,
        research_outcome=research_outcome,
    )


def write_eval_node_artifacts(*, output_dir: Path, artifacts: EvalNodeArtifacts) -> None:
    _write_json(output_dir / "metrics.json", artifacts.metrics)
    _write_json(
        output_dir / "failure_class.json",
        {"failure_class": artifacts.failure_class, "exit_code": artifacts.exit_code},
    )
    _write_json(output_dir / "research_outcome.json", artifacts.research_outcome)
    _write_json(output_dir / "parsed_eval.json", artifacts.parsed)


def write_parse_failure_artifacts(
    *,
    output_dir: Path,
    failure_class: str,
    exit_code: int,
    error: str,
) -> dict[str, Any]:
    research_outcome = {
        "research_outcome": "execution_blocked",
        "next_action": "repair" if failure_class == "code_failure" else "continue",
        "reason": error,
    }
    _write_json(
        output_dir / "failure_class.json",
        {"failure_class": failure_class, "exit_code": exit_code, "error": error},
    )
    _write_json(output_dir / "research_outcome.json", research_outcome)
    return research_outcome


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
