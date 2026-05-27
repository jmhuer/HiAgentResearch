from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MetricBounds(Protocol):
    min: float | None
    max: float | None


def metrics_meet_expectations(metrics: dict[str, float], targets: dict[str, MetricBounds]) -> tuple[bool, str]:
    for name, bounds in targets.items():
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
    next_action: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "research_outcome": self.research_outcome,
            "next_action": self.next_action,
            "reason": self.reason,
        }


def classify_research_outcome(
    *,
    execution_failure_class: str,
    eval_passed: bool,
    metrics: dict[str, float],
    targets: dict[str, MetricBounds],
) -> ResearchOutcome:
    if execution_failure_class != "none":
        return ResearchOutcome(
            research_outcome="execution_blocked",
            next_action="repair" if execution_failure_class == "code_failure" else "continue",
            reason=f"execution did not complete cleanly: {execution_failure_class}",
        )

    targets_met, targets_error = metrics_meet_expectations(metrics, targets)
    if eval_passed and targets_met:
        return ResearchOutcome(
            research_outcome="met_targets",
            next_action="continue",
            reason="configured targets were met",
        )

    reason = targets_error or "eval completed but configured targets were not met"
    return ResearchOutcome(
        research_outcome="below_targets",
        next_action="continue",
        reason=reason,
    )
