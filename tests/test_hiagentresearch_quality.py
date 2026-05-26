from dataclasses import dataclass

from hiagentresearch.src.quality import metrics_meet_expectations


@dataclass
class Bounds:
    min: float | None = None
    max: float | None = None


def test_metrics_meet_expectations() -> None:
    ok, error = metrics_meet_expectations({"tests_passed": 2.0}, {"tests_passed": Bounds(min=1)})

    assert ok is True
    assert error == ""


def test_metrics_reject_missing_or_out_of_bounds() -> None:
    ok, error = metrics_meet_expectations({"tests_passed": 0.0}, {"tests_passed": Bounds(min=1)})
    assert ok is False
    assert "below minimum" in error

    ok, error = metrics_meet_expectations({}, {"accuracy": Bounds(min=0.99)})
    assert ok is False
    assert "missing expected metric" in error
