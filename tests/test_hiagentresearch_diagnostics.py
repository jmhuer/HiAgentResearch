"""Tests for EvalDiagnostics schema and eval node integration."""

from __future__ import annotations

import json

import pytest

from hiagentresearch.src.core.diagnostics_schema import (
    DiagnosticsValidationError,
    validate_diagnostics,
)
from hiagentresearch.src.eval.node import normalize_eval_node, write_eval_node_artifacts


def test_validate_diagnostics_requires_attachments_when_blocked():
    with pytest.raises(DiagnosticsValidationError):
        validate_diagnostics(
            {"schema_version": 1, "summary": "blocked", "attachments": []},
            execution_blocked=True,
        )


def test_normalize_eval_node_writes_diagnostics(tmp_path):
    stdout = json.dumps(
        {
            "passed": False,
            "execution_passed": False,
            "failure_class": "infra_failure",
            "research_outcome": "execution_blocked",
            "macro_f1": 0.1,
            "diagnostics": {
                "schema_version": 1,
                "summary": "CI eval blocked execution: expected 15 scored matches, got 0.",
                "primary_failure": "TypeError('fps')",
                "coverage": {"expected": 15, "completed": 0, "failed": 15, "unscored": 15},
                "phases": [{"name": "scorer", "exit_code": 0, "error": None}],
                "attachments": [
                    {
                        "name": "runner_log.json",
                        "role": "runner_log",
                        "description": "Per-match inference log",
                    }
                ],
            },
        }
    )
    from hiagentresearch.src.core.config import EvaluationConfig, MetricExpectation

    artifacts = normalize_eval_node(
        stdout=stdout,
        stderr="",
        exit_code=2,
        eval_config=EvaluationConfig(
            entrypoint="x",
            command_template="x",
            targets={"macro_f1": MetricExpectation(min=0.7)},
        ),
    )
    write_eval_node_artifacts(output_dir=tmp_path, artifacts=artifacts)
    diagnostics = json.loads((tmp_path / "diagnostics.json").read_text(encoding="utf-8"))
    failure = json.loads((tmp_path / "failure_class.json").read_text(encoding="utf-8"))
    assert diagnostics["primary_failure"] == "TypeError('fps')"
    assert failure["primary_error"] == "TypeError('fps')"
