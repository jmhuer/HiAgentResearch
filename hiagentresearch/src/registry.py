from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from hiagentresearch.src.models import IntentPacket, TransitionEvent, utc_now_iso


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    commit_sha TEXT,
                    workflow_run_id TEXT,
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
            conn.commit()
        finally:
            conn.close()

    def append_event(self, event: dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=True) + "\n")

    def write_intent_packet(self, packet: IntentPacket) -> Path:
        path = self.intent_dir / f"{packet.group_id}.json"
        path.write_text(json.dumps(packet.to_dict(), indent=2), encoding="utf-8")
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
    ) -> None:
        now = utc_now_iso()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                (run_id, group_id, branch, commit_sha, workflow_run_id, status, failure_class, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, group_id, branch, commit_sha, workflow_run_id, status, failure_class, now),
            )
            for name, value in metrics.items():
                conn.execute(
                    "INSERT INTO metrics (run_id, metric_name, metric_value, created_at) VALUES (?, ?, ?, ?)",
                    (run_id, name, float(value), now),
                )
            conn.commit()
        finally:
            conn.close()

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
