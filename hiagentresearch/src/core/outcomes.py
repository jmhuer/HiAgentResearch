from __future__ import annotations

from typing import Any

BASELINE_REQUIRED_METRICS = ("accuracy", "latency_ms")

LEGACY_OUTCOME_ALIASES = {
    "improved_baseline": "met_targets",
    "did_not_improve_baseline": "below_targets",
}


def normalize_research_outcome_name(name: str) -> str:
    return LEGACY_OUTCOME_ALIASES.get(str(name), str(name))


def outcome_met_targets(name: str) -> bool:
    return normalize_research_outcome_name(name) == "met_targets"


def baseline_metrics_complete(metrics: dict[str, float]) -> bool:
    return all(name in metrics and metrics[name] is not None for name in BASELINE_REQUIRED_METRICS)


def baseline_metrics_from_eval_payload(payload: dict[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for name in (*BASELINE_REQUIRED_METRICS, "duration_sec"):
        value = payload.get(name)
        if value is not None:
            metrics[name] = float(value)
    report = payload.get("eval_report")
    if isinstance(report, dict):
        for name in BASELINE_REQUIRED_METRICS:
            if name not in metrics and report.get(name) is not None:
                metrics[name] = float(report[name])
    return metrics
