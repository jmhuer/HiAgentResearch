from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from hiagentresearch.src.config import HiAgentResearchConfig, load_config
from hiagentresearch.src.registry import Registry


DASHBOARD_SCHEMA_VERSION = 1
STATIC_PACKAGE = "hiagentresearch.src.dashboard.static"
SQLITE_RUNTIME_ASSETS = {
    "sqlite.worker.js": "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/dist/sqlite.worker.js",
    "sql-wasm.wasm": "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/dist/sql-wasm.wasm",
}


@dataclass(frozen=True)
class DashboardBuildResult:
    output_dir: Path
    database_path: Path
    summary_path: Path
    manifest_path: Path
    snapshot_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "output_dir": str(self.output_dir),
            "database_path": str(self.database_path),
            "summary_path": str(self.summary_path),
            "manifest_path": str(self.manifest_path),
            "snapshot_path": str(self.snapshot_path),
        }


def build_from_registry(
    *,
    state_dir: Path,
    output_dir: Path | None = None,
    config: HiAgentResearchConfig | None = None,
    source_label: str = "local_registry",
    require_sqlite_assets: bool = False,
) -> DashboardBuildResult:
    loaded = config or load_config()
    target_dir = (output_dir or loaded.dashboard_output_path()).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    registry = Registry(state_dir.resolve())
    registry.init()
    snapshot = registry.dashboard_snapshot()
    metric_expectations = _metric_expectations(loaded)
    snapshot["metric_expectations"] = metric_expectations
    snapshot["lineage_topology"] = _lineage_topology(loaded)
    database_path = target_dir / "dashboard.db"
    _write_dashboard_db(
        source_db=registry.db_path,
        destination_db=database_path,
        metric_expectations=metric_expectations,
    )
    _copy_static_assets(target_dir)
    _copy_sqlite_runtime_assets(target_dir, require=require_sqlite_assets)
    return _write_dashboard_files(
        output_dir=target_dir,
        database_path=database_path,
        snapshot=snapshot,
        config=loaded,
        source_label=source_label,
    )


def build_from_artifacts(
    *,
    artifact_root: Path,
    output_dir: Path | None = None,
    config: HiAgentResearchConfig | None = None,
    require_sqlite_assets: bool = False,
) -> DashboardBuildResult:
    loaded = config or load_config()
    with tempfile.TemporaryDirectory(prefix="hiagentresearch-dashboard-") as tmp:
        state_dir = Path(tmp) / "state"
        registry = Registry(state_dir)
        registry.init()
        ingested = _ingest_artifact_root(registry=registry, config=loaded, artifact_root=artifact_root.resolve())
        if ingested == 0:
            raise ValueError(f"no valid dashboard artifact directories found under {artifact_root}")
        return build_from_registry(
            state_dir=state_dir,
            output_dir=output_dir or loaded.dashboard_output_path(),
            config=loaded,
            source_label=f"github_artifacts:{ingested}",
            require_sqlite_assets=require_sqlite_assets,
        )


def _write_dashboard_files(
    *,
    output_dir: Path,
    database_path: Path,
    snapshot: dict[str, Any],
    config: HiAgentResearchConfig,
    source_label: str,
) -> DashboardBuildResult:
    metric_names = sorted(
        name for name in snapshot.get("metric_names", []) if not config.dashboard.metrics or name in config.dashboard.metrics
    )
    summary = {
        "title": config.dashboard.title,
        "metric_names": metric_names,
        "groups": snapshot.get("summary", []),
        "lineage_topology": snapshot.get("lineage_topology", {}),
    }
    manifest = {
        "dashboard_schema_version": DASHBOARD_SCHEMA_VERSION,
        "registry_schema_version": snapshot.get("registry_schema_version"),
        "title": config.dashboard.title,
        "source": source_label,
        "database": "dashboard.db",
        "snapshot": "dashboard.json",
        "summary": "summary.json",
        "repository": _repository_metadata(config),
        "sqlite": {
            "adapter": "sql.js-httpvfs",
            "url": "dashboard.db",
            "worker_url": "sqlite.worker.js",
            "wasm_url": "sql-wasm.wasm",
            "request_chunk_size": 4096,
        },
        "cache_bust": _sha256(database_path.read_bytes())[:12],
    }

    summary_path = output_dir / "summary.json"
    manifest_path = output_dir / "manifest.json"
    snapshot_path = output_dir / "dashboard.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return DashboardBuildResult(
        output_dir=output_dir,
        database_path=database_path,
        summary_path=summary_path,
        manifest_path=manifest_path,
        snapshot_path=snapshot_path,
    )


def _write_dashboard_db(
    *,
    source_db: Path,
    destination_db: Path,
    metric_expectations: list[dict[str, Any]],
) -> None:
    if destination_db.exists():
        destination_db.unlink()
    source = sqlite3.connect(source_db)
    destination = sqlite3.connect(destination_db)
    try:
        destination.execute("PRAGMA journal_mode = DELETE")
        destination.execute("PRAGMA page_size = 4096")
        _create_dashboard_schema(destination)
        for table in ("runs", "metrics", "research_outcomes", "experiments", "artifacts"):
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            columns = [info[1] for info in source.execute(f"PRAGMA table_info({table})").fetchall()]
            if not rows:
                continue
            placeholders = ", ".join("?" for _ in columns)
            destination.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                rows,
            )
        destination.executemany(
            """
            INSERT INTO metric_expectations (group_id, metric_name, min_value, max_value, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    row["group_id"],
                    row["metric_name"],
                    row.get("min"),
                    row.get("max"),
                    row["source"],
                )
                for row in metric_expectations
            ],
        )
        destination.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('dashboard_schema_version', ?)",
            (str(DASHBOARD_SCHEMA_VERSION),),
        )
        destination.commit()
        destination.execute("VACUUM")
    finally:
        source.close()
        destination.close()


def _create_dashboard_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            commit_sha TEXT,
            workflow_run_id TEXT,
            correlation_id TEXT DEFAULT '',
            status TEXT NOT NULL,
            failure_class TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE metrics (
            run_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE research_outcomes (
            run_id TEXT PRIMARY KEY,
            research_outcome TEXT NOT NULL,
            improved_baseline INTEGER NOT NULL,
            metrics_ok INTEGER NOT NULL,
            next_action TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE experiments (
            run_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            loop_index INTEGER,
            hypothesis_id TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            target_files_json TEXT NOT NULL,
            planned_code_changes_json TEXT NOT NULL,
            manifest_path TEXT NOT NULL,
            lineage_mode TEXT,
            lineage_parent_group_id TEXT,
            lineage_anchor_sha TEXT,
            lineage_anchor_policy TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE artifacts (
            run_id TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, artifact_path)
        );
        CREATE TABLE metric_expectations (
            group_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            min_value REAL,
            max_value REAL,
            source TEXT NOT NULL,
            PRIMARY KEY (group_id, metric_name)
        );
        CREATE UNIQUE INDEX idx_dashboard_metrics_run_name ON metrics(run_id, metric_name);
        CREATE INDEX idx_dashboard_runs_group_created ON runs(group_id, created_at);
        CREATE INDEX idx_dashboard_metrics_group_name ON metrics(metric_name, run_id);
        CREATE INDEX idx_dashboard_outcomes_outcome ON research_outcomes(research_outcome, improved_baseline);
        CREATE INDEX idx_dashboard_experiments_group ON experiments(group_id, loop_index);
        CREATE INDEX idx_dashboard_expectations_metric ON metric_expectations(metric_name, group_id);
        CREATE VIEW latest_group_summary AS
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
            LEFT JOIN metrics latency ON latest.run_id = latency.run_id AND latency.metric_name = 'latency_ms';
        CREATE VIEW metric_series AS
            SELECT r.run_id, r.group_id, r.branch, r.commit_sha, r.workflow_run_id,
                   r.correlation_id, r.created_at, m.metric_name, m.metric_value,
                   e.loop_index, e.lineage_mode, e.lineage_parent_group_id,
                   e.lineage_anchor_sha, e.lineage_anchor_policy
            FROM runs r
            JOIN metrics m ON r.run_id = m.run_id
            LEFT JOIN experiments e ON r.run_id = e.run_id;
        """
    )


def _copy_static_assets(output_dir: Path) -> None:
    for asset in resources.files(STATIC_PACKAGE).iterdir():
        if asset.name.startswith("__") or asset.name.endswith(".py"):
            continue
        destination = output_dir / asset.name
        if asset.is_file():
            with resources.as_file(asset) as source:
                shutil.copyfile(source, destination)


def _copy_sqlite_runtime_assets(output_dir: Path, *, require: bool) -> None:
    for filename, url in SQLITE_RUNTIME_ASSETS.items():
        destination = output_dir / filename
        if destination.exists():
            continue
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                destination.write_bytes(response.read())
        except OSError:
            if require:
                raise


def _lineage_topology(config: HiAgentResearchConfig) -> dict[str, Any]:
    group_meta = {
        group.id: {
            "mode": group.lineage.mode,
            "inherit_from": group.lineage.inherit_from,
        }
        for group in config.research_groups
    }
    children: dict[str, list[str]] = {}
    for group in config.research_groups:
        parent = group.lineage.inherit_from
        if parent:
            children.setdefault(parent, []).append(group.id)
    chains: list[list[str]] = []
    consumed: set[str] = set()
    for group in config.research_groups:
        if group.lineage.mode != "baseline" or group.id in consumed:
            continue
        chain = [group.id]
        current = group.id
        while current in children and len(children[current]) == 1:
            nxt = children[current][0]
            chain.append(nxt)
            current = nxt
        for group_id in chain:
            consumed.add(group_id)
        chains.append(chain)
    return {"groups": group_meta, "chains": chains}


def _metric_expectations(config: HiAgentResearchConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in config.research_groups:
        eval_config = group.evaluation or config.evaluation
        source = "group" if group.evaluation else "global"
        for metric_name, expectation in sorted(eval_config.success_metrics.items()):
            rows.append(
                {
                    "group_id": group.id,
                    "metric_name": metric_name,
                    "min": expectation.min,
                    "max": expectation.max,
                    "source": source,
                }
            )
    return rows


def _repository_metadata(config: HiAgentResearchConfig) -> dict[str, str]:
    import os

    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository:
        web_url = f"{server_url}/{repository}"
    else:
        web_url = _normalize_github_remote(_git_remote_url(config.github.remote))
        repository = _repository_slug(web_url)
    return {
        "web_url": web_url,
        "repository": repository,
        "commit_url_template": f"{web_url}/commit/{{commit_sha}}" if web_url else "",
        "branch_url_template": f"{web_url}/tree/{{branch}}" if web_url else "",
        "workflow_run_url_template": f"{web_url}/actions/runs/{{workflow_run_id}}" if web_url else "",
    }


def _git_remote_url(remote: str) -> str:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", remote],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _normalize_github_remote(remote_url: str) -> str:
    if not remote_url:
        return ""
    cleaned = remote_url.removesuffix(".git")
    if cleaned.startswith("git@github.com:"):
        return f"https://github.com/{cleaned.removeprefix('git@github.com:')}"
    return cleaned


def _repository_slug(web_url: str) -> str:
    prefix = "https://github.com/"
    if web_url.startswith(prefix):
        return web_url.removeprefix(prefix)
    return ""


def _ingest_artifact_root(*, registry: Registry, config: HiAgentResearchConfig, artifact_root: Path) -> int:
    count = 0
    for metrics_path in sorted(artifact_root.rglob("metrics.json")):
        artifact_dir = metrics_path.parent
        if _ingest_artifact_dir(registry=registry, config=config, artifact_dir=artifact_dir):
            count += 1
    return count


def _ingest_artifact_dir(*, registry: Registry, config: HiAgentResearchConfig, artifact_dir: Path) -> bool:
    required = [artifact_dir / name for name in config.artifact_contract.required]
    if any(not path.exists() for path in required):
        return False
    try:
        metrics = json.loads((artifact_dir / "metrics.json").read_text(encoding="utf-8"))
        failure = json.loads((artifact_dir / "failure_class.json").read_text(encoding="utf-8"))
        outcome = json.loads((artifact_dir / "research_outcome.json").read_text(encoding="utf-8"))
        meta = json.loads((artifact_dir / "run_meta.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    run_id = str(meta.get("run_id") or f"gh_{meta.get('workflow_run_id', artifact_dir.name)}")
    group_id = str(meta.get("group_id", "unknown"))
    branch = str(meta.get("branch", "unknown"))
    failure_class = str(failure.get("failure_class", "infra_failure"))
    registry.record_run(
        run_id=run_id,
        group_id=group_id,
        branch=branch,
        status="finished" if failure_class == "none" else "error",
        failure_class=failure_class,
        metrics={key: float(value) for key, value in metrics.items()},
        commit_sha=str(meta.get("commit_sha", "")),
        workflow_run_id=str(meta.get("workflow_run_id", "")),
        correlation_id=str(meta.get("correlation_id") or run_id),
    )
    registry.record_research_outcome(run_id=run_id, outcome=outcome)
    manifest_path = artifact_dir / "experiment_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        registry.record_experiment_manifest(
            run_id=run_id,
            manifest_path="experiment_manifest.json",
            manifest=manifest,
        )
    registry.record_artifacts(
        run_id=run_id,
        artifact_paths=[artifact_dir / name for name in config.artifact_contract.required + config.artifact_contract.optional],
        artifact_type="github_eval",
        base_dir=artifact_dir,
    )
    return True


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
