from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from hiagentresearch.src.models import IntentPacket, TransitionEvent, utc_now_iso


SCHEMA_VERSION = 2


class Registry:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.intent_dir = self.state_dir / "intent_packets"
        self.intent_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.state_dir / "events.jsonl"
        self.db_path = self.state_dir / "evals.db"

    def init(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    commit_sha TEXT,
                    workflow_run_id TEXT,
                    correlation_id TEXT DEFAULT '',
                    status TEXT NOT NULL,
                    failure_class TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    run_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transitions (
                    run_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, artifact_path)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS intent_packets (
                    group_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._migrate(conn)
            conn.commit()
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "correlation_id" not in existing_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN correlation_id TEXT DEFAULT ''")

        # Older prototype registries allowed repeated metric rows. Collapse them
        # before adding the canonical uniqueness invariant.
        conn.execute(
            """
            DELETE FROM metrics
            WHERE rowid NOT IN (
                SELECT MAX(rowid)
                FROM metrics
                GROUP BY run_id, metric_name
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_metrics_run_name
            ON metrics(run_id, metric_name)
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def append_event(self, event: dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_redact_secrets(event), ensure_ascii=True) + "\n")

    def write_intent_packet(self, packet: IntentPacket) -> Path:
        path = self.intent_dir / f"{packet.group_id}.json"
        payload = packet.to_dict()
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO intent_packets (group_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (packet.group_id, json.dumps(payload, sort_keys=True), packet.updated_at),
            )
            conn.commit()
        finally:
            conn.close()
        return path

    def read_intent_packet(self, group_id: str) -> IntentPacket | None:
        path = self.intent_dir / f"{group_id}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return IntentPacket(**payload)

    def record_run(
        self,
        *,
        run_id: str,
        group_id: str,
        branch: str,
        status: str,
        failure_class: str,
        metrics: dict[str, float],
        commit_sha: str = "",
        workflow_run_id: str = "",
        correlation_id: str = "",
    ) -> None:
        now = utc_now_iso()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                (
                    run_id,
                    group_id,
                    branch,
                    commit_sha,
                    workflow_run_id,
                    correlation_id,
                    status,
                    failure_class,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    group_id,
                    branch,
                    commit_sha,
                    workflow_run_id,
                    correlation_id,
                    status,
                    failure_class,
                    now,
                ),
            )
            for name, value in metrics.items():
                conn.execute(
                    """
                    INSERT INTO metrics (run_id, metric_name, metric_value, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(run_id, metric_name)
                    DO UPDATE SET metric_value=excluded.metric_value, created_at=excluded.created_at
                    """,
                    (run_id, name, float(value), now),
                )
            conn.commit()
        finally:
            conn.close()

    def record_artifact(
        self,
        *,
        run_id: str,
        artifact_path: Path,
        artifact_type: str,
        base_dir: Path | None = None,
    ) -> None:
        now = utc_now_iso()
        path = artifact_path.resolve()
        if base_dir:
            try:
                display_path = str(path.relative_to(base_dir.resolve()))
            except ValueError:
                display_path = str(path)
        else:
            display_path = str(path)
        payload = path.read_bytes()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts
                (run_id, artifact_path, artifact_type, sha256, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, display_path, artifact_type, _sha256(payload), len(payload), now),
            )
            conn.commit()
        finally:
            conn.close()

    def record_artifacts(
        self,
        *,
        run_id: str,
        artifact_paths: list[Path],
        artifact_type: str,
        base_dir: Path | None = None,
    ) -> None:
        for path in artifact_paths:
            if path.exists() and path.is_file():
                self.record_artifact(
                    run_id=run_id,
                    artifact_path=path,
                    artifact_type=artifact_type,
                    base_dir=base_dir,
                )

    def record_transition(self, transition: TransitionEvent) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO transitions
                (run_id, group_id, from_state, to_state, reason, actor, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition.run_id,
                    transition.group_id,
                    transition.from_state,
                    transition.to_state,
                    transition.reason,
                    transition.actor,
                    transition.timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.append_event({"event_type": "transition", **transition.to_dict()})

    def schema_version(self) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def latest_run(self, group_id: str | None = None) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if group_id:
                row = conn.execute(
                    "SELECT * FROM runs WHERE group_id = ? ORDER BY created_at DESC LIMIT 1",
                    (group_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def metrics_for_run(self, run_id: str) -> dict[str, float]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT metric_name, metric_value FROM metrics WHERE run_id = ? ORDER BY metric_name",
                (run_id,),
            ).fetchall()
            return {name: float(value) for name, value in rows}
        finally:
            conn.close()

    def artifacts_for_run(self, run_id: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY artifact_path",
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(token in key.lower() for token in ("secret", "token", "api_key", "password")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value
