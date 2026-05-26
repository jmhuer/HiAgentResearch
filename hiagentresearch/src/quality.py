from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(slots=True)
class ResearchOutcome:
    research_outcome: str
    improved_baseline: bool
    metrics_ok: bool
    next_action: str
    reason: str

    def to_dict(self) -> dict[str, bool | str]:
        return {
            "research_outcome": self.research_outcome,
            "improved_baseline": self.improved_baseline,
            "metrics_ok": self.metrics_ok,
            "next_action": self.next_action,
            "reason": self.reason,
        }


def classify_research_outcome(
    *,
    execution_failure_class: str,
    eval_passed: bool,
    metrics: dict[str, float],
    expectations: dict[str, MetricBounds],
) -> ResearchOutcome:
    if execution_failure_class != "none":
        return ResearchOutcome(
            research_outcome="execution_blocked",
            improved_baseline=False,
            metrics_ok=False,
            next_action="repair" if execution_failure_class == "code_failure" else "continue",
            reason=f"execution did not complete cleanly: {execution_failure_class}",
        )

    metrics_ok, metrics_error = metrics_meet_expectations(metrics, expectations)
    if eval_passed and metrics_ok:
        return ResearchOutcome(
            research_outcome="improved_baseline",
            improved_baseline=True,
            metrics_ok=True,
            next_action="continue",
            reason="configured improvement metrics were met",
        )

    reason = metrics_error or "eval completed but did not report baseline improvement"
    return ResearchOutcome(
        research_outcome="did_not_improve_baseline",
        improved_baseline=False,
        metrics_ok=False,
        next_action="continue",
        reason=reason,
    )
