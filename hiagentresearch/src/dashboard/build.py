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
from typing import Any, Iterable

from hiagentresearch.src.core.artifacts import (
    CYCLE_MANIFEST,
    INGEST_REQUIRED,
    eval_node_index_names,
)
from hiagentresearch.src.core.config import HiAgentResearchConfig, load_config
from hiagentresearch.src.dashboard.trajectory import (
    _sha_match,
    assign_trajectory_positions,
    baseline_metric_points,
    parent_anchor_loop_index,
)
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.core.outcomes import required_baseline_metrics
from hiagentresearch.src.github.ingest import (
    build_synthetic_cycle_manifest,
    record_baseline_snapshot_from_manifest,
    record_baseline_snapshot_from_metrics,
)
from hiagentresearch.src.agents.task_contract import task_contract
from hiagentresearch.src.lineage.anchors import best_trajectory_anchor, last_trajectory_anchor
from hiagentresearch.src.lineage.resolve import (
    LineageError,
    _non_merge_lineage_leaves,
    resolve_branch_bootstrap,
)
from hiagentresearch.src.paths import REPO_ROOT
from hiagentresearch.src.registry import schema
from hiagentresearch.src.registry.store import BASELINE_RUN_GROUP, Registry
from hiagentresearch.src.runtime.baseline import ensure_baseline_snapshot, install_dependency_files


DASHBOARD_SCHEMA_VERSION = 1
# The dashboard ships a single look (the "te" bundle). The original ("classic") bundle is kept
# only for occasional visual comparison — opt in with HIAGENTRESEARCH_DASHBOARD_THEME=classic.
# This is intentionally an env var, not a config option: themes are not a user-facing feature.
STATIC_PACKAGE = "hiagentresearch.src.dashboard.static_te"
STATIC_PACKAGE_CLASSIC = "hiagentresearch.src.dashboard.static"


def _static_package() -> str:
    return STATIC_PACKAGE_CLASSIC if os.environ.get("HIAGENTRESEARCH_DASHBOARD_THEME") == "classic" else STATIC_PACKAGE
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
    topology = _lineage_topology(loaded, registry=registry, cycles=snapshot.get("cycles", []))
    topology["inherit_anchors"] = _inherit_anchors_combined(
        loaded,
        registry,
        snapshot.get("cycles", []),
        snapshot.get("runs", []),
    )
    group_winners, lineage_winners = _lineage_winner_maps(loaded, registry=registry)
    topology["group_trajectory_winners"] = group_winners
    topology["lineage_winners"] = lineage_winners
    # The lineage DAG as a per-group parent chain — the single structure the frontend walks to
    # connect each trajectory to its nearest in-scope ancestor (the natural replacement for the
    # connector point-type taxonomy). Built from the maps above + inherit_anchors + baseline.
    topology["lineage_parents"] = _lineage_parents(topology)
    # Per-metric per-group winners drive the chart ★: each metric stars the best commit for
    # THAT metric (direction-aware, per policy), so the latency chart marks the lowest latency
    # rather than the accuracy winner. Only the per-GROUP map is consumed (by _annotate_winner_flags);
    # the anchor-based lineage_winners map above stays the single "top commit" for the flat-config
    # Lineage panel.
    topology["winners_by_metric"] = {
        metric: {"group": _lineage_winner_maps(loaded, registry=registry, metric_override=metric)[0]}
        for metric in loaded.dashboard.metrics
    }
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
        snapshot.get("cycles", []),
        snapshot["lineage_topology"],
        display_metrics=loaded.dashboard.metrics,
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


def _dashboard_display_metric_names(
    *,
    configured: list[str],
    available: Iterable[str],
) -> list[str]:
    """Return chart metric names in config order (first = default in the UI).

    When the dashboard config lists metrics explicitly, preserve that order instead of
    sorting alphabetically — otherwise duration_sec wins over macro_f1. Any captured
    metrics not listed in config are appended in sorted order.
    """
    available_set = {str(name) for name in available if name}
    configured_set = {str(name) for name in configured if name}
    if configured_set:
        return [str(name) for name in configured if str(name) in available_set]
    return sorted(available_set)


def _write_dashboard_files(
    *,
    output_dir: Path,
    database_path: Path,
    snapshot: dict[str, Any],
    config: HiAgentResearchConfig,
    source_label: str,
) -> DashboardBuildResult:
    metric_names = _dashboard_display_metric_names(
        configured=list(config.dashboard.metrics),
        available=snapshot.get("metric_names", []),
    )
    snapshot = {**snapshot, "metric_names": metric_names}
    summary = {
        "title": config.dashboard.title,
        "metric_names": metric_names,
        "discrete_metrics": list(config.dashboard.discrete_metrics),
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
        for table in ("runs", "metrics", "research_outcomes", "cycles", "artifacts"):
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
        CREATE INDEX idx_dashboard_cycles_group ON cycles(group_id, loop_index);
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
                   e.lineage_parent_anchor_step, e.lineage_anchor_source_group,
                   e.merge_plan_json, e.merge_cycle_provenance_json
            FROM runs r
            JOIN metrics m ON r.run_id = m.run_id
            LEFT JOIN cycles e ON r.run_id = e.run_id;
        """
    )


def _rewrite_index_cache_bust(output_dir: Path, cache_bust: str) -> None:
    index = output_dir / "index.html"
    if not index.exists():
        return
    text = index.read_text(encoding="utf-8")
    text = re.sub(r'src="\./app\.js(?:\?v=[^"]*)?"', f'src="./app.js?v={cache_bust}"', text)
    # Bust stylesheet cache too; otherwise GitHub Pages can keep an old CSS file while
    # app.js updates, making the deployed visual theme look different from local preview.
    text = re.sub(
        r'href="\./styles\.css(?:\?v=[^"]*)?"',
        f'href="./styles.css?v={cache_bust}"',
        text,
    )
    text = re.sub(
        r'href="\./theme-te\.css(?:\?v=[^"]*)?"',
        f'href="./theme-te.css?v={cache_bust}"',
        text,
    )
    index.write_text(text, encoding="utf-8")


def _copy_static_assets(output_dir: Path) -> None:
    for asset in resources.files(_static_package()).iterdir():
        # Skip Python and the Node ESM marker (package.json is for `node --test` of the static
        # JS only; the browser ignores it and it has no place in the served dashboard).
        if asset.name.startswith("__") or asset.name.endswith(".py") or asset.name == "package.json":
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
    cycles: list[dict[str, Any]],
    topology: dict[str, Any],
    display_metrics: list[str] | None = None,
) -> list[dict[str, Any]]:
    cycles_by_run = {str(row["run_id"]): row for row in cycles}
    display_metric_set = {str(name) for name in display_metrics} if display_metrics else None
    joined: list[dict[str, Any]] = []
    for row in metrics:
        # The frozen L0 baseline is stored as a run under the reserved empty-group
        # sentinel; it is surfaced via the per-group baseline anchors below, not as
        # its own (group-less) series, so drop it from the display rows here.
        if not str(row.get("group_id", "")):
            continue
        # Scope to the configured dashboard metrics (e.g. accuracy, latency_ms) so the
        # chart and Run Detail show exactly the tracked research metrics — not the
        # incidental diagnostic ones (exit codes, test counts) the eval also emits.
        if display_metric_set is not None and str(row.get("metric_name", "")) not in display_metric_set:
            continue
        cycle = cycles_by_run.get(str(row.get("run_id", "")))
        loop_index = cycle.get("loop_index") if cycle else row.get("loop_index")
        joined.append({**row, "loop_index": loop_index})
    metric_rows = _dedupe_metric_rows(joined)
    positioned = assign_trajectory_positions(metric_rows, topology)
    baseline_snapshot = topology.get("baseline_snapshot")
    if not baseline_snapshot:
        return _annotate_winner_flags(positioned, topology)
    snapshot_metrics = baseline_snapshot.get("metrics") or {}
    # Include the snapshot's own metric keys so L0 anchors render even before any
    # loop run exists for a group (otherwise the dashboard stays empty until the
    # first real run), but scope to the configured dashboard metrics so we don't
    # anchor incidental eval metrics.
    candidate_metrics = {
        str(row.get("metric_name", "")) for row in positioned if row.get("metric_name")
    } | {str(name) for name in snapshot_metrics}
    if display_metrics:
        candidate_metrics &= {str(name) for name in display_metrics}
    metric_names = sorted(candidate_metrics)
    group_meta = topology.get("groups") or {}
    # Anchor L0 for every configured baseline-mode group, not only those that
    # already have run data — the frozen baseline is shared across all of them.
    baseline_group_ids = sorted(
        group_id for group_id, meta in group_meta.items() if meta.get("mode") == "baseline"
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
    based = assign_trajectory_positions([*anchored, *positioned], topology) if anchored else positioned
    # Emit each collapse's BASE node (the adopted commit it builds on) as a real point on the
    # collapse's series — a SELECT collapse adopts the strongest leaf's commit, a MERGE collapse
    # integrates from a base before its own loops. That is the only synthetic node left: the
    # inheritance-connector / area-spine origins are gone — the frontend derives where each
    # trajectory connects to (its nearest in-scope ancestor) at render time from lineage_parents.
    based = [*based, *_collapse_result_points(based, topology, metric_names)]
    return _annotate_winner_flags(based, topology)


def _collapse_result_points(
    rows: list[dict[str, Any]],
    topology: dict[str, Any],
    metric_names: list[str],
) -> list[dict[str, Any]]:
    """The BASE node every collapse builds on, emitted as a real point on the collapse's series.

    A SELECT collapse (no run of its own) adopts the strongest leaf's commit — that commit IS its
    result. A MERGE collapse integrates from a base (the strongest source's commit) before its own
    loops. Either way the base sits at the adopted commit's position/value, read from the resolved
    inherit anchor (``anchor_source_group`` at ``parent_trajectory_step``). Emitting it as a real
    node — owned by the collapse — is what makes the merge base visible on the Overview even when
    the source leaf is out of scope, and gives a SELECT collapse a node at all.

    This is the only synthetic node the dashboard still emits: the old leaf-origin and area-spine
    *connectors* are gone — the frontend derives where each trajectory connects (its nearest
    in-scope ancestor) at render time by walking ``lineage_parents``.

    A collapse additionally re-emits the adopted source leaf's path *up to* the adopted commit (its
    intermediate loop nodes), tagged ``path_of_leaf``, so the Overview shows HOW the base was reached
    instead of teleporting from the previous area to the adopted step — but ONLY when the source is in
    the collapse's OWN area. When the base is inherited from an UPSTREAM area, that climb is the
    upstream area's own story (already drawn under it); re-tracing it here would start the collapse
    BEFORE the commit it inherited (e.g. a downstream area branching at the source's pre-winner loop
    instead of the adopted winner). This holds for both SELECT and MERGE collapses. The path nodes are
    suppressed by the frontend on the leaf's own area tab (where the leaf line already draws the path),
    so there is no double-drawing. (The auto ``final_merge`` has role ``final_merge``, not ``collapse``,
    so it is not processed here and never re-traces a child collapse's climb.)"""
    groups = topology.get("groups") or {}
    inherit_anchors = topology.get("inherit_anchors") or {}
    point_at: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        gid = str(row.get("group_id", ""))
        metric = str(row.get("metric_name", ""))
        step = row.get("trajectory_x")
        if gid and metric and step is not None:
            point_at.setdefault((gid, metric, int(step)), row)
    results: list[dict[str, Any]] = []
    for group_id, meta in groups.items():
        if meta.get("role") != "collapse":
            continue
        anchor = inherit_anchors.get(group_id) or {}
        source = str(anchor.get("anchor_source_group") or "")
        step = anchor.get("parent_trajectory_step")
        sha = str(anchor.get("commit_sha") or "")
        if not source or step is None or step == "" or not sha:
            continue
        step = int(step)
        # The adopted source's own anchor step is the lower bound for the path trace. Inherited
        # sources have an explicit anchor; baseline-mode roots implicitly inherit the frozen L0.
        source_anchor_step = _source_anchor_trajectory_step(source, groups, inherit_anchors)
        # Re-trace the adopted source's climb into the base ONLY when that source is within this
        # collapse's OWN area: then the climb (the source leaf's loops from its anchor up to the
        # adopted commit) is the area's internal progression into its base, and drawing it keeps the
        # Overview line continuous instead of teleporting across the hidden source leaf. When the base
        # is INHERITED from an upstream area, that climb belongs to (and is already drawn under) the
        # upstream area; re-emitting it here would make this collapse start BEFORE the commit it
        # inherited — a downstream area branching at the source's pre-winner loop instead of the
        # adopted winner. The base node itself (below) is always emitted at the adopted step. Not
        # gated on SELECT-vs-MERGE: both reach their base via the same leaf climb.
        source_area = str((groups.get(source) or {}).get("area") or "")
        same_area = bool(source_area) and source_area == str(meta.get("area") or "")
        trace = source_anchor_step is not None and same_area
        for metric in metric_names:
            if (group_id, metric, step) not in point_at:  # base node (the adopted commit)
                src_point = point_at.get((source, metric, step))
                if src_point is not None:
                    results.append(
                        {
                            "run_id": f"collapsebase:{group_id}",
                            "group_id": group_id,
                            "metric_name": metric,
                            "metric_value": src_point.get("metric_value"),
                            "loop_index": 0,
                            "trajectory_x": step,
                            "commit_sha": sha,
                            "goal": f"Collapse base — adopted {source}",
                        }
                    )
            if trace:  # re-emit the adopted leaf's climb into the base (Overview-only via path_of_leaf)
                for s in range(source_anchor_step + 1, step):
                    sp = point_at.get((source, metric, s))
                    if sp is None:
                        continue
                    results.append(
                        {
                            "run_id": f"collapsepath:{group_id}:{s}",
                            "group_id": group_id,
                            "metric_name": metric,
                            "metric_value": sp.get("metric_value"),
                            "loop_index": 0,
                            "trajectory_x": s,
                            "commit_sha": sp.get("commit_sha"),
                            "path_of_leaf": source,
                            "goal": f"Path to adopted {source}",
                        }
                    )
    return results


def _source_anchor_trajectory_step(
    group_id: str,
    groups: dict[str, Any],
    inherit_anchors: dict[str, Any],
) -> int | None:
    """Return the trajectory step a source group started from.

    Inherit-mode groups carry this explicitly in ``inherit_anchors``. Baseline-mode groups
    are roots; for trajectory/path purposes they have the implicit frozen L0 anchor.
    """
    anchor = inherit_anchors.get(group_id) or {}
    step = anchor.get("parent_trajectory_step")
    if step not in (None, ""):
        return int(step)
    if (groups.get(group_id) or {}).get("mode") == "baseline":
        return 0
    return None


def _dedupe_metric_rows(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in metrics:
        group_id = str(row.get("group_id", ""))
        loop_index = row.get("loop_index")
        loop_key = str(loop_index) if loop_index not in (None, "") else str(row.get("run_id", ""))
        # The key MUST include metric_name: each (group, loop) carries one row per
        # metric (accuracy, latency_ms, ...). Omitting the metric collapses them into
        # a single row, keeping only the alphabetically-first metric (accuracy) and
        # silently dropping every other tracked metric from the dashboard.
        metric_name = str(row.get("metric_name", ""))
        key = f"{group_id}:{loop_key}:{metric_name}"
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


def _lineage_topology(
    config: HiAgentResearchConfig,
    *,
    registry: Registry | None = None,
    cycles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    group_meta = {
        group.id: {
            "mode": group.lineage.mode,
            "inherit_from": group.lineage.inherit_from,
            # task_kind + its Run Detail label let the frontend present each run in the
            # right frame (e.g. "Change goal" for engineering vs "Hypothesis") without
            # hardcoding task kinds in the UI.
            "task_kind": group.task_kind,
            "intent_label": task_contract(group.task_kind).detail_intent_label,
            "preserve_metrics": task_contract(group.task_kind).preserve_metrics,
            "draw_from": list(group.lineage.draw_from),
            # Hierarchy placement (fan-out areas, §4/§5): the dashboard groups a tab per
            # `area`, listing its `role=="leaf"` groups + the `role=="collapse"` trajectory,
            # plus one tab for the `role=="final_merge"`. Empty for flat (non-fan-out) configs,
            # which render as a single tab = today's page (frontend falls back on empty area).
            "area": group.area,
            "role": group.role,
            # A select collapse (combine:false → loops==0) adopts the strongest leaf and creates
            # no commit of its own; the dashboard resolves its result from the adopted leaf.
            "is_select": group.task_kind == "merge" and group.loops == 0,
            # The leaf's single idea, shown on hover (group id stays the node label so it
            # lines up with the merge sources). Empty for non-leaves.
            "seed_approach": group.seed_approach,
            # The research goal, shown as the tab's description (both fan-out and flat).
            "objective": group.objective,
        }
        for group in config.research_groups
    }
    children: dict[str, list[str]] = {}
    for group in config.research_groups:
        parent = group.lineage.inherit_from
        if parent:
            children.setdefault(parent, []).append(group.id)
    # A merge converges every lineage, so it isn't a node in any single chain: it gets its
    # own dedicated row. The lineages it will combine are known from config alone
    # (`_non_merge_lineage_leaves`), so a configured-but-not-yet-run merge is shown up front
    # (greyed). Its base + ranked sources are resolved best-effort once those lineages have
    # winning commits; until then `resolved` stays False.
    merge_groups: list[dict[str, Any]] = []
    git = GitService(REPO_ROOT) if registry is not None else None
    for group in config.research_groups:
        if group.task_kind != "merge":
            continue
        lineage = group.lineage
        if lineage.inherit_from or lineage.draw_from:
            planned = [s for s in [lineage.inherit_from, *lineage.draw_from] if s]
        else:
            planned = _non_merge_lineage_leaves(config)
        # Ordered participants, base first then integration sources (best→worst). Each is
        # "known" only once its lineage has produced a real (non-baseline) run — until then
        # the merge cannot know its base/commits, so the UI shows placeholders. `planned`
        # carries the config-known lineage names for the no-resolution-yet case.
        participants: list[dict[str, Any]] = []
        no_ops: list[dict[str, Any]] = []
        merge_plan = _latest_merge_plan(cycles or [], group.id)
        if registry is not None:
            if merge_plan:
                participants = _participants_from_merge_plan(merge_plan, registry=registry)
                no_ops = [_participant_from_merge_plan(source, registry=registry) for source in merge_plan.get("no_ops", [])]
            else:
                try:
                    bootstrap = resolve_branch_bootstrap(group, config, registry=registry, git=git)
                except LineageError:
                    bootstrap = None
                if bootstrap is not None:
                    base = bootstrap.merge_base or {"group_id": bootstrap.parent_group_id, "commit_sha": bootstrap.start_ref}
                    ordered = [_participant_from_merge_plan(base, registry=registry)]
                    ordered += [
                        _participant_from_merge_plan(source, registry=registry)
                        for source in bootstrap.merge_sources
                    ]
                    no_ops = [
                        _participant_from_merge_plan(source, registry=registry)
                        for source in bootstrap.merge_no_ops
                    ]
                    participants = ordered
            if participants:
                # Keep the per-group draw_from in sync so Run Detail's "merges in:" tag
                # shows the integration sources (Run Detail reads group_meta, not merge_groups).
                group_meta[group.id]["draw_from"] = [
                    p.get("source_group_id") or p.get("group_id") for p in participants[1:]
                ]
        merge_groups.append(
            {
                "group_id": group.id,
                "branch": group.branch,
                "planned_sources": planned,
                "participants": participants,
                "no_ops": no_ops,
                "merge_plan": merge_plan,
                # A select collapse (combine:false → loops==0) ADOPTS the single strongest leaf
                # rather than integrating them. The dashboard renders it distinctly so the losing
                # competitors aren't shown as folded-in merge steps.
                "is_select": group.loops == 0,
            }
        )
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
        "merge_groups": merge_groups,
        "tabs": _area_tabs(config),
        "area_lineage": _area_lineage(config),
        "execution_waves": config.execution_waves(),
        "baseline_snapshot": baseline_snapshot,
        "orchestration_session": orchestration_session,
        "inherit_anchors": {},
        "group_trajectory_winners": {},
        "lineage_winners": {},
    }


def _latest_merge_plan(cycles: list[dict[str, Any]], group_id: str) -> dict[str, Any] | None:
    matches = [
        cycle.get("merge_plan")
        for cycle in cycles
        if str(cycle.get("group_id", "")) == group_id and isinstance(cycle.get("merge_plan"), dict)
    ]
    return matches[-1] if matches else None


def _participants_from_merge_plan(
    merge_plan: dict[str, Any],
    *,
    registry: Registry,
) -> list[dict[str, Any]]:
    base = merge_plan.get("base")
    if not isinstance(base, dict):
        return []
    return [
        _participant_from_merge_plan(base, registry=registry),
        *[
            _participant_from_merge_plan(source, registry=registry)
            for source in merge_plan.get("fold_ins", [])
            if isinstance(source, dict)
        ],
    ]


def _participant_from_merge_plan(source: dict[str, Any], *, registry: Registry) -> dict[str, Any]:
    group_id = str(source.get("group_id") or "")
    source_group_id = str(source.get("source_group_id") or "")
    participant = {
        "group_id": group_id,
        "source_group_id": source_group_id,
        "branch": str(source.get("branch") or ""),
        "source_branch": str(source.get("source_branch") or ""),
        "commit_sha": str(source.get("commit_sha") or ""),
        "metric_value": source.get("metric_value"),
        "trajectory_step": source.get("trajectory_step"),
        "known": _participant_known(source, registry=registry),
    }
    if source.get("reason"):
        participant["reason"] = str(source.get("reason"))
    return participant


def _participant_known(source: dict[str, Any], *, registry: Registry) -> bool:
    commit_sha = str(source.get("commit_sha") or "")
    if not commit_sha:
        return False
    for gid in (str(source.get("group_id") or ""), str(source.get("source_group_id") or "")):
        if gid and registry.last_github_run(gid) is not None:
            return True
    return False


def _area_lineage(config: HiAgentResearchConfig) -> dict[str, Any]:
    """Research-group/area-level lineage for the Overview tab and the full-from-L0 per-area
    view. Areas are nodes (each represented by its result group — its collapse, or its single
    leaf for a degenerate area); edges are area→area inheritance. Hypotheses are abstracted
    away here. Generalizes the flat case: a linear config's areas ARE its groups, so this
    reduces to the group chains.

    Emits: ``areas`` (area_id → {result_group, ancestors: [root..parent area ids]}),
    ``chains`` (maximal area chains for the Overview map), and ``order`` (areas in config order)."""
    result_group: dict[str, str | None] = {}
    parent_inherit: dict[str, str] = {}  # area → the result-node id its leaves inherit from
    order: list[str] = []
    for group in config.research_groups:
        if not group.area or group.role == "final_merge":
            continue
        if group.area not in result_group:
            result_group[group.area] = None
            order.append(group.area)
        if group.role == "collapse":
            result_group[group.area] = group.id
        elif group.role == "leaf":
            if result_group[group.area] is None:
                result_group[group.area] = group.id  # degenerate area: the leaf is the result
            if group.lineage.inherit_from and group.area not in parent_inherit:
                parent_inherit[group.area] = group.lineage.inherit_from

    id_to_area = {group.id: group.area for group in config.research_groups if group.area}
    parent_area = {area: id_to_area.get(node) for area, node in parent_inherit.items()}

    def ancestors(area: str) -> list[str]:
        chain: list[str] = []
        seen: set[str] = set()
        cur = parent_area.get(area)
        while cur and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = parent_area.get(cur)
        chain.reverse()  # root → … → parent
        return chain

    children: dict[str, list[str]] = {}
    for area in order:
        parent = parent_area.get(area)
        if parent:
            children.setdefault(parent, []).append(area)
    # Maximal area chains for the Overview: one per ROOT→LEAF inheritance path. A branching
    # lineage (e.g. a shared foundation that several areas inherit from) therefore renders as
    # full paths — baseline → root → … → leaf — so the inheritance is visible, instead of the
    # downstream areas collapsing into disconnected "baseline → area" singletons.
    chains: list[list[str]] = []

    def _walk(area: str, prefix: list[str]) -> None:
        path = prefix + [area]
        kids = children.get(area, [])
        if kids:
            for kid in kids:
                _walk(kid, path)
        else:
            chains.append(path)

    for area in order:
        if not parent_area.get(area):  # start only from roots (no parent area)
            _walk(area, [])

    areas = {area: {"result_group": result_group[area], "ancestors": ancestors(area)} for area in order}
    return {"areas": areas, "chains": chains, "order": order}


def _area_tabs(config: HiAgentResearchConfig) -> list[dict[str, Any]]:
    """One tab per fan-out area (its leaf groups + collapse). The final merge gets NO tab of
    its own — it's shown on the Overview (its node in the Merge panel + its chart trajectory),
    which keeps the structure consistent across branching and linear runs.

    Purely a projection of the `area`/`role` group metadata the desugar wrote (§4), so the
    frontend never branches on task-kind strings."""
    objectives = {group.id: group.objective for group in config.research_groups}
    areas: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for group in config.research_groups:
        if not group.area:
            continue
        if group.role == "final_merge":
            continue  # the final merge has no tab of its own — it lives on the Overview
        if group.area not in areas:
            areas[group.area] = {"area": group.area, "leaves": [], "collapse": None, "objective": ""}
            order.append(group.area)
        if group.role == "collapse":
            areas[group.area]["collapse"] = group.id
        else:
            areas[group.area]["leaves"].append(group.id)
            # All leaves of an area share its objective; take the first non-empty one.
            if not areas[group.area]["objective"]:
                areas[group.area]["objective"] = objectives.get(group.id, "")
    # No dedicated final-merge tab: the Overview IS the merge view (its Merge panel shows the
    # final merge, its chart shows the final-merge trajectory). The frontend finds the final
    # merge by role for the Overview.
    return [areas[a] for a in order]


def _lineage_parents(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The lineage DAG as a per-group parent chain — the single structure the frontend walks to
    connect each trajectory to its nearest IN-SCOPE ancestor. This is the natural replacement for
    the inheritance-connector / area-spine / select-result / collapse-base point taxonomy: instead
    of the backend pre-guessing one source + baking visibility flags, it emits the whole ancestry
    and the frontend (which owns scope) picks the first hop that is in view.

    ``primary`` is the ancestor chain, nearest first → the L0 baseline. Each hop is the *direct
    lineage parent group* from ``inherit_anchors`` — which is the area RESULT (a collapse), never
    the hidden adopted leaf (``anchor_source_group``); that single fact is why the recurring
    select-collapse bugs dissolve. The frontend picks the first hop in the active scope: the direct
    parent on a per-area tab, the prior area-result on the Overview — from the same list, with no
    per-view baking. ``secondary`` lists a merge's fold-in sources (non-base participants) for the
    merge arrows.

    Metric-agnostic: each hop carries only {group_id, trajectory_step, commit_sha, is_baseline};
    the frontend resolves the per-metric value from the real rows it already has (L0 via the
    baseline snapshot). Built purely from topology, so it needs no enriched metric rows."""
    groups = topology.get("groups") or {}
    anchors = topology.get("inherit_anchors") or {}
    winners = topology.get("group_trajectory_winners") or {}
    baseline = topology.get("baseline_snapshot") or {}
    baseline_sha = str(baseline.get("commit_sha") or baseline.get("ref") or "")

    # Resolve a commit a merge built on (its base, or a fold-in participant) to the planned SOURCE
    # GROUP whose result is that commit — i.e. the RENDERED area result the frontend draws. For an
    # area collapse that source is a leaf (so this agrees with the recorded owner); for the auto
    # final_merge the source is a terminal COLLAPSE, but the recorded owner flattens to the
    # collapse's hidden adopted leaf (a SELECT collapse has no commit of its own). Without this the
    # chain/arrows would point at a hidden leaf and the frontend would walk past the visible collapse
    # to an earlier area. Returns None when no planned source matches (caller falls back to the
    # recorded owner).
    def planned_source_for(merge: dict[str, Any], commit_sha: str) -> str | None:
        if not commit_sha:
            return None
        for src in merge.get("planned_sources") or []:
            if _sha_match(str((winners.get(str(src)) or {}).get("commit_sha") or ""), str(commit_sha)):
                return str(src)
        return None

    def hop(group_id: str) -> dict[str, Any]:
        # A hop sits at the parent's representative commit — its trajectory winner — which is the
        # commit the descendant inherited from. None step/commit is fine (parent has no run yet);
        # the frontend resolves the value from real rows or skips the hop.
        win = winners.get(group_id) or {}
        return {
            "group_id": group_id,
            "trajectory_step": win.get("trajectory_step"),
            "commit_sha": win.get("commit_sha"),
            "is_baseline": False,
        }

    # A merge's base parent (primary[0]) and its fold-in sources (secondary) are both resolved to
    # rendered area results via planned_source_for, falling back to the recorded owner.
    merge_base: dict[str, str] = {}
    fold_in: dict[str, list[dict[str, Any]]] = {}
    for merge in topology.get("merge_groups") or []:
        gid = str(merge.get("group_id") or "")
        if not gid:
            continue
        base = planned_source_for(merge, str((anchors.get(gid) or {}).get("commit_sha") or ""))
        if base:
            merge_base[gid] = base
        parts = [p for p in (merge.get("participants") or []) if p.get("group_id")]
        if len(parts) > 1:
            seen_fold: set[str] = set()
            edges: list[dict[str, Any]] = []
            for p in parts[1:]:  # parts[0] is the base; the rest are fold-ins
                src = planned_source_for(merge, str(p.get("commit_sha") or "")) or str(p["group_id"])
                if src and src not in seen_fold:
                    seen_fold.add(src)
                    edges.append(hop(src))
            fold_in[gid] = edges

    def parent_of(group_id: str) -> str:
        if group_id in merge_base:
            return merge_base[group_id]
        return str((anchors.get(group_id) or {}).get("parent_group_id") or "")

    l0 = {"group_id": None, "trajectory_step": 0, "commit_sha": baseline_sha, "is_baseline": True}

    out: dict[str, dict[str, Any]] = {}
    for group_id in groups:
        primary: list[dict[str, Any]] = []
        seen: set[str] = set()
        cur = parent_of(group_id)
        while cur and cur not in seen:
            seen.add(cur)
            primary.append(hop(cur))
            cur = parent_of(cur)
        primary.append(l0)
        out[group_id] = {"primary": primary, "secondary": fold_in.get(group_id, [])}
    return out


def _lineage_winner_maps(
    config: HiAgentResearchConfig,
    *,
    registry: Registry,
    metric_override: str | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Compute per-group and per-chain winners.

    By default each group uses its configured ``anchor_metric`` — this drives the
    single trajectory "top commit" shown in the Lineage panel. With
    ``metric_override`` every group's winner is computed for that one metric instead
    (direction derived from its target), which is how the chart stars the best commit
    for whichever metric is being viewed (e.g. lowest latency_ms). ``top_commit_policy``
    is always honored per group (best_commit ⇒ best value; last_commit ⇒ latest run).
    """
    git = GitService(REPO_ROOT)
    group_winners: dict[str, dict[str, Any]] = {}
    for group in config.research_groups:
        if not _group_has_own_trajectory_point(group, registry):
            continue
        policy = group.lineage.top_commit_policy
        metric = metric_override or group.lineage.anchor_metric
        winner = _trajectory_winner_for_group(
            group_id=group.id,
            policy=policy,
            metric=metric,
            baseline_ref=config.orchestration.baseline_ref,
            registry=registry,
            git=git,
            minimize=config.evaluation.metric_minimizes(metric),
        )
        if winner is None:
            continue
        group_winners[group.id] = {
            "group_id": group.id,
            "anchor_policy": policy,
            "anchor_metric": metric,
            **winner,
        }

    # A select collapse (combine:false → loops==0) produces no commit of its own — its result IS
    # the adopted strongest leaf's commit. Resolve it the same way the Merge/Select panel does
    # (the bootstrap base), so the collapse owns a top commit; otherwise it has no run and would
    # vanish from the chart and break the spine. The adopted commit is the result for every metric
    # (a select picks ONE commit), and source_group_id is the collapse itself so its result point
    # (emitted in _collapse_result_points) stars as its own top commit.
    for group in config.research_groups:
        if not (group.task_kind == "merge" and group.loops == 0) or group.id in group_winners:
            continue
        try:
            bootstrap = resolve_branch_bootstrap(group, config, registry=registry, git=git)
        except LineageError:
            continue
        if not bootstrap.start_ref or bootstrap.parent_anchor_step is None:
            continue
        # A SELECT collapse only owns a top commit once its lineage has actually produced one. When
        # no leaf in this area has run yet, the bootstrap resolves up the unrun inherit chain to the
        # frozen L0 baseline (step 0). Leave it unresolved like an unrun inherit leaf, so it doesn't
        # render a spurious ★ "top commit" at the baseline before the area has run.
        if int(bootstrap.parent_anchor_step) == 0:
            continue
        group_winners[group.id] = {
            "group_id": group.id,
            "anchor_policy": group.lineage.top_commit_policy,
            "anchor_metric": metric_override or group.lineage.anchor_metric,
            "commit_sha": bootstrap.start_ref,
            "trajectory_step": int(bootstrap.parent_anchor_step),
            "source_group_id": group.id,
            "is_baseline_anchor": False,
        }

    lineage_winners: dict[str, dict[str, Any]] = {}
    for chain in _lineage_topology(config, registry=registry).get("chains", []):
        if not chain:
            continue
        lineage_id = str(chain[0])
        effective_leaf = _effective_leaf_group_id(chain, group_winners)
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


def _group_has_own_trajectory_point(group: Any, registry: Registry) -> bool:
    """Whether a group has produced a trajectory point it can own as a top commit.

    A GitHub loop run always counts. A *baseline-mode* group additionally counts
    the frozen L0 baseline, so the baseline is the initial top commit (starred)
    before any loop has run. Inherit-mode groups need a loop run of their own:
    their L0 is an ancestor's commit, which is starred on that ancestor — counting
    it here would let an unrun inherit child steal the star (regression guarded by
    test_lineage_winner_after_wave_one_uses_model_not_unrun_inherit_children).
    """
    if registry.last_github_run(group.id) is not None:
        return True
    return group.lineage.mode == "baseline" and registry.baseline_snapshot() is not None


def _effective_leaf_group_id(
    chain: list[str],
    group_winners: dict[str, dict[str, Any]],
) -> str | None:
    """Deepest chain group that owns a trajectory winner.

    ``group_winners`` is already gated by :func:`_group_has_own_trajectory_point`,
    so membership is authoritative: walking the chain backwards yields the deepest
    actually-run group (or the baseline-mode root when only L0 exists), skipping
    not-yet-run inherit children.
    """
    for group_id in reversed(chain):
        if group_id in group_winners:
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
    minimize: bool = False,
) -> dict[str, Any] | None:
    if policy == "best_commit":
        anchor = best_trajectory_anchor(
            parent_group_id=group_id,
            anchor_metric=metric,
            baseline_ref=baseline_ref,
            registry=registry,
            git=git,
            minimize=minimize,
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
    # The chart ★ marks the best commit FOR THE VIEWED metric (direction-aware, per policy) — so the
    # latency chart stars the lowest-latency commit, not the accuracy winner. Per-metric winners live
    # in topology["winners_by_metric"][metric]["group"]; the single trajectory "top commit" (by
    # anchor_metric) lives in topology["lineage_winners"] and drives the flat-config Lineage panel,
    # not these chart flags.
    winners_by_metric = topology.get("winners_by_metric") or {}
    inherit_anchors = topology.get("inherit_anchors") or {}

    group_winners_by_metric: dict[str, dict[str, Any]] = {
        str(metric): (maps.get("group") or {}) for metric, maps in winners_by_metric.items()
    }

    # Inherit anchors (diamonds / branch connectors) are metric-independent.
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
        row_metric = str(row.get("metric_name", ""))
        group_winners = group_winners_by_metric.get(row_metric, {})

        group_winner = group_winners.get(row_group)
        is_group_policy_winner = False
        if group_winner:
            winner_group = str(group_winner.get("group_id") or row_group)
            winner_sha = str(group_winner.get("commit_sha") or "").lower()
            if group_winner.get("is_baseline_anchor"):
                is_group_policy_winner = bool(row.get("is_baseline_anchor")) and row_group == winner_group
            else:
                # group_winners is keyed by row_group, so the winner already belongs to THIS group's
                # trajectory — match the row carrying its winning commit by sha. We deliberately do
                # NOT also require the row's source to equal the winner's source: when a collapse never
                # beats the leaf commit it inherited (e.g. a merge that regressed on every loop), the
                # winning commit's source is that upstream leaf, yet the best node still lives on the
                # collapse's own series and must get the ★.
                is_group_policy_winner = bool(row_sha) and _sha_match(row_sha, winner_sha)

        matching_inherit_groups = inherit_by_source.get((row_group, row_sha), [])
        is_inherit_anchor = bool(matching_inherit_groups)
        # Backend is the single source of truth for the chart symbol; the frontend just reads `symbol`.
        # ★ = this group/area's BEST commit for the viewed metric (matches the Lineage/Merge panels, so
        # plot and panels agree and you can see merge candidates / SELECT competition at a glance).
        # ◆ = a commit a downstream group inherits from (an anchor).
        symbol = "star" if is_group_policy_winner else "diamond" if is_inherit_anchor else "circle"
        annotated.append(
            {
                **row,
                "is_group_policy_winner": is_group_policy_winner,
                "is_inherit_anchor": is_inherit_anchor,
                "inherit_anchor_for_groups": matching_inherit_groups,
                "symbol": symbol,
            }
        )
    return annotated


def _now_iso() -> str:
    from hiagentresearch.src.core.models import utc_now_iso

    return utc_now_iso()


def _inherit_anchors_combined(
    config: HiAgentResearchConfig,
    registry: Registry,
    cycles: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    anchors = _inherit_anchors_from_cycles(cycles, runs)
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
    # NOTE: this loop only adds config-declared inherit-mode groups. A merge (area collapse or
    # final merge) gets its anchor elsewhere: _inherit_anchors_from_cycles (above) records it from
    # the merge's own run rows, and _lineage_parents resolves its base parent via planned_source_for.
    # So merges are NOT added here — they have no lineage.inherit_from to bootstrap from.
    return anchors


def _inherit_anchors_from_cycles(
    cycles: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    commit_owners = _commit_owner_groups(cycles, runs)
    anchors: dict[str, dict[str, Any]] = {}
    for row in cycles:
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
                cycles=cycles,
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
    cycles: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> dict[str, str]:
    """Map each run commit sha to the group that produced it."""
    runs_by_id = {str(row["run_id"]): row for row in runs}
    owners: dict[str, str] = {}
    for cycle in cycles:
        run_id = str(cycle.get("run_id", ""))
        commit_sha = str(runs_by_id.get(run_id, {}).get("commit_sha", "") or "").strip().lower()
        group_id = str(cycle.get("group_id", "") or "").strip()
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

    from hiagentresearch.src.github.actions import _parse_remote_url

    # In GitHub Actions these env vars are set; locally we derive the web URL from the
    # configured git remote. Parsing is host-agnostic (via _parse_remote_url), so
    # GitHub Enterprise remotes (e.g. github.disney.com) resolve to a real web URL and
    # repo slug — not just public github.com.
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository:
        web_url = f"{server_url}/{repository}"
    else:
        host, slug = _parse_remote_url(_git_remote_url(config.github.remote))
        web_url = f"https://{host}/{slug}" if host and slug else ""
        repository = slug
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

    # A baseline eval is the frozen L0 origin, not a research loop: record it as the
    # single-source baseline run (same path as the live ingest) and stop, so it never
    # appears as a phantom loop under the anchor group's series.
    if str(meta.get("node_kind", "")) == "baseline":
        record_baseline_snapshot_from_metrics(
            registry,
            ref=str(meta.get("baseline_ref") or meta.get("branch") or "main"),
            metrics={key: float(value) for key, value in metrics.items()},
            required=required_baseline_metrics(config.evaluation.targets),
        )
        return True

    run_id = str(meta.get("run_id") or f"gh_{meta.get('workflow_run_id', artifact_dir.name)}")
    group_id = str(meta.get("group_id", "unknown"))
    branch = str(meta.get("branch", "unknown"))
    failure_class = str(failure.get("failure_class", "infra_failure"))
    manifest_path = artifact_dir / CYCLE_MANIFEST
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_source = CYCLE_MANIFEST
    else:
        manifest = build_synthetic_cycle_manifest(
            run_id=run_id,
            group_id=group_id,
            branch=branch,
            meta=meta,
        )
        manifest_source = "(synthetic:missing cycle_manifest.json)"
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
    registry.record_cycle_manifest(
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
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = ?",
        ("orchestration_session",),
    ).fetchone()
    if row:
        try:
            payload = json.loads(str(row[0]))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("started_at"):
            return str(payload["started_at"])
    # Fallback: the frozen baseline run anchors the session start.
    baseline = conn.execute(
        "SELECT created_at FROM runs WHERE group_id = ? AND failure_class = 'none' "
        "ORDER BY created_at ASC LIMIT 1",
        (BASELINE_RUN_GROUP,),
    ).fetchone()
    return str(baseline[0]) if baseline else None


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


