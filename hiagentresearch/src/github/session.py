"""Resolve orchestration session boundaries from GitHub Actions artifacts."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from hiagentresearch.src.core.config import HiAgentResearchConfig
from hiagentresearch.src.orchestration.session import SESSION_ARTIFACT, SESSION_META_KEY, read_session_started_at


def read_session_from_evals_db(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (SESSION_META_KEY,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        payload = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("started_at"):
        return str(payload["started_at"])
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'baseline_snapshot'",
    ).fetchone()
    if not row:
        return None
    try:
        baseline = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return None
    if isinstance(baseline, dict) and baseline.get("created_at"):
        return str(baseline["created_at"])
    return None


def resolve_github_session_started_at(config: HiAgentResearchConfig) -> str | None:
    """Read the current session start from the latest successful baseline eval on main."""
    ref = config.orchestration.baseline_ref
    workflow = config.github.workflow_name
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            workflow,
            "--branch",
            ref,
            "--status",
            "success",
            "--limit",
            "15",
            "--json",
            "databaseId",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    runs = json.loads(result.stdout)
    with tempfile.TemporaryDirectory(prefix="hiagentresearch-session-") as tmp:
        root = Path(tmp)
        for row in runs:
            run_id = str(row.get("databaseId", "")).strip()
            if not run_id:
                continue
            target = root / run_id
            subprocess.run(
                [
                    "gh",
                    "run",
                    "download",
                    run_id,
                    "--pattern",
                    "hiagentresearch-*",
                    "--dir",
                    str(target),
                ],
                check=False,
            )
            for session_path in sorted(target.rglob(SESSION_ARTIFACT)):
                started = read_session_started_at(session_path)
                if started:
                    return started
            for db_path in sorted(target.rglob("evals.db")):
                started = read_session_from_evals_db(db_path)
                if started:
                    return started
    return None


def write_session_artifact(artifact_dir: Path, *, started_at: str) -> None:
    artifact_dir.joinpath(SESSION_ARTIFACT).write_text(
        json.dumps({"started_at": started_at}, indent=2),
        encoding="utf-8",
    )
