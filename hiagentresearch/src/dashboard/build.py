from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from hiagentresearch.src.core.artifacts import (
    EXPERIMENT_MANIFEST,
    INGEST_REQUIRED,
    eval_node_index_names,
)
from hiagentresearch.src.core.config import HiAgentResearchConfig, load_config
from hiagentresearch.src.dashboard.trajectory import (
    assign_trajectory_positions,
    baseline_metric_points,
    parent_anchor_loop_index,
)
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.core.outcomes import required_baseline_metrics
from hiagentresearch.src.github.ingest import (
    build_synthetic_experiment_manifest,
    record_baseline_snapshot_from_manifest,
)
from hiagentresearch.src.lineage.anchors import best_trajectory_anchor, last_trajectory_anchor
from hiagentresearch.src.lineage.resolve import LineageError, resolve_branch_bootstrap
from hiagentresearch.src.paths import REPO_ROOT
from hiagentresearch.src.registry import schema
from hiagentresearch.src.registry.store import Registry
from hiagentresearch.src.runtime.baseline import ensure_baseline_snapshot, install_dependency_files


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
    should_compute_baseline = not source_label.startswith("github_artifacts")
    if should_compute_baseline and (source_label.startswith("github_artifacts") or os.environ.get("CI")):
        install_dependency_files(loaded)
    if should_compute_baseline:
        ensure_baseline_snapshot(registry, loaded)
    snapshot = registry.dashboard_snapshot()
    metric_targets = _metric_targets(loaded)
    snapshot["metric_targets"] = metric_targets
    topology = _lineage_topology(loaded, registry=registry)
    topology["inherit_anchors"] = _inherit_anchors_combined(
        loaded,
        registry,
        snapshot.get("experiments", []),
        snapshot.get("runs", []),
    )
    group_winners, lineage_winners = _lineage_winner_maps(loaded, registry=registry)
    topology["group_trajectory_winners"] = group_winners
    topology["lineage_winners"] = lineage_winners
    registry.write_lineage_winners(
        {
            "updated_at": _now_iso(),
            "group_trajectory_winners": group_winners,
            "lineage_winners": lineage_winners,
        }
    )
    snapshot["lineage_topology"] = topology
    snapshot["metrics"] = _enrich_metrics_for_dashboard(
        snapshot["metrics"],
        snapshot.get("experiments", []),
        snapshot["lineage_topology"],
    )
    database_path = target_dir / "dashboard.db"
    _write_dashboard_db(
        source_db=registry.db_path,
        destination_db=database_path,
        metric_targets=metric_targets,
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
        "metric_targets": snapshot.get("metric_targets", []),
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
    app_js = output_dir / "app.js"
    if app_js.exists():
        manifest["static_cache_bust"] = _sha256(app_js.read_bytes())[:12]
        _rewrite_index_cache_bust(output_dir, manifest["static_cache_bust"])

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
    metric_targets: list[dict[str, Any]],
) -> None:
    if destination_db.exists():
        destination_db.unlink()
    source = sqlite3.connect(source_db)
    destination = sqlite3.connect(destination_db)
    try:
        destination.execute("PRAGMA journal_mode = DELETE")
        destination.execute("PRAGMA page_size = 4096")
        _create_dashboard_schema(destination)
        session_run_ids = _session_run_ids(source)
        for table in ("runs", "metrics", "research_outcomes", "experiments", "artifacts"):
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            source_columns = [info[1] for info in source.execute(f"PRAGMA table_info({table})").fetchall()]
            if session_run_ids is not None and "run_id" in source_columns:
                run_id_idx = source_columns.index("run_id")
                rows = [row for row in rows if str(row[run_id_idx]) in session_run_ids]
            dest_columns = [
                info[1] for info in destination.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            columns = [name for name in source_columns if name in dest_columns]
            if not rows or not columns:
                continue
            indexes = [source_columns.index(name) for name in columns]
            trimmed_rows = [tuple(row[index] for index in indexes) for row in rows]
            placeholders = ", ".join("?" for _ in columns)
            destination.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                trimmed_rows,
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
                for row in metric_targets
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
    for table_ddl in schema.SHARED_TABLES:
        conn.execute(table_ddl)
    conn.executescript(
        """
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
        """
    )
    conn.execute(f"CREATE VIEW latest_group_summary AS{schema.GROUP_SUMMARY_SELECT}")
    conn.executescript(
        """
        CREATE VIEW metric_series AS
            SELECT r.run_id, r.group_id, r.branch, r.commit_sha, r.workflow_run_id,
                   r.correlation_id, r.created_at, m.metric_name, m.metric_value,
                   e.loop_index, e.lineage_mode, e.lineage_parent_group_id,
                   e.lineage_anchor_sha, e.lineage_anchor_policy,
                   e.lineage_parent_anchor_step, e.lineage_anchor_source_group
            FROM runs r
            JOIN metrics m ON r.run_id = m.run_id
            LEFT JOIN experiments e ON r.run_id = e.run_id;
        """
    )


def _rewrite_index_cache_bust(output_dir: Path, cache_bust: str) -> None:
    index = output_dir / "index.html"
    if not index.exists():
        return
    text = index.read_text(encoding="utf-8")
    text = re.sub(r'src="\./app\.js(?:\?v=[^"]*)?"', f'src="./app.js?v={cache_bust}"', text)
    index.write_text(text, encoding="utf-8")


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


def _enrich_metrics_for_dashboard(
    metrics: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
    topology: dict[str, Any],
) -> list[dict[str, Any]]:
    experiments_by_run = {str(row["run_id"]): row for row in experiments}
    joined: list[dict[str, Any]] = []
    for row in metrics:
        experiment = experiments_by_run.get(str(row.get("run_id", "")))
        loop_index = experiment.get("loop_index") if experiment else row.get("loop_index")
        joined.append({**row, "loop_index": loop_index})
    metric_rows = _dedupe_metric_rows(joined)
    positioned = assign_trajectory_positions(metric_rows, topology)
    baseline_snapshot = topology.get("baseline_snapshot")
    if not baseline_snapshot:
        return positioned
    metric_names = sorted({str(row.get("metric_name", "")) for row in positioned if row.get("metric_name")})
    group_ids = sorted({str(row.get("group_id", "")) for row in positioned if row.get("group_id")})
    group_meta = topology.get("groups") or {}
    baseline_group_ids = sorted(
        group_id for group_id in group_ids if group_meta.get(group_id, {}).get("mode") == "baseline"
    )
    anchored: list[dict[str, Any]] = []
    for metric_name in metric_names:
        anchored.extend(
            baseline_metric_points(
                metric_name=metric_name,
                group_ids=baseline_group_ids,
                baseline_snapshot=baseline_snapshot,
            )
        )
    final_rows = positioned if not anchored else assign_trajectory_positions([*anchored, *positioned], topology)
    return _annotate_winner_flags(final_rows, topology)


def _dedupe_metric_rows(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in metrics:
        group_id = str(row.get("group_id", ""))
        loop_index = row.get("loop_index")
        loop_key = str(loop_index) if loop_index not in (None, "") else str(row.get("run_id", ""))
        key = f"{group_id}:{loop_key}"
        existing = best.get(key)
        if existing is None or _prefer_github_run(row, existing):
            best[key] = row
    return list(best.values())


def _prefer_github_run(candidate: dict[str, Any], incumbent: dict[str, Any]) -> bool:
    candidate_github = str(candidate.get("run_id", "")).startswith("gh_")
    incumbent_github = str(incumbent.get("run_id", "")).startswith("gh_")
    if candidate_github and not incumbent_github:
        return True
    if not candidate_github and incumbent_github:
        return False
    return str(candidate.get("run_id", "")) > str(incumbent.get("run_id", ""))


def _lineage_topology(config: HiAgentResearchConfig, *, registry: Registry | None = None) -> dict[str, Any]:
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
    baseline_snapshot = registry.baseline_snapshot() if registry is not None else None
    orchestration_session = registry.orchestration_session() if registry is not None else None
    if orchestration_session is None and registry is not None:
        started = registry.orchestration_session_started_at()
        if started:
            orchestration_session = {"started_at": started}
    return {
        "groups": group_meta,
        "chains": chains,
        "execution_waves": config.execution_waves(),
        "baseline_snapshot": baseline_snapshot,
        "orchestration_session": orchestration_session,
        "inherit_anchors": {},
        "group_trajectory_winners": {},
        "lineage_winners": {},
    }


def _lineage_winner_maps(
    config: HiAgentResearchConfig,
    *,
    registry: Registry,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    git = GitService(REPO_ROOT)
    group_winners: dict[str, dict[str, Any]] = {}
    for group in config.research_groups:
        if registry.last_github_run(group.id) is None:
            continue
        policy = group.lineage.anchor_policy if group.lineage.mode == "inherit" else "best_commit"
        winner = _trajectory_winner_for_group(
            group_id=group.id,
            policy=policy,
            metric=group.lineage.anchor_metric,
            baseline_ref=config.orchestration.baseline_ref,
            registry=registry,
            git=git,
        )
        if winner is None:
            continue
        group_winners[group.id] = {
            "group_id": group.id,
            "anchor_policy": policy,
            "anchor_metric": group.lineage.anchor_metric,
            **winner,
        }

    lineage_winners: dict[str, dict[str, Any]] = {}
    for chain in _lineage_topology(config, registry=registry).get("chains", []):
        if not chain:
            continue
        lineage_id = str(chain[0])
        effective_leaf = _effective_leaf_group_id(chain, group_winners, registry)
        if not effective_leaf:
            continue
        leaf_winner = group_winners[effective_leaf]
        lineage_winners[lineage_id] = {
            "lineage_id": lineage_id,
            "configured_leaf_group_id": str(chain[-1]),
            "leaf_group_id": effective_leaf,
            "winner_commit_sha": leaf_winner.get("commit_sha", ""),
            "winner_source_group_id": leaf_winner.get("source_group_id"),
            "anchor_policy": leaf_winner.get("anchor_policy"),
            "anchor_metric": leaf_winner.get("anchor_metric"),
            "trajectory_step": leaf_winner.get("trajectory_step"),
            "is_baseline_anchor": bool(leaf_winner.get("is_baseline_anchor", False)),
        }
    return group_winners, lineage_winners


def _effective_leaf_group_id(
    chain: list[str],
    group_winners: dict[str, dict[str, Any]],
    registry: Registry,
) -> str | None:
    """Last chain group with a completed GitHub eval (skips not-yet-run inherit children)."""
    for group_id in reversed(chain):
        if group_id in group_winners and registry.last_github_run(group_id) is not None:
            return group_id
    return None


def _trajectory_winner_for_group(
    *,
    group_id: str,
    policy: str,
    metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
) -> dict[str, Any] | None:
    if policy == "best_commit":
        anchor = best_trajectory_anchor(
            parent_group_id=group_id,
            anchor_metric=metric,
            baseline_ref=baseline_ref,
            registry=registry,
            git=git,
        )
    elif policy == "last_commit":
        anchor = last_trajectory_anchor(
            parent_group_id=group_id,
            anchor_metric=metric,
            baseline_ref=baseline_ref,
            registry=registry,
            git=git,
        )
    else:
        return None
    if anchor is None:
        return None
    is_baseline = anchor.source_group_id is None and int(anchor.trajectory_step) == 0
    return {
        "commit_sha": anchor.ref,
        "source_group_id": anchor.source_group_id,
        "trajectory_step": int(anchor.trajectory_step),
        "is_baseline_anchor": is_baseline,
    }


def _annotate_winner_flags(rows: list[dict[str, Any]], topology: dict[str, Any]) -> list[dict[str, Any]]:
    group_winners = topology.get("group_trajectory_winners") or {}
    lineage_winners = topology.get("lineage_winners") or {}
    inherit_anchors = topology.get("inherit_anchors") or {}
    lineage_by_source: dict[tuple[str, str], list[str]] = {}
    lineage_baseline_roots: set[str] = set()
    for lineage_id, payload in lineage_winners.items():
        if payload.get("is_baseline_anchor"):
            lineage_baseline_roots.add(str(lineage_id))
            continue
        source_group = str(payload.get("winner_source_group_id") or "")
        commit_sha = str(payload.get("winner_commit_sha") or "")
        if source_group and commit_sha:
            lineage_by_source.setdefault((source_group, commit_sha.lower()), []).append(str(lineage_id))
    inherit_by_source: dict[tuple[str, str], list[str]] = {}
    for group_id, payload in inherit_anchors.items():
        source_group = str(payload.get("anchor_source_group") or payload.get("parent_group_id") or "")
        commit_sha = str(payload.get("commit_sha") or "")
        if source_group and commit_sha:
            inherit_by_source.setdefault((source_group, commit_sha.lower()), []).append(str(group_id))

    annotated: list[dict[str, Any]] = []
    for row in rows:
        row_group = str(row.get("group_id", ""))
        row_sha = str(row.get("commit_sha", "") or "").lower()
        group_winner = group_winners.get(row_group)
        is_group_policy_winner = False
        if group_winner:
            winner_group = str(group_winner.get("group_id") or row_group)
            winner_source = str(group_winner.get("source_group_id") or winner_group)
            winner_sha = str(group_winner.get("commit_sha") or "").lower()
            winner_baseline = bool(group_winner.get("is_baseline_anchor", False))
            if winner_baseline:
                is_group_policy_winner = bool(row.get("is_baseline_anchor")) and row_group == winner_group
            else:
                is_group_policy_winner = row_group == winner_source and bool(row_sha) and _sha_match(row_sha, winner_sha)
        matching_lineages = list(lineage_by_source.get((row_group, row_sha), []))
        if row.get("is_baseline_anchor") and row_group in lineage_baseline_roots:
            matching_lineages.append(row_group)
        matching_inherit_groups = inherit_by_source.get((row_group, row_sha), [])
        annotated.append(
            {
                **row,
                "is_group_policy_winner": is_group_policy_winner,
                "is_lineage_winner": bool(matching_lineages),
                "lineage_winner_ids": matching_lineages,
                "is_inherit_anchor": bool(matching_inherit_groups),
                "inherit_anchor_for_groups": matching_inherit_groups,
            }
        )
    return annotated


def _sha_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def _now_iso() -> str:
    from hiagentresearch.src.core.models import utc_now_iso

    return utc_now_iso()


def _inherit_anchors_combined(
    config: HiAgentResearchConfig,
    registry: Registry,
    experiments: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    anchors = _inherit_anchors_from_experiments(experiments, runs)
    git = GitService(REPO_ROOT)
    for group in config.research_groups:
        if group.lineage.mode != "inherit":
            continue
        try:
            bootstrap = resolve_branch_bootstrap(
                group,
                config,
                registry=registry,
                git=git,
            )
        except LineageError:
            continue
        if group.id in anchors:
            continue
        resolved_step = bootstrap.parent_anchor_step
        if resolved_step is None:
            resolved_step = 0
        anchors[group.id] = {
            "parent_group_id": bootstrap.parent_group_id,
            "anchor_source_group": bootstrap.anchor_source_group_id or bootstrap.parent_group_id,
            "commit_sha": bootstrap.start_ref,
            "anchor_policy": bootstrap.anchor_policy,
            "parent_trajectory_step": resolved_step,
            "parent_anchor_loop_index": resolved_step,
        }
    return anchors


def _inherit_anchors_from_experiments(
    experiments: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    commit_owners = _commit_owner_groups(experiments, runs)
    anchors: dict[str, dict[str, Any]] = {}
    for row in experiments:
        group_id = str(row.get("group_id", ""))
        commit_sha = str(row.get("lineage_anchor_sha", "") or "").strip()
        parent_group_id = str(row.get("lineage_parent_group_id", "") or "").strip()
        if not group_id or not commit_sha:
            continue
        if str(row.get("lineage_mode", "")) != "inherit" and not parent_group_id:
            continue
        loop_index = int(row.get("loop_index") or 999)
        existing = anchors.get(group_id)
        if existing is not None and int(existing.get("bootstrap_loop_index") or 999) <= loop_index:
            continue
        recorded_step = row.get("lineage_parent_anchor_step")
        if recorded_step is not None and recorded_step != "":
            parent_step = int(recorded_step)
        else:
            parent_step = parent_anchor_loop_index(
                parent_group_id=parent_group_id,
                commit_sha=commit_sha,
                experiments=experiments,
                runs=runs,
            )
        # The anchor commit may belong to an ancestor (e.g. a grandparent peak that
        # an intermediate group never beat). Prefer the recorded owner, then fall
        # back to the group that actually owns the commit, then the immediate parent.
        source_group = str(row.get("lineage_anchor_source_group", "") or "").strip()
        if not source_group:
            source_group = _resolve_commit_owner(commit_owners, commit_sha) or parent_group_id
        anchors[group_id] = {
            "bootstrap_loop_index": loop_index,
            "parent_group_id": parent_group_id,
            "anchor_source_group": source_group,
            "commit_sha": commit_sha,
            "anchor_policy": row.get("lineage_anchor_policy"),
            "parent_trajectory_step": parent_step,
            "parent_anchor_loop_index": parent_step,
        }
    return {
        group_id: {key: value for key, value in payload.items() if key != "bootstrap_loop_index"}
        for group_id, payload in anchors.items()
    }


def _commit_owner_groups(
    experiments: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> dict[str, str]:
    """Map each run commit sha to the group that produced it."""
    runs_by_id = {str(row["run_id"]): row for row in runs}
    owners: dict[str, str] = {}
    for experiment in experiments:
        run_id = str(experiment.get("run_id", ""))
        commit_sha = str(runs_by_id.get(run_id, {}).get("commit_sha", "") or "").strip().lower()
        group_id = str(experiment.get("group_id", "") or "").strip()
        if commit_sha and group_id:
            owners.setdefault(commit_sha, group_id)
    return owners


def _resolve_commit_owner(commit_owners: dict[str, str], commit_sha: str) -> str:
    target = commit_sha.strip().lower()
    if not target:
        return ""
    owner = commit_owners.get(target)
    if owner:
        return owner
    for known_sha, group_id in commit_owners.items():
        if known_sha.startswith(target) or target.startswith(known_sha):
            return group_id
    return ""


def _metric_targets(config: HiAgentResearchConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in config.research_groups:
        for metric_name, expectation in sorted(config.evaluation.targets.items()):
            rows.append(
                {
                    "group_id": group.id,
                    "metric_name": metric_name,
                    "min": expectation.min,
                    "max": expectation.max,
                    "source": "global",
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
    required = [artifact_dir / name for name in INGEST_REQUIRED]
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
    manifest_path = artifact_dir / EXPERIMENT_MANIFEST
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_source = EXPERIMENT_MANIFEST
    else:
        manifest = build_synthetic_experiment_manifest(
            run_id=run_id,
            group_id=group_id,
            branch=branch,
            meta=meta,
        )
        manifest_source = "(synthetic:missing experiment_manifest.json)"
    record_baseline_snapshot_from_manifest(
        registry, manifest, required=required_baseline_metrics(config.evaluation.targets)
    )
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
    registry.record_experiment_manifest(
        run_id=run_id,
        manifest_path=manifest_source,
        manifest=manifest,
    )
    registry.record_artifacts(
        run_id=run_id,
        artifact_paths=[artifact_dir / name for name in eval_node_index_names()],
        artifact_type="github_eval",
        base_dir=artifact_dir,
    )
    return True


def _session_run_ids(conn: sqlite3.Connection) -> set[str] | None:
    cutoff = _session_cutoff_from_db(conn)
    if not cutoff:
        return None
    rows = conn.execute(
        "SELECT run_id FROM runs WHERE created_at >= ?",
        (cutoff,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def _session_cutoff_from_db(conn: sqlite3.Connection) -> str | None:
    for key in ("orchestration_session", "baseline_snapshot"):
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            continue
        try:
            payload = json.loads(str(row[0]))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if key == "orchestration_session" and payload.get("started_at"):
            return str(payload["started_at"])
        if key == "baseline_snapshot" and payload.get("created_at"):
            return str(payload["created_at"])
    return None


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


