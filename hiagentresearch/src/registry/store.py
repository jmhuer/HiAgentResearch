from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from hiagentresearch.src.core.coerce import as_int_or_none, as_string_list, optional_str
from hiagentresearch.src.core.models import IntentPacket, TransitionEvent, utc_now_iso
from hiagentresearch.src.core.outcomes import normalize_research_outcome_name, outcome_met_targets
from hiagentresearch.src.orchestration.session import SESSION_META_KEY
from hiagentresearch.src.registry import schema


SCHEMA_VERSION = 7

# Reserved group id for the frozen L0 baseline run. It is intentionally empty so
# the baseline run never attaches to a configured research group's series (the
# dashboard only enumerates truthy group ids); the per-group L0 anchors shown on
# the chart are still synthesized from baseline_snapshot().
BASELINE_RUN_GROUP = ""


class Registry:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "evals.db"

    def init(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            for table_ddl in schema.SHARED_TABLES:
                conn.execute(table_ddl)
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_group_created ON runs(group_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_correlation ON runs(correlation_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name_run ON metrics(metric_name, run_id)")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outcomes_outcome
            ON research_outcomes(research_outcome, improved_baseline)
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cycles_group ON cycles(group_id, loop_index)")
        cycle_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(cycles)").fetchall()
        }
        for column, ddl in (
            ("lineage_mode", "ALTER TABLE cycles ADD COLUMN lineage_mode TEXT"),
            ("lineage_parent_group_id", "ALTER TABLE cycles ADD COLUMN lineage_parent_group_id TEXT"),
            ("lineage_anchor_sha", "ALTER TABLE cycles ADD COLUMN lineage_anchor_sha TEXT"),
            ("lineage_anchor_policy", "ALTER TABLE cycles ADD COLUMN lineage_anchor_policy TEXT"),
            ("lineage_parent_anchor_step", "ALTER TABLE cycles ADD COLUMN lineage_parent_anchor_step INTEGER"),
            ("lineage_anchor_source_group", "ALTER TABLE cycles ADD COLUMN lineage_anchor_source_group TEXT"),
        ):
            if column not in cycle_columns:
                conn.execute(ddl)
        outcome_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(research_outcomes)").fetchall()
        }
        if "metrics_ok" in outcome_columns:
            conn.execute("ALTER TABLE research_outcomes DROP COLUMN metrics_ok")
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
        """Frozen L0 baseline, derived from its canonical run row.

        The baseline is stored once, as a run row under the reserved
        :data:`BASELINE_RUN_GROUP` sentinel (a group-agnostic L0 shared by every
        baseline-mode branch). This accessor reprojects it into the historical
        ``{ref, metrics, created_at}`` shape so lineage anchors and the dashboard
        read it the same way they always have — a single source of truth in the
        runs table rather than a parallel schema_meta blob.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            run = conn.execute(
                """
                SELECT run_id, branch, commit_sha, created_at
                FROM runs
                WHERE group_id = ? AND failure_class = 'none'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (BASELINE_RUN_GROUP,),
            ).fetchone()
            if not run:
                return None
            metric_rows = conn.execute(
                "SELECT metric_name, metric_value FROM metrics WHERE run_id = ?",
                (str(run["run_id"]),),
            ).fetchall()
        finally:
            conn.close()
        metrics = {str(r["metric_name"]): float(r["metric_value"]) for r in metric_rows}
        return {
            "ref": str(run["branch"]),
            "commit_sha": str(run["commit_sha"] or ""),
            "metrics": metrics,
            "created_at": str(run["created_at"]),
        }

    def orchestration_session(self) -> dict[str, Any] | None:
        return self._read_schema_meta_json(SESSION_META_KEY)

    def mark_session_complete(self) -> None:
        """Stamp the orchestration session as finished.

        The dashboard is a static snapshot, so it shows the run as live or complete
        based on this stamp at build time: a build taken mid-run has no ``completed_at``
        (the loop is still going), one taken after ``loops-all`` returns does. Preserves
        ``started_at``."""
        session = self.orchestration_session() or {}
        session["completed_at"] = utc_now_iso()
        self._write_schema_meta_json(SESSION_META_KEY, session)

    def orchestration_session_started_at(self) -> str | None:
        session = self.orchestration_session()
        if isinstance(session, dict) and session.get("started_at"):
            return str(session["started_at"])
        baseline = self.baseline_snapshot()
        if isinstance(baseline, dict) and baseline.get("created_at"):
            return str(baseline["created_at"])
        return None

    def _session_run_filter(self, *, table: str = "runs") -> tuple[str, list[Any]]:
        cutoff = self.orchestration_session_started_at()
        if not cutoff:
            return "", []
        if table == "runs":
            return " AND created_at >= ?", [cutoff]
        return f" AND {table}.created_at >= ?", [cutoff]

    def _displayable_run_filter(self, *, alias: str = "r") -> tuple[str, list[Any]]:
        """Restrict a run query to *displayable* runs: committed CI evals plus the
        frozen baseline.

        A research cycle records two run rows that share a ``correlation_id``: an
        ephemeral local quick-eval (the loop's next-action probe — no commit) and
        the authoritative, commit-bound GitHub Actions eval (``gh_*``). Only the
        latter (and the baseline L0) belongs on the dashboard, so the trajectory
        shows one point per cycle rather than a local/CI pair with diverging
        numbers. Keyed on ``commit_sha`` (set on every CI eval) rather than the
        run-id prefix, with the baseline sentinel admitted explicitly.
        """
        col = f"{alias}." if alias else ""
        return f" AND ({col}commit_sha != '' OR {col}group_id = ?)", [BASELINE_RUN_GROUP]

    def lineage_winners(self) -> dict[str, Any]:
        payload = self._read_schema_meta_json("lineage_winners")
        if isinstance(payload, dict):
            return payload
        return {}

    def write_lineage_winners(self, winners: dict[str, Any]) -> None:
        self._write_schema_meta_json("lineage_winners", winners)

    def _read_schema_meta_json(self, key: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = ?",
                (key,),
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

    def record_baseline_snapshot(
        self, *, ref: str, metrics: dict[str, float], commit_sha: str = ""
    ) -> None:
        """Record the frozen L0 baseline as a first-class run row.

        The baseline used to live in a bespoke schema_meta blob; it is now an
        ordinary run (under the reserved :data:`BASELINE_RUN_GROUP` sentinel) so
        the runs table is the single source of truth. ``commit_sha`` is the
        resolved SHA of the baseline ref so L0 anchors to a real commit (like every
        other run) and the dashboard can link it. The orchestration-session anchor
        is written *first* so the baseline run's ``created_at`` lands on or after
        the cutoff and is never filtered out of session-scoped reads.
        """
        started_at = utc_now_iso()
        self._write_schema_meta_json(SESSION_META_KEY, {"started_at": started_at})
        self.record_run(
            run_id=f"baseline:{ref}",
            group_id=BASELINE_RUN_GROUP,
            branch=ref,
            status="finished",
            failure_class="none",
            metrics={str(name): float(value) for name, value in metrics.items()},
            commit_sha=commit_sha,
            correlation_id=f"baseline_{ref}",
            created_at=started_at,
        )

    def _write_schema_meta_json(self, key: str, payload: dict[str, Any]) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO schema_meta (key, value)
                VALUES (?, ?)
                """,
                (key, json.dumps(payload, sort_keys=True)),
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
        created_at: str | None = None,
    ) -> None:
        now = created_at or utc_now_iso()
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
            (run_id, research_outcome, improved_baseline, next_action, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                research_outcome,
                1 if met_targets else 0,
                str(outcome.get("next_action", "")),
                str(outcome.get("reason", "")),
                utc_now_iso(),
            ),
        )

    def record_cycle_manifest(
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
                INSERT OR REPLACE INTO cycles
                (
                    run_id,
                    group_id,
                    branch,
                    loop_index,
                    goal_id,
                    goal,
                    target_files_json,
                    planned_code_changes_json,
                    manifest_path,
                    lineage_mode,
                    lineage_parent_group_id,
                    lineage_anchor_sha,
                    lineage_anchor_policy,
                    lineage_parent_anchor_step,
                    lineage_anchor_source_group,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(manifest.get("group_id", "")),
                    str(manifest.get("branch", "")),
                    as_int_or_none(manifest.get("loop_index")),
                    str(manifest.get("goal_id", "")),
                    str(manifest.get("goal", "")),
                    json.dumps(as_string_list(manifest.get("target_files")), sort_keys=True),
                    json.dumps(as_string_list(manifest.get("planned_code_changes")), sort_keys=True),
                    manifest_path,
                    optional_str(manifest.get("lineage_mode")),
                    optional_str(manifest.get("lineage_parent_group_id")),
                    optional_str(manifest.get("lineage_anchor_sha")),
                    optional_str(manifest.get("lineage_anchor_policy")),
                    as_int_or_none(manifest.get("lineage_parent_anchor_step")),
                    optional_str(manifest.get("lineage_anchor_source_group")),
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
        session_filter, session_params = self._session_run_filter(table="runs")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                f"""
                SELECT *
                FROM runs
                WHERE group_id = ?
                  AND failure_class = 'none'
                  AND commit_sha != ''
                  AND run_id LIKE 'gh_%'
                  {session_filter}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (group_id, *session_params),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    def latest_loop_github_run(self, group_id: str) -> dict[str, Any] | None:
        """Newest GitHub run on a group's trajectory, ordered by loop_index.

        Unlike :meth:`last_github_run` (which orders by ``created_at``), this keys
        on the cycle's ``loop_index`` so "the run we just finished" is the
        highest accepted loop even when retries/parallel waves make wall-clock
        order disagree with loop order. ``created_at`` breaks ties.
        """
        session_filter, session_params = self._session_run_filter(table="r")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                f"""
                SELECT r.*
                FROM runs r
                LEFT JOIN cycles e ON r.run_id = e.run_id
                WHERE r.group_id = ?
                  AND r.failure_class = 'none'
                  AND r.commit_sha != ''
                  AND r.run_id LIKE 'gh_%'
                  {session_filter}
                ORDER BY COALESCE(e.loop_index, -1) DESC, r.created_at DESC
                LIMIT 1
                """,
                (group_id, *session_params),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    def github_runs_with_metric(self, group_id: str, metric_name: str) -> list[dict[str, Any]]:
        session_filter, session_params = self._session_run_filter(table="r")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"""
                SELECT r.*, m.metric_value
                FROM runs r
                JOIN metrics m ON r.run_id = m.run_id
                WHERE r.group_id = ?
                  AND r.failure_class = 'none'
                  AND m.metric_name = ?
                  AND r.run_id LIKE 'gh_%'
                  AND r.commit_sha != ''
                  {session_filter}
                ORDER BY r.created_at ASC
                """,
                (group_id, metric_name, *session_params),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def earliest_cycle(self, group_id: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT *
                FROM cycles
                WHERE group_id = ?
                ORDER BY COALESCE(loop_index, 999999) ASC, created_at ASC
                LIMIT 1
                """,
                (group_id,),
            ).fetchone()
            if not row:
                return None
            return _cycle_row_to_dict(row)
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

    def cycle_for_run(self, run_id: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM cycles WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            return _cycle_row_to_dict(row)
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

    def _attach_run_metrics(
        self, conn: sqlite3.Connection, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach a generic ``metrics`` dict (all recorded metrics) to each summary
        row, so consumers are not tied to any specific metric name."""
        for row in rows:
            run_id = str(row.get("run_id", ""))
            metric_rows = conn.execute(
                "SELECT metric_name, metric_value FROM metrics WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            row["metrics"] = {str(r[0]): float(r[1]) for r in metric_rows}
        return rows

    def group_summary(self) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"{schema.GROUP_SUMMARY_SELECT}\nORDER BY latest.group_id"
            ).fetchall()
            return self._attach_run_metrics(conn, [_row_to_dict(row) for row in rows])
        finally:
            conn.close()

    def dashboard_snapshot(self) -> dict[str, Any]:
        session_filter, session_params = self._session_run_filter(table="r")
        runs_session_filter, _ = self._session_run_filter()
        display_filter, display_params = self._displayable_run_filter(alias="r")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            metrics = [
                _row_to_dict(row)
                for row in conn.execute(
                    f"""
                    SELECT r.run_id, r.group_id, r.branch, r.commit_sha, r.workflow_run_id,
                           r.correlation_id, r.created_at, m.metric_name, m.metric_value
                    FROM runs r
                    JOIN metrics m ON r.run_id = m.run_id
                    WHERE 1=1{session_filter}{display_filter}
                    ORDER BY r.group_id, r.created_at, m.metric_name
                    """,
                    (*session_params, *display_params),
                ).fetchall()
            ]
            cycles = [
                _cycle_row_to_dict(row)
                for row in conn.execute(
                    f"""
                    SELECT e.*
                    FROM cycles e
                    JOIN runs r ON r.run_id = e.run_id
                    WHERE 1=1{session_filter}
                    ORDER BY e.group_id, e.loop_index, e.created_at
                    """,
                    session_params,
                ).fetchall()
            ]
            cutoff = self.orchestration_session_started_at()
            if cutoff:
                inner_display_filter, _ = self._displayable_run_filter(alias="inner_r")
                summary_rows = conn.execute(
                    f"""
                    WITH latest AS (
                        SELECT *
                        FROM runs r
                        WHERE r.created_at >= ?
                          {display_filter}
                          AND r.created_at = (
                            SELECT MAX(inner_r.created_at)
                            FROM runs inner_r
                            WHERE inner_r.group_id = r.group_id
                              AND inner_r.created_at >= ?
                              {inner_display_filter}
                          )
                    )
                    SELECT latest.*,
                           outcome.research_outcome,
                           outcome.improved_baseline,
                           outcome.next_action
                    FROM latest
                    LEFT JOIN research_outcomes outcome ON latest.run_id = outcome.run_id
                    ORDER BY latest.group_id
                    """,
                    (cutoff, *display_params, cutoff, *display_params),
                ).fetchall()
            else:
                summary_rows = conn.execute(
                    f"{schema.GROUP_SUMMARY_SELECT}\nORDER BY latest.group_id"
                ).fetchall()
            runs_display_filter, _ = self._displayable_run_filter(alias="")
            runs_sql = (
                f"SELECT * FROM runs WHERE 1=1{runs_session_filter}{runs_display_filter}"
                " ORDER BY created_at DESC LIMIT ?"
            )
            runs_params = [session_params[0]] if runs_session_filter else []
            runs_rows = conn.execute(
                runs_sql, (*runs_params, *display_params, 10_000)
            ).fetchall()
            outcomes_sql = f"""
                SELECT o.*
                FROM research_outcomes o
                JOIN runs r ON r.run_id = o.run_id
                WHERE 1=1{session_filter}
                ORDER BY o.created_at
                """
            artifacts_sql = f"""
                SELECT a.*
                FROM artifacts a
                JOIN runs r ON r.run_id = a.run_id
                WHERE 1=1{session_filter}
                ORDER BY a.run_id, a.artifact_path
                """
            session = self.orchestration_session()
            if session is None and self.orchestration_session_started_at():
                session = {"started_at": self.orchestration_session_started_at()}
            return {
                "export_schema_version": 1,
                "registry_schema_version": self.schema_version(),
                "orchestration_session": session,
                # Exclude the frozen baseline sentinel run (BASELINE_RUN_GROUP) from
                # group-facing lists; it surfaces as per-group L0 anchors, not a
                # standalone "unknown" group card or run-detail entry.
                "summary": self._attach_run_metrics(
                    conn, [_row_to_dict(row) for row in summary_rows if str(row["group_id"])]
                ),
                "runs": [_row_to_dict(row) for row in runs_rows if str(row["group_id"])],
                "metrics": metrics,
                "metric_names": sorted({str(row["metric_name"]) for row in metrics}),
                "research_outcomes": [
                    _row_to_dict(row) for row in conn.execute(outcomes_sql, session_params).fetchall()
                ],
                "cycles": cycles,
                "artifacts": [
                    _row_to_dict(row) for row in conn.execute(artifacts_sql, session_params).fetchall()
                ],
            }
        finally:
            conn.close()


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    if payload.get("improved_baseline") is not None:
        payload["improved_baseline"] = bool(payload["improved_baseline"])
    return payload


def _cycle_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Parse a single ``cycles`` row, decoding its JSON list columns once."""
    payload = _row_to_dict(row)
    payload["target_files"] = json.loads(str(payload.pop("target_files_json")))
    payload["planned_code_changes"] = json.loads(str(payload.pop("planned_code_changes_json")))
    return payload
