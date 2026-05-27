from __future__ import annotations

import os
from pathlib import Path

from hiagentresearch.src.paths import REPO_ROOT

CREDENTIALS_DIR = REPO_ROOT / "credentials"


def ensure_cursor_api_key() -> None:
    if os.environ.get("CURSOR_API_KEY", "").strip():
        return
    for filename in ("cursor_secret.txt", "CURSOR_API_KEY.txt"):
        path = CREDENTIALS_DIR / filename
        if path.exists():
            os.environ["CURSOR_API_KEY"] = path.read_text(encoding="utf-8").strip()
            return
