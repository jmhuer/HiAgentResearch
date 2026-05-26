from hiagentresearch.src.artifact_schema import (
    classify_non_json_failure,
    normalize_eval,
    normalize_phase1_eval_json,
    normalize_mnist_eval,
    normalize_pytest_eval,
)


def test_normalize_mnist_eval_passes() -> None:
    stdout = '{"passed": true, "accuracy": 0.991, "latency_ms": 11.2}'
    result = normalize_mnist_eval(stdout=stdout, exit_code=0)
    assert result.passed is True
    assert result.failure_class == "none"
    assert result.to_metrics()["accuracy"] == 0.991


def test_normalize_mnist_eval_eval_failure() -> None:
    stdout = '{"passed": false, "accuracy": 0.982, "latency_ms": 13.9}'
    result = normalize_mnist_eval(stdout=stdout, exit_code=2)
    assert result.passed is False
    assert result.failure_class == "eval_failure"


def test_classify_non_json_failure_module_not_found() -> None:
    stderr = "ModuleNotFoundError: No module named 'sklearn'"
    assert classify_non_json_failure(stderr=stderr, exit_code=1) == "infra_failure"


def test_normalize_pytest_eval_passes() -> None:
    stdout = "..                                                                       [100%]\n2 passed in 3.08s\n"
    result = normalize_pytest_eval(stdout=stdout, stderr="", exit_code=0)
    assert result.passed is True
    assert result.failure_class == "none"
    assert result.raw["tests_passed"] == 2


def test_normalize_eval_dispatch_pytest() -> None:
    stdout = ".                                                                        [100%]\n1 passed in 0.02s\n"
    result = normalize_eval(parser="pytest_exit_code", stdout=stdout, stderr="", exit_code=0)
    assert result.passed is True


def test_normalize_phase1_eval_json() -> None:
    stdout = '{"passed": true, "tests_passed": 2, "tests_failed": 0, "duration_sec": 3.1}'
    result = normalize_phase1_eval_json(stdout=stdout, exit_code=0)
    assert result.passed is True
    assert result.failure_class == "none"


def test_normalize_eval_dispatch_phase1_json() -> None:
    stdout = '{"passed": true, "tests_passed": 2, "tests_failed": 0, "duration_sec": 3.1}'
    result = normalize_eval(parser="mnist_phase1_json_stdout", stdout=stdout, stderr="", exit_code=0)
    assert result.passed is True
