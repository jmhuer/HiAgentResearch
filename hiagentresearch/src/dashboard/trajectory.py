from __future__ import annotations

from typing import Any


def group_wave_index(execution_waves: list[list[str]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for wave_index, wave in enumerate(execution_waves):
        for group_id in wave:
            mapping[group_id] = wave_index
    return mapping


def wave_depths(
    points: list[dict[str, Any]],
    execution_waves: list[list[str]],
) -> list[int]:
    """Cumulative loop depth before each orchestration wave."""
    depths: list[int] = []
    cumulative = 0
    for wave in execution_waves:
        depths.append(cumulative)
        max_loop = 0
        for group_id in wave:
            for point in points:
                if point.get("group_id") != group_id:
                    continue
                loop_index = _loop_index(point)
                if loop_index is not None:
                    max_loop = max(max_loop, loop_index)
        cumulative += max_loop
    return depths


def assign_trajectory_positions(
    points: list[dict[str, Any]],
    topology: dict[str, Any],
) -> list[dict[str, Any]]:
    """Map loop work onto a shared lineage axis.

    L0 is reserved for the frozen baseline anchor. Loop *k* in execution wave *w*
    is placed at L(depth[w] + k), where depth advances by the deepest loop reached
    in each prior wave (parallel groups in the same wave share depth).
    """
    waves: list[list[str]] = list(topology.get("execution_waves") or [])
    if not waves:
        return [
            {
                **point,
                "trajectory_x": _loop_index(point) or 0,
            }
            for point in points
        ]

    group_wave = group_wave_index(waves)
    depths = wave_depths(points, waves)
    positioned: list[dict[str, Any]] = []
    for point in points:
        loop_index = _loop_index(point)
        if loop_index is None or loop_index <= 0:
            positioned.append({**point, "trajectory_x": 0})
            continue
        wave_index = group_wave.get(str(point.get("group_id", "")), 0)
        trajectory_x = depths[wave_index] + loop_index
        positioned.append(
            {
                **point,
                "trajectory_x": trajectory_x,
                "lineage_wave": wave_index,
            }
        )
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


def _loop_index(point: dict[str, Any]) -> int | None:
    raw = point.get("loop_index")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
