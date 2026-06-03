from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hiagentresearch.src.git.service import GitService

if TYPE_CHECKING:
    from hiagentresearch.src.registry.store import Registry


@dataclass(frozen=True, slots=True)
class TrajectoryAnchor:
    """A point on a group's lineage axis, expressed as a global coordinate.

    ``trajectory_step`` counts accepted agent loops since the original (L0) baseline,
    so it is comparable across groups. ``source_group_id`` names the group that owns
    the commit (``None`` => the frozen L0 baseline), which may be an ancestor when an
    inherited group never beat the baseline it started from.
    """

    ref: str
    trajectory_step: int
    metric_value: float
    source_group_id: str | None = None


def best_trajectory_anchor(
    *,
    parent_group_id: str,
    anchor_metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
) -> TrajectoryAnchor | None:
    """Pick the best metric on the parent trajectory (its relative L0 + its loops).

    The relative L0 is the parent's own origin point, which is itself the best point
    of whatever the parent inherited from. Loops are placed at ``base_step + loop_index``
    so the returned step is a single global axis position. When the origin wins (the
    parent never improved on its baseline), the anchor naturally points back to the
    ancestor that produced the best commit.
    """
    origin = origin_trajectory_anchor(
        parent_group_id=parent_group_id,
        anchor_metric=anchor_metric,
        baseline_ref=baseline_ref,
        registry=registry,
        git=git,
    )
    base_step = origin.trajectory_step if origin is not None else 0
    candidates: list[TrajectoryAnchor] = []
    if origin is not None:
        candidates.append(origin)
    candidates.extend(
        loop_trajectory_anchors(
            parent_group_id=parent_group_id,
            anchor_metric=anchor_metric,
            base_step=base_step,
            registry=registry,
        )
    )
    if not candidates:
        return None
    return max(candidates, key=lambda item: _rank(item.metric_value, anchor_metric))


def last_trajectory_anchor(
    *,
    parent_group_id: str,
    anchor_metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
) -> TrajectoryAnchor | None:
    """Pick the latest successful commit on the parent trajectory."""
    row = registry.last_github_run(parent_group_id)
    if not row or not row.get("commit_sha"):
        return None
    commit_sha = str(row["commit_sha"])
    origin = origin_trajectory_anchor(
        parent_group_id=parent_group_id,
        anchor_metric=anchor_metric,
        baseline_ref=baseline_ref,
        registry=registry,
        git=git,
    )
    base_step = origin.trajectory_step if origin is not None else 0
    loop_index = parent_trajectory_step_for_run(registry, parent_group_id, str(row["run_id"]))
    metric_value = registry.metric_for_group_commit(parent_group_id, commit_sha, anchor_metric)
    return TrajectoryAnchor(
        ref=commit_sha,
        trajectory_step=base_step + loop_index,
        metric_value=float(metric_value) if metric_value is not None else 0.0,
        source_group_id=parent_group_id,
    )


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
                    source_group_id=inherited_group_id,
                )
    snapshot = registry.baseline_snapshot()
    metrics = (snapshot or {}).get("metrics") or {}
    if anchor_metric in metrics and metrics[anchor_metric] is not None:
        return TrajectoryAnchor(
            ref=git.resolve_ref(baseline_ref),
            trajectory_step=0,
            metric_value=float(metrics[anchor_metric]),
            source_group_id=None,
        )
    return None


def loop_trajectory_anchors(
    *,
    parent_group_id: str,
    anchor_metric: str,
    base_step: int,
    registry: Registry,
) -> list[TrajectoryAnchor]:
    """Resolve parent loop points from successful GitHub runs as global coordinates.

    ``base_step`` is the global axis position of the parent's relative L0, so loop *k*
    lands at ``base_step + k``.
    """
    anchors: list[TrajectoryAnchor] = []
    for row in registry.github_runs_with_metric(parent_group_id, anchor_metric):
        commit_sha = str(row.get("commit_sha", "")).strip()
        metric_value = row.get("metric_value")
        if not commit_sha or metric_value is None:
            continue
        loop_index = parent_trajectory_step_for_run(registry, parent_group_id, str(row["run_id"]))
        anchors.append(
            TrajectoryAnchor(
                ref=commit_sha,
                trajectory_step=base_step + loop_index,
                metric_value=float(metric_value),
                source_group_id=parent_group_id,
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
