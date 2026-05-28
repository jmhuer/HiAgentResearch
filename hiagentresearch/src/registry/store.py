from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from hiagentresearch.src.core.models import IntentPacket, TransitionEvent, utc_now_iso
from hiagentresearch.src.core.outcomes import normalize_research_outcome_name, outcome_met_targets


SCHEMA_VERSION = 5


class Registry:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_outcomes (
                    run_id TEXT PRIMARY KEY,
                    research_outcome TEXT NOT NULL,
                    improved_baseline INTEGER NOT NULL,
                    metrics_ok INTEGER NOT NULL,
                    next_action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    run_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    loop_index INTEGER,
                    hypothesis_id TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    target_files_json TEXT NOT NULL,
                    planned_code_changes_json TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_group_created ON runs(group_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_correlation ON runs(correlation_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name_run ON metrics(metric_name, run_id)")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outcomes_outcome
            ON research_outcomes(research_outcome, improved_baseline)
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_group ON experiments(group_id, loop_index)")
        experiment_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(experiments)").fetchall()
        }
        for column, ddl in (
            ("lineage_mode", "ALTER TABLE experiments ADD COLUMN lineage_mode TEXT"),
            ("lineage_parent_group_id", "ALTER TABLE experiments ADD COLUMN lineage_parent_group_id TEXT"),
            ("lineage_anchor_sha", "ALTER TABLE experiments ADD COLUMN lineage_anchor_sha TEXT"),
            ("lineage_anchor_policy", "ALTER TABLE experiments ADD COLUMN lineage_anchor_policy TEXT"),
            ("lineage_parent_anchor_step", "ALTER TABLE experiments ADD COLUMN lineage_parent_anchor_step INTEGER"),
        ):
            if column not in experiment_columns:
                conn.execute(ddl)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def write_intent_packet(self, packet: IntentPacket) -> None:
        payload = packet.to_dict()
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

    def baseline_snapshot(self) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'baseline_snapshot'",
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        try:
            payload = json.loads(str(row[0]))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def record_baseline_snapshot(self, *, ref: str, metrics: dict[str, float]) -> None:
        payload = {
            "ref": ref,
            "metrics": {str(name): float(value) for name, value in metrics.items()},
            "created_at": utc_now_iso(),
        }
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO schema_meta (key, value)
                VALUES ('baseline_snapshot', ?)
                """,
                (json.dumps(payload, sort_keys=True),),
            )
            conn.commit()
        finally:
            conn.close()

    def read_intent_packet(self, group_id: str) -> IntentPacket | None:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM intent_packets WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        payload = json.loads(str(row[0]))
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

    def record_research_outcome(self, *, run_id: str, outcome: dict[str, Any]) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            self._record_research_outcome_conn(conn, run_id=run_id, outcome=outcome)
            conn.commit()
        finally:
            conn.close()

    def _record_research_outcome_conn(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        outcome: dict[str, Any],
    ) -> None:
        research_outcome = normalize_research_outcome_name(str(outcome.get("research_outcome", "unknown")))
        met_targets = outcome_met_targets(research_outcome)
        conn.execute(
            """
            INSERT OR REPLACE INTO research_outcomes
            (run_id, research_outcome, improved_baseline, metrics_ok, next_action, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                research_outcome,
                1 if met_targets else 0,
                1 if met_targets else 0,
                str(outcome.get("next_action", "")),
                str(outcome.get("reason", "")),
                utc_now_iso(),
            ),
        )

    def record_experiment_manifest(
        self,
        *,
        run_id: str,
        manifest_path: str,
        manifest: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO experiments
                (
                    run_id,
                    group_id,
                    branch,
                    loop_index,
                    hypothesis_id,
                    hypothesis,
                    target_files_json,
                    planned_code_changes_json,
                    manifest_path,
                    lineage_mode,
                    lineage_parent_group_id,
                    lineage_anchor_sha,
                    lineage_anchor_policy,
                    lineage_parent_anchor_step,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(manifest.get("group_id", "")),
                    str(manifest.get("branch", "")),
                    _as_int_or_none(manifest.get("loop_index")),
                    str(manifest.get("hypothesis_id", "")),
                    str(manifest.get("hypothesis", "")),
                    json.dumps(_as_string_list(manifest.get("target_files")), sort_keys=True),
                    json.dumps(_as_string_list(manifest.get("planned_code_changes")), sort_keys=True),
                    manifest_path,
                    _optional_str(manifest.get("lineage_mode")),
                    _optional_str(manifest.get("lineage_parent_group_id")),
                    _optional_str(manifest.get("lineage_anchor_sha")),
                    _optional_str(manifest.get("lineage_anchor_policy")),
                    _as_int_or_none(manifest.get("lineage_parent_anchor_step")),
                    now,
                ),
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

    def schema_version(self) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def last_github_run(self, group_id: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT *
                FROM runs
                WHERE group_id = ?
                  AND failure_class = 'none'
                  AND commit_sha != ''
                  AND run_id LIKE 'gh_%'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (group_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    def best_github_run(self, group_id: str, metric_name: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT r.*
                FROM runs r
                JOIN metrics m ON r.run_id = m.run_id
                WHERE r.group_id = ?
                  AND r.failure_class = 'none'
                  AND m.metric_name = ?
                  AND r.run_id LIKE 'gh_%'
                  AND r.commit_sha != ''
                ORDER BY m.metric_value DESC, r.created_at DESC
                LIMIT 1
                """,
                (group_id, metric_name),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    def github_runs_with_metric(self, group_id: str, metric_name: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT r.*, m.metric_value
                FROM runs r
                JOIN metrics m ON r.run_id = m.run_id
                WHERE r.group_id = ?
                  AND r.failure_class = 'none'
                  AND m.metric_name = ?
                  AND r.run_id LIKE 'gh_%'
                  AND r.commit_sha != ''
                ORDER BY r.created_at ASC
                """,
                (group_id, metric_name),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def earliest_experiment(self, group_id: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT *
                FROM experiments
                WHERE group_id = ?
                ORDER BY COALESCE(loop_index, 999999) ASC, created_at ASC
                LIMIT 1
                """,
                (group_id,),
            ).fetchone()
            if not row:
                return None
            payload = _row_to_dict(row)
            payload["target_files"] = json.loads(str(payload.pop("target_files_json")))
            payload["planned_code_changes"] = json.loads(str(payload.pop("planned_code_changes_json")))
            return payload
        finally:
            conn.close()

    def metric_for_group_commit(self, group_id: str, commit_sha: str, metric_name: str) -> float | None:
        normalized_sha = str(commit_sha).strip().lower()
        if not normalized_sha:
            return None
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT m.metric_value
                FROM runs r
                JOIN metrics m ON m.run_id = r.run_id
                WHERE r.group_id = ?
                  AND m.metric_name = ?
                  AND LOWER(r.commit_sha) != ''
                  AND (
                        LOWER(r.commit_sha) = ?
                        OR LOWER(r.commit_sha) LIKE (? || '%')
                        OR ? LIKE (LOWER(r.commit_sha) || '%')
                  )
                ORDER BY r.created_at DESC
                LIMIT 1
                """,
                (group_id, metric_name, normalized_sha, normalized_sha, normalized_sha),
            ).fetchone()
            if not row:
                return None
            return float(row[0])
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

    def outcome_for_run(self, run_id: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM research_outcomes WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    def experiment_for_run(self, run_id: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM experiments WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            payload = _row_to_dict(row)
            payload["target_files"] = json.loads(str(payload.pop("target_files_json")))
            payload["planned_code_changes"] = json.loads(str(payload.pop("planned_code_changes_json")))
            return payload
        finally:
            conn.close()

    def runs_for_group(self, group_id: str | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if group_id:
                rows = conn.execute(
                    "SELECT * FROM runs WHERE group_id = ? ORDER BY created_at DESC LIMIT ?",
                    (group_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def metrics_for_group(self, group_id: str, metric_name: str | None = None) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if metric_name:
                rows = conn.execute(
                    """
                    SELECT r.run_id, r.group_id, r.branch, r.commit_sha, r.workflow_run_id,
                           r.correlation_id, r.created_at, m.metric_name, m.metric_value
                    FROM runs r
                    JOIN metrics m ON r.run_id = m.run_id
                    WHERE r.group_id = ? AND m.metric_name = ?
                    ORDER BY r.created_at
                    """,
                    (group_id, metric_name),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT r.run_id, r.group_id, r.branch, r.commit_sha, r.workflow_run_id,
                           r.correlation_id, r.created_at, m.metric_name, m.metric_value
                    FROM runs r
                    JOIN metrics m ON r.run_id = m.run_id
                    WHERE r.group_id = ?
                    ORDER BY r.created_at, m.metric_name
                    """,
                    (group_id,),
                ).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def group_summary(self) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                WITH latest AS (
                    SELECT *
                    FROM runs r
                    WHERE created_at = (
                        SELECT MAX(created_at)
                        FROM runs inner_r
                        WHERE inner_r.group_id = r.group_id
                    )
                )
                SELECT latest.*,
                       outcome.research_outcome,
                       outcome.improved_baseline,
                       outcome.next_action,
                       accuracy.metric_value AS accuracy,
                       latency.metric_value AS latency_ms
                FROM latest
                LEFT JOIN research_outcomes outcome ON latest.run_id = outcome.run_id
                LEFT JOIN metrics accuracy ON latest.run_id = accuracy.run_id AND accuracy.metric_name = 'accuracy'
                LEFT JOIN metrics latency ON latest.run_id = latency.run_id AND latency.metric_name = 'latency_ms'
                ORDER BY latest.group_id
                """
            ).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def dashboard_snapshot(self) -> dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            metrics = [
                _row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT r.run_id, r.group_id, r.branch, r.commit_sha, r.workflow_run_id,
                           r.correlation_id, r.created_at, m.metric_name, m.metric_value
                    FROM runs r
                    JOIN metrics m ON r.run_id = m.run_id
                    ORDER BY r.group_id, r.created_at, m.metric_name
                    """
                ).fetchall()
            ]
            experiments = []
            for row in conn.execute("SELECT * FROM experiments ORDER BY group_id, loop_index, created_at").fetchall():
                payload = _row_to_dict(row)
                payload["target_files"] = json.loads(str(payload.pop("target_files_json")))
                payload["planned_code_changes"] = json.loads(str(payload.pop("planned_code_changes_json")))
                experiments.append(payload)
            return {
                "export_schema_version": 1,
                "registry_schema_version": self.schema_version(),
                "summary": self.group_summary(),
                "runs": self.runs_for_group(None, limit=10_000),
                "metrics": metrics,
                "metric_names": sorted({str(row["metric_name"]) for row in metrics}),
                "research_outcomes": [
                    _row_to_dict(row)
                    for row in conn.execute("SELECT * FROM research_outcomes ORDER BY created_at").fetchall()
                ],
                "experiments": experiments,
                "artifacts": [
                    _row_to_dict(row)
                    for row in conn.execute("SELECT * FROM artifacts ORDER BY run_id, artifact_path").fetchall()
                ],
            }
        finally:
            conn.close()


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key in ("improved_baseline", "metrics_ok"):
        if key in payload and payload[key] is not None:
            payload[key] = bool(payload[key])
    return payload


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _as_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
