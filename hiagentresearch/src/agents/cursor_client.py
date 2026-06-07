from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import Any

# SDK defaults: 60s unary, 600s stream. Research loops routinely exceed 60s wall
# clock; raise both so wait()/ObserveRun cannot cut off a healthy run.
_DEFAULT_UNARY_TIMEOUT_SEC = 1800.0
_DEFAULT_STREAM_TIMEOUT_SEC = 1800.0


@contextlib.contextmanager
def cursor_sdk_client(
    workspace: str,
    *,
    unary_timeout_default: float = _DEFAULT_UNARY_TIMEOUT_SEC,
    stream_timeout_default: float = _DEFAULT_STREAM_TIMEOUT_SEC,
) -> Iterator[Any]:
    """Yield a Cursor SDK client (orchestrator timeouts) via the public bridge API.

    Uses ``Client.launch_bridge`` + ``Client.with_options`` — both public — and
    manages the bridge lifecycle as a context manager, rather than reaching into
    SDK internals. The caller keeps the client alive for the duration of the agent
    run by using ``with cursor_sdk_client(...) as client``. Timeout defaults come
    from config; the matching env vars still override.
    """
    from cursor_sdk import Client

    unary_timeout = _timeout_from_env(
        "HIAGENTRESEARCH_CURSOR_UNARY_TIMEOUT_SEC",
        unary_timeout_default,
    )
    stream_timeout = _timeout_from_env(
        "HIAGENTRESEARCH_CURSOR_STREAM_TIMEOUT_SEC",
        stream_timeout_default,
    )
    with Client.launch_bridge(workspace=workspace) as base:
        yield base.with_options(unary_timeout=unary_timeout, stream_timeout=stream_timeout)


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
