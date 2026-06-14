import json

from hiagentresearch.src.core.artifact_schema import (
    classify_non_json_failure,
    normalize_eval,
)
from hiagentresearch.src.eval.node import write_parse_failure_artifacts

CANONICAL_METRICS = ("accuracy", "latency_ms")


def test_normalize_eval_passes() -> None:
    stdout = '{"passed": true, "accuracy": 0.991, "latency_ms": 11.2}'
    result = normalize_eval(stdout=stdout, stderr="", exit_code=0, metric_names=CANONICAL_METRICS)
    assert result.passed is True
    assert result.failure_class == "none"
    assert result.to_metrics()["accuracy"] == 0.991
    assert result.to_metrics()["latency_ms"] == 11.2


def test_normalize_eval_regression_is_clean_execution() -> None:
    stdout = '{"passed": false, "accuracy": 0.982, "latency_ms": 13.9}'
    result = normalize_eval(stdout=stdout, stderr="", exit_code=2, metric_names=CANONICAL_METRICS)
    assert result.passed is False
    # exit_code 2 with all target metrics present means a clean below-targets run.
    assert result.failure_class == "none"


def test_normalize_eval_captures_all_numeric_metrics_generically() -> None:
    # The framework captures every numeric metric the eval reports (project-agnostic),
    # not just the configured targets; control keys are excluded.
    stdout = '{"passed": true, "accuracy": 0.99, "latency_ms": 2.0, "f1": 0.7}'
    result = normalize_eval(stdout=stdout, stderr="", exit_code=0, metric_names=("accuracy",))
    assert result.to_metrics() == {"accuracy": 0.99, "latency_ms": 2.0, "f1": 0.7}
    assert "passed" not in result.to_metrics()


def test_normalize_eval_execution_blocked_marks_code_failure() -> None:
    stdout = '{"passed": false, "execution_passed": false, "failure_class": "code_failure"}'
    result = normalize_eval(stdout=stdout, stderr="", exit_code=2, metric_names=CANONICAL_METRICS)
    assert result.passed is False
    assert result.failure_class == "code_failure"


def test_normalize_eval_keeps_full_payload_in_raw() -> None:
    stdout = (
        '{"passed": true, "execution_passed": true, "accuracy": 0.99, '
        '"latency_ms": 2.0, "tests_passed": 2, "tests_failed": 0, "duration_sec": 3.1}'
    )
    result = normalize_eval(stdout=stdout, stderr="", exit_code=0, metric_names=CANONICAL_METRICS)
    assert result.passed is True
    assert result.failure_class == "none"
    assert result.raw["tests_passed"] == 2


def test_classify_non_json_failure_module_not_found() -> None:
    stderr = "ModuleNotFoundError: No module named 'sklearn'"
    assert classify_non_json_failure(stderr=stderr, exit_code=1) == "infra_failure"


def test_write_parse_failure_artifacts_defaults_to_repair(tmp_path) -> None:
    outcome = write_parse_failure_artifacts(
        output_dir=tmp_path,
        failure_class="infra_failure",
        exit_code=125,
        error="eval command timed out",
    )

    assert outcome["research_outcome"] == "execution_blocked"
    assert outcome["next_action"] == "repair"
    written = json.loads((tmp_path / "research_outcome.json").read_text(encoding="utf-8"))
    assert written["next_action"] == "repair"
