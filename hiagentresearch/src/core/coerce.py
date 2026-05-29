"""Small value coercions shared by the registry and manifest writers.

These keep JSON-sourced values (manifests, intent files) normalized in one place
instead of being re-implemented per module.
"""

from __future__ import annotations

from typing import Any


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def as_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
