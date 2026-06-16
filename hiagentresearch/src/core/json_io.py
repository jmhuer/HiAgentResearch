"""Small JSON read helpers shared across the runtime.

Two patterns recurred at several call sites: a *soft* file read that degrades to an
empty object on a missing/corrupt file (for already-validated or optional artifacts),
and extracting the last JSON object embedded in a noisy text stream (agent stdout).
Centralizing them keeps the parsing rules in one place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_object(path: Path) -> dict[str, Any]:
    """Read a JSON object from ``path``; return ``{}`` if missing, corrupt, or not an object.

    Soft by design — callers use this only for artifacts whose presence/validity was
    already enforced upstream, or that are genuinely optional. It is not a substitute
    for strict parsing where a parse error must surface to the user.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_last_json_object(text: str) -> dict[str, Any] | None:
    """Return the last top-level JSON object embedded in ``text``, or None.

    Scans backwards for ``{`` and attempts a decode at each candidate, so a trailing
    JSON summary survives leading log noise.
    """
    decoder = json.JSONDecoder()
    idx = len(text)
    while idx > 0:
        idx = text.rfind("{", 0, idx)
        if idx < 0:
            return None
        try:
            obj, _end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx -= 1
            continue
        if isinstance(obj, dict):
            return obj
        idx -= 1
    return None
