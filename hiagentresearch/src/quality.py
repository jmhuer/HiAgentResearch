from __future__ import annotations

from typing import Protocol


class MetricBounds(Protocol):
    min: float | None
    max: float | None


def metrics_meet_expectations(metrics: dict[str, float], expectations: dict[str, MetricBounds]) -> tuple[bool, str]:
    for name, bounds in expectations.items():
        if name not in metrics:
            return False, f"missing expected metric: {name}"
        value = metrics[name]
        if bounds.min is not None and value < bounds.min:
            return False, f"metric {name}={value} below minimum {bounds.min}"
        if bounds.max is not None and value > bounds.max:
            return False, f"metric {name}={value} above maximum {bounds.max}"
    return True, ""
