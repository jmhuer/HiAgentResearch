from __future__ import annotations

import os
from typing import Any

# SDK defaults: 60s unary, 600s stream. Phase-1 loops routinely exceed 60s wall
# clock; raise both so wait()/ObserveRun cannot cut off a healthy run.
_DEFAULT_UNARY_TIMEOUT_SEC = 1800.0
_DEFAULT_STREAM_TIMEOUT_SEC = 1800.0


def cursor_sdk_client() -> Any:
    """Return a shared-bridge Cursor SDK client with orchestrator timeouts."""
    from cursor_sdk import Client
    from cursor_sdk._client import _default_client

    unary_timeout = _timeout_from_env(
        "HIAGENTRESEARCH_CURSOR_UNARY_TIMEOUT_SEC",
        _DEFAULT_UNARY_TIMEOUT_SEC,
    )
    stream_timeout = _timeout_from_env(
        "HIAGENTRESEARCH_CURSOR_STREAM_TIMEOUT_SEC",
        _DEFAULT_STREAM_TIMEOUT_SEC,
    )
    base: Client = _default_client()
    return base.with_options(
        unary_timeout=unary_timeout,
        stream_timeout=stream_timeout,
    )


def _timeout_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number of seconds, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value
