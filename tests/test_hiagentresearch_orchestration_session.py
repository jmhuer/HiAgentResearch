from hiagentresearch.src.orchestration.session import timestamp_at_or_after
from hiagentresearch.src.registry.store import Registry


def test_record_baseline_snapshot_starts_orchestration_session(tmp_path) -> None:
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_baseline_snapshot(
        ref="main",
        metrics={"accuracy": 0.8, "latency_ms": 1.0, "duration_sec": 1.0},
    )
    session = registry.orchestration_session()
    baseline = registry.baseline_snapshot()
    assert session is not None
    assert session["started_at"] == baseline["created_at"]


def test_timestamp_at_or_after_compares_github_and_registry_timestamps() -> None:
    assert timestamp_at_or_after("2026-06-02T05:00:00Z", "2026-06-02T04:00:00+00:00")
    assert not timestamp_at_or_after("2026-06-02T03:00:00Z", "2026-06-02T04:00:00+00:00")
