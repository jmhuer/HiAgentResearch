from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hiagentresearch.src.git.service import GitService

if TYPE_CHECKING:
    from hiagentresearch.src.registry.store import Registry


@dataclass(frozen=True, slots=True)
class TrajectoryAnchor:
    """A point on a parent group's lineage axis (L0 frozen baseline or Lk loop)."""

    ref: str
    trajectory_step: int
    metric_value: float


def best_trajectory_anchor(
    *,
    parent_group_id: str,
    anchor_metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
) -> TrajectoryAnchor | None:
    """Pick the best metric on the parent trajectory (origin + loops)."""
    candidates: list[TrajectoryAnchor] = []
    origin = origin_trajectory_anchor(
        parent_group_id=parent_group_id,
        anchor_metric=anchor_metric,
        baseline_ref=baseline_ref,
        registry=registry,
        git=git,
    )
    if origin is not None:
        candidates.append(origin)
    candidates.extend(
        loop_trajectory_anchors(
            parent_group_id=parent_group_id,
            anchor_metric=anchor_metric,
            registry=registry,
        )
    )
    if not candidates:
        return None
    return max(candidates, key=lambda item: _rank(item.metric_value, anchor_metric))


def origin_trajectory_anchor(
    *,
    parent_group_id: str,
    anchor_metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
) -> TrajectoryAnchor | None:
    """Resolve the parent origin point (L0 for baseline groups, inherited anchor for inherit groups)."""
    origin = registry.earliest_experiment(parent_group_id)
    if origin:
        inherited_group_id = str(origin.get("lineage_parent_group_id") or "").strip()
        inherited_sha = str(origin.get("lineage_anchor_sha") or "").strip()
        if inherited_group_id and inherited_sha:
            metric_value = registry.metric_for_group_commit(inherited_group_id, inherited_sha, anchor_metric)
            if metric_value is not None:
                return TrajectoryAnchor(
                    ref=inherited_sha,
                    trajectory_step=int(origin.get("lineage_parent_anchor_step") or 0),
                    metric_value=float(metric_value),
                )
    snapshot = registry.baseline_snapshot()
    metrics = (snapshot or {}).get("metrics") or {}
    if anchor_metric in metrics and metrics[anchor_metric] is not None:
        return TrajectoryAnchor(
            ref=git.resolve_ref(baseline_ref),
            trajectory_step=0,
            metric_value=float(metrics[anchor_metric]),
        )
    return None


def loop_trajectory_anchors(
    *,
    parent_group_id: str,
    anchor_metric: str,
    registry: Registry,
) -> list[TrajectoryAnchor]:
    """Resolve parent loop points (L1..Lk) from successful GitHub runs."""
    anchors: list[TrajectoryAnchor] = []
    for row in registry.github_runs_with_metric(parent_group_id, anchor_metric):
        commit_sha = str(row.get("commit_sha", "")).strip()
        metric_value = row.get("metric_value")
        if not commit_sha or metric_value is None:
            continue
        anchors.append(
            TrajectoryAnchor(
                ref=commit_sha,
                trajectory_step=parent_trajectory_step_for_run(registry, parent_group_id, str(row["run_id"])),
                metric_value=float(metric_value),
            )
        )
    return anchors


def parent_trajectory_step_for_run(registry: Registry, parent_group_id: str, run_id: str) -> int:
    experiment = registry.experiment_for_run(run_id)
    if experiment and str(experiment.get("group_id", "")) == parent_group_id:
        loop_index = experiment.get("loop_index")
        if loop_index is not None:
            return int(loop_index)
    return 1


def _rank(value: float, metric_name: str) -> float:
    if metric_name == "latency_ms":
        return -value
    return value
