from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ArtifactParseError(ValueError):
    """Raised when eval output cannot be normalized."""


@dataclass(slots=True)
class NormalizedEvalResult:
    passed: bool
    accuracy: float | None
    latency_ms: float | None
    failure_class: str
    raw: dict[str, Any]

    def to_metrics(self) -> dict[str, float]:
        metrics: dict[str, float] = {}
        if self.accuracy is not None:
            metrics["accuracy"] = self.accuracy
        if self.latency_ms is not None:
            metrics["latency_ms"] = self.latency_ms
        return metrics


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ArtifactParseError("Empty eval output.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some tools print logs before JSON; parse from first '{'.
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ArtifactParseError(f"Could not parse JSON payload: {exc}") from exc
        raise ArtifactParseError("Could not locate JSON payload in eval output.")


def classify_failure(exit_code: int, payload: dict[str, Any]) -> str:
    if exit_code == 0:
        return "none"
    if "error" in payload and "missing checkpoint" in str(payload.get("error", "")).lower():
        return "code_failure"
    if exit_code == 2:
        return "eval_failure"
    return "infra_failure"


def normalize_mnist_eval(stdout: str, exit_code: int) -> NormalizedEvalResult:
    payload = _extract_json(stdout)
    failure_class = classify_failure(exit_code, payload)
    passed = bool(payload.get("passed", False)) and exit_code == 0
    return NormalizedEvalResult(
        passed=passed,
        accuracy=_as_float_or_none(payload.get("accuracy")),
        latency_ms=_as_float_or_none(payload.get("latency_ms")),
        failure_class=failure_class,
        raw=payload,
    )


def normalize_pytest_eval(stdout: str, stderr: str, exit_code: int) -> NormalizedEvalResult:
    tests_passed = _extract_pytest_pass_count(stdout)
    failure_class = "none" if exit_code == 0 else ("eval_failure" if tests_passed is not None else "code_failure")
    return NormalizedEvalResult(
        passed=exit_code == 0,
        accuracy=None,
        latency_ms=None,
        failure_class=failure_class,
        raw={"stdout": stdout, "stderr": stderr, "tests_passed": tests_passed},
    )


def normalize_eval(parser: str, stdout: str, stderr: str, exit_code: int) -> NormalizedEvalResult:
    if parser == "mnist_json_stdout":
        return normalize_mnist_eval(stdout=stdout, exit_code=exit_code)
    if parser == "mnist_phase1_json_stdout":
        return normalize_phase1_eval_json(stdout=stdout, exit_code=exit_code)
    if parser == "pytest_exit_code":
        return normalize_pytest_eval(stdout=stdout, stderr=stderr, exit_code=exit_code)
    raise ArtifactParseError(f"Unknown parser profile: {parser}")


def classify_non_json_failure(stderr: str, exit_code: int) -> str:
    text = (stderr or "").lower()
    if "modulenotfounderror" in text:
        return "infra_failure"
    if "traceback" in text:
        return "code_failure"
    if exit_code == 2:
        return "eval_failure"
    return "infra_failure"


def _extract_pytest_pass_count(stdout: str) -> int | None:
    for line in stdout.splitlines():
        line = line.strip().lower()
        if " passed" in line:
            token = line.split(" passed", 1)[0].split()[-1]
            if token.isdigit():
                return int(token)
    return None


def normalize_phase1_eval_json(stdout: str, exit_code: int) -> NormalizedEvalResult:
    payload = _extract_json(stdout)
    failure_class = "none" if exit_code == 0 else ("eval_failure" if exit_code == 2 else "code_failure")
    return NormalizedEvalResult(
        passed=bool(payload.get("passed", False)) and exit_code == 0,
        accuracy=_as_float_or_none(payload.get("accuracy")),
        latency_ms=_as_float_or_none(payload.get("latency_ms")),
        failure_class=failure_class,
        raw=payload,
    )


def _as_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
