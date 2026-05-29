import pytest

from hiagentresearch.src.agents.cursor_client import _timeout_from_env


def test_timeout_from_env_uses_default(monkeypatch) -> None:
    monkeypatch.delenv("HIAGENTRESEARCH_CURSOR_UNARY_TIMEOUT_SEC", raising=False)
    assert _timeout_from_env("HIAGENTRESEARCH_CURSOR_UNARY_TIMEOUT_SEC", 1800.0) == 1800.0


def test_timeout_from_env_parses_value(monkeypatch) -> None:
    monkeypatch.setenv("HIAGENTRESEARCH_CURSOR_UNARY_TIMEOUT_SEC", "3600")
    assert _timeout_from_env("HIAGENTRESEARCH_CURSOR_UNARY_TIMEOUT_SEC", 1800.0) == 3600.0


def test_timeout_from_env_rejects_invalid(monkeypatch) -> None:
    monkeypatch.setenv("HIAGENTRESEARCH_CURSOR_UNARY_TIMEOUT_SEC", "nope")
    with pytest.raises(ValueError, match="must be a positive number"):
        _timeout_from_env("HIAGENTRESEARCH_CURSOR_UNARY_TIMEOUT_SEC", 1800.0)
