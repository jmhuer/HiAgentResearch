import pytest


@pytest.fixture(autouse=True)
def skip_dashboard_baseline_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hiagentresearch.src.dashboard.build.ensure_baseline_snapshot",
        lambda registry, config: None,
    )
