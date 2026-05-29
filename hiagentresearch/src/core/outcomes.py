from __future__ import annotations

from collections.abc import Iterable

# Fallback when no targets are configured; real projects derive required metrics
# from `evaluation.targets` via `required_baseline_metrics`.
BASELINE_REQUIRED_METRICS = ("accuracy", "latency_ms")

LEGACY_OUTCOME_ALIASES = {
    "improved_baseline": "met_targets",
    "did_not_improve_baseline": "below_targets",
}


def required_baseline_metrics(targets: Iterable[str] | None) -> tuple[str, ...]:
    names = tuple(str(name) for name in (targets or ()))
    return names or BASELINE_REQUIRED_METRICS


def normalize_research_outcome_name(name: str) -> str:
    return LEGACY_OUTCOME_ALIASES.get(str(name), str(name))


def outcome_met_targets(name: str) -> bool:
    return normalize_research_outcome_name(name) == "met_targets"


def baseline_metrics_complete(
    metrics: dict[str, float],
    required: Iterable[str] = BASELINE_REQUIRED_METRICS,
) -> bool:
    names = tuple(str(name) for name in required) or BASELINE_REQUIRED_METRICS
    return all(name in metrics and metrics[name] is not None for name in names)
