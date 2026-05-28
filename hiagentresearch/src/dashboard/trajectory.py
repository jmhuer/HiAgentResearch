from __future__ import annotations

from typing import Any


def assign_trajectory_positions(
    points: list[dict[str, Any]],
    topology: dict[str, Any],
) -> list[dict[str, Any]]:
    """Map metrics onto a shared lineage axis counting accepted agent loops.

    L0 is the frozen baseline anchor. For baseline-mode groups, loop *k* is at L*k*.
    For inherited groups, L(parent_anchor_loop_index + k) where the parent anchor is
    the inherited parent commit's loop index (not the max loops across a wave).
  """
    group_meta: dict[str, Any] = topology.get("groups") or {}
    inherit_anchors: dict[str, Any] = topology.get("inherit_anchors") or {}
    positioned: list[dict[str, Any]] = []
    for point in points:
        if point.get("is_baseline_anchor"):
            positioned.append({**point, "trajectory_x": 0})
            continue
        loop_index = _loop_index(point)
        if loop_index is None:
            positioned.append({**point, "trajectory_x": 0})
            continue
        group_id = str(point.get("group_id", ""))
        mode = str(group_meta.get(group_id, {}).get("mode") or "baseline")
        if mode == "inherit":
            anchor = inherit_anchors.get(group_id) or {}
            parent_loops = int(anchor.get("parent_anchor_loop_index") or 0)
            trajectory_x = parent_loops + loop_index
        else:
            trajectory_x = loop_index
        positioned.append({**point, "trajectory_x": trajectory_x})
    return positioned


def baseline_metric_points(
    *,
    metric_name: str,
    group_ids: list[str],
    baseline_snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not baseline_snapshot:
        return []
    metrics = baseline_snapshot.get("metrics") or {}
    if metric_name not in metrics:
        return []
    try:
        metric_value = float(metrics[metric_name])
    except (TypeError, ValueError):
        return []
    ref = str(baseline_snapshot.get("ref") or "main")
    run_id = f"baseline:{ref}"
    return [
        {
            "run_id": run_id,
            "group_id": group_id,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "loop_index": 0,
            "trajectory_x": 0,
            "is_baseline_anchor": True,
            "research_outcome": "baseline",
            "hypothesis": f"Frozen eval anchor ({ref})",
        }
        for group_id in group_ids
    ]


def parent_anchor_loop_index(
    *,
    parent_group_id: str,
    commit_sha: str,
    experiments: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> int:
    target = commit_sha.strip().lower()
    if not target or not parent_group_id:
        return 0
    runs_by_id = {str(row["run_id"]): row for row in runs}
    matched = 0
    for experiment in experiments:
        if str(experiment.get("group_id", "")) != parent_group_id:
            continue
        run_id = str(experiment.get("run_id", ""))
        run_sha = str(runs_by_id.get(run_id, {}).get("commit_sha", "") or "").strip().lower()
        if not run_sha or not _sha_matches(run_sha, target):
            continue
        loop_index = _loop_index(experiment)
        if loop_index is not None:
            matched = max(matched, loop_index)
    return matched


def _sha_matches(left: str, right: str) -> bool:
    return left == right or left.startswith(right) or right.startswith(left)


def _loop_index(point: dict[str, Any]) -> int | None:
    raw = point.get("loop_index")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
