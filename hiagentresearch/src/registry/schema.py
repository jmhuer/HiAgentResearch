"""Canonical table DDL shared by the registry and the exported dashboard database.

Table definitions live here once so the live registry (`store.Registry.init`) and the
read-only dashboard copy (`dashboard.build`) cannot drift. Each registry-only table
(``transitions``, ``intent_packets``) and the dashboard-only extras (views, indexes,
``metric_expectations``) stay with their owner.
"""

from __future__ import annotations

SCHEMA_META_TABLE = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

RUNS_TABLE = """
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

METRICS_TABLE = """
CREATE TABLE IF NOT EXISTS metrics (
    run_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    created_at TEXT NOT NULL
)
"""

RESEARCH_OUTCOMES_TABLE = """
CREATE TABLE IF NOT EXISTS research_outcomes (
    run_id TEXT PRIMARY KEY,
    research_outcome TEXT NOT NULL,
    improved_baseline INTEGER NOT NULL,
    next_action TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

CYCLES_TABLE = """
CREATE TABLE IF NOT EXISTS cycles (
    run_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    loop_index INTEGER,
    goal_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    target_files_json TEXT NOT NULL,
    planned_code_changes_json TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    lineage_mode TEXT,
    lineage_parent_group_id TEXT,
    lineage_anchor_sha TEXT,
    lineage_anchor_policy TEXT,
    lineage_parent_anchor_step INTEGER,
    lineage_anchor_source_group TEXT,
    created_at TEXT NOT NULL
)
"""

ARTIFACTS_TABLE = """
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

# Tables that exist in both the registry and the dashboard export.
SHARED_TABLES = (
    SCHEMA_META_TABLE,
    RUNS_TABLE,
    METRICS_TABLE,
    RESEARCH_OUTCOMES_TABLE,
    CYCLES_TABLE,
    ARTIFACTS_TABLE,
)

# Latest-run-per-group projection used by both `Registry.group_summary` and the
# dashboard's `latest_group_summary` view. Callers append their own ORDER BY / wrap
# it in a CREATE VIEW as needed.
GROUP_SUMMARY_SELECT = """
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
       outcome.next_action
FROM latest
LEFT JOIN research_outcomes outcome ON latest.run_id = outcome.run_id
"""
