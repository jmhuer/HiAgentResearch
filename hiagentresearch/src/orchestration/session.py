"""Orchestration session boundaries for dashboard and registry scoping."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

SESSION_META_KEY = "orchestration_session"
SESSION_ARTIFACT = "orchestration_session.json"


def read_session_started_at(path: Path | str) -> str | None:
    session_path = Path(path)
    if not session_path.exists():
        return None
    try:
        payload = json.loads(session_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(payload, dict) and payload.get("started_at"):
        return str(payload["started_at"])
    return None


def parse_iso_timestamp(value: str) -> datetime:
    normalized = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def timestamp_at_or_after(value: str, cutoff: str) -> bool:
    return parse_iso_timestamp(value) >= parse_iso_timestamp(cutoff)
