from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


class ArtifactParseError(ValueError):
    """Raised when eval output cannot be normalized."""


@dataclass(slots=True)
class NormalizedEvalResult:
    passed: bool
    failure_class: str
    metrics: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_metrics(self) -> dict[str, float]:
        return dict(self.metrics)


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


def classify_failure(exit_code: int, payload: dict[str, Any], metric_names: Iterable[str] = ()) -> str:
    if exit_code == 0:
        return "none"
    if payload.get("execution_passed") is False:
        return str(payload.get("failure_class") or "code_failure")
    if "error" in payload and "missing checkpoint" in str(payload.get("error", "")).lower():
        return "code_failure"
    names = set(metric_names)
    if exit_code == 2 and names and names.issubset(payload):
        return "none"
    if exit_code == 2:
        return "eval_failure"
    return "infra_failure"


def normalize_eval(
    *,
    stdout: str,
    stderr: str,
    exit_code: int,
    metric_names: Iterable[str] = (),
) -> NormalizedEvalResult:
    """Read a canonical JSON eval report from stdout.

    Canonical JSON is the single universal eval contract: a top-level object with
    ``passed`` / ``execution_passed`` health flags and metric keys. Which metric
    keys matter is declared by the project's ``evaluation.targets``; project-
    specific output shaping belongs in the frozen eval adapter, not here.
    """
    names = list(metric_names)
    payload = _extract_json(stdout)
    failure_class = classify_failure(exit_code, payload, names)
    metrics: dict[str, float] = {}
    for name in names:
        value = _as_float_or_none(payload.get(name))
        if value is not None:
            metrics[name] = value
    return NormalizedEvalResult(
        passed=bool(payload.get("passed", False)) and exit_code == 0,
        failure_class=failure_class,
        metrics=metrics,
        raw=payload,
    )


def classify_non_json_failure(stderr: str, exit_code: int) -> str:
    text = (stderr or "").lower()
    if "modulenotfounderror" in text:
        return "infra_failure"
    if "traceback" in text:
        return "code_failure"
    if exit_code == 2:
        return "eval_failure"
    return "infra_failure"


def _as_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
