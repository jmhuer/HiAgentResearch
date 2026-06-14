from dataclasses import dataclass

from hiagentresearch.src.runtime.quality import classify_research_outcome, metrics_meet_expectations


@dataclass
class Bounds:
    min: float | None = None
    max: float | None = None


def test_metrics_meet_expectations() -> None:
    ok, error = metrics_meet_expectations(
        {"accuracy": 0.99, "latency_ms": 2.0},
        {"accuracy": Bounds(min=0.9), "latency_ms": Bounds(max=13.0)},
    )

    assert ok is True
    assert error == ""


def test_metrics_reject_missing_or_out_of_bounds() -> None:
    ok, error = metrics_meet_expectations({"accuracy": 0.5}, {"accuracy": Bounds(min=0.9)})
    assert ok is False
    assert "below minimum" in error

    ok, error = metrics_meet_expectations({}, {"accuracy": Bounds(min=0.99)})
    assert ok is False
    assert "missing expected metric" in error


def test_research_outcome_separates_regression_from_execution_failure() -> None:
    outcome = classify_research_outcome(
        execution_failure_class="none",
        eval_passed=False,
        metrics={"accuracy": 0.89, "latency_ms": 1.7},
        targets={
            "accuracy": Bounds(min=0.985),
            "latency_ms": Bounds(max=13.0),
        },
    )

    assert outcome.research_outcome == "below_targets"
    assert outcome.next_action == "continue"


def test_research_outcome_marks_targets_met() -> None:
    outcome = classify_research_outcome(
        execution_failure_class="none",
        eval_passed=True,
        metrics={"accuracy": 0.99, "latency_ms": 2.0},
        targets={
            "accuracy": Bounds(min=0.985),
            "latency_ms": Bounds(max=13.0),
        },
    )

    assert outcome.research_outcome == "met_targets"


def test_execution_blocked_defaults_to_repair() -> None:
    outcome = classify_research_outcome(
        execution_failure_class="infra_failure",
        eval_passed=False,
        metrics={},
        targets={"accuracy": Bounds(min=0.985)},
    )

    assert outcome.research_outcome == "execution_blocked"
    assert outcome.next_action == "repair"
    assert "infra_failure" in outcome.reason
