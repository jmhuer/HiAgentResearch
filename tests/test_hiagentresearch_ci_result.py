import json

from hiagentresearch.src.core.ci_result import CIResult


def test_from_dicts_normalizes_and_defaults() -> None:
    ci = CIResult.from_dicts(
        {"failure_class": "none"},
        {"research_outcome": "improved_baseline", "next_action": "continue"},  # legacy alias
        {"macro_f1": 0.71},
    )
    assert ci.failure_class == "none"
    assert ci.research_outcome == "met_targets"  # alias collapsed
    assert ci.met_targets is True
    assert ci.execution_blocked is False
    assert ci.decision() == "continue"
    assert ci.metrics["macro_f1"] == 0.71


def test_missing_artifacts_fall_back_to_historical_defaults() -> None:
    ci = CIResult.from_dicts({}, {})
    assert ci.failure_class == "infra_failure"
    assert ci.research_outcome == "unknown"
    assert ci.execution_blocked is True
    # No next_action, not met, blocked -> repair.
    assert ci.decision() == "repair"


def test_decision_continue_when_below_targets_but_not_blocked() -> None:
    ci = CIResult.from_dicts({"failure_class": "none"}, {"research_outcome": "below_targets"})
    assert ci.decision() == "continue"


def test_first_reason_prefers_primary_error() -> None:
    ci = CIResult.from_dicts(
        {"failure_class": "code_failure", "primary_error": "gate failed", "error": "x"},
        {"research_outcome": "execution_blocked", "reason": "y"},
    )
    assert ci.first_reason() == "gate failed"


def test_from_ci_dir_reads_all_three_files(tmp_path) -> None:
    (tmp_path / "failure_class.json").write_text(json.dumps({"failure_class": "none"}), encoding="utf-8")
    (tmp_path / "research_outcome.json").write_text(
        json.dumps({"research_outcome": "met_targets", "next_action": "done"}), encoding="utf-8"
    )
    (tmp_path / "metrics.json").write_text(json.dumps({"accuracy": 0.99}), encoding="utf-8")
    ci = CIResult.from_ci_dir(tmp_path)
    assert ci.met_targets is True
    assert ci.decision() == "done"
    assert ci.metrics["accuracy"] == 0.99
