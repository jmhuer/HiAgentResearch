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
    """Pick the best metric on the parent's trajectory, including L0 baseline."""
    candidates: list[TrajectoryAnchor] = []

    snapshot = registry.baseline_snapshot()
    metrics = (snapshot or {}).get("metrics") or {}
    if anchor_metric in metrics and metrics[anchor_metric] is not None:
        candidates.append(
            TrajectoryAnchor(
                ref=git.resolve_ref(baseline_ref),
                trajectory_step=0,
                metric_value=float(metrics[anchor_metric]),
            )
        )

    row = registry.best_github_run(parent_group_id, anchor_metric)
    if row and row.get("commit_sha"):
        run_metrics = registry.metrics_for_run(str(row["run_id"]))
        score = run_metrics.get(anchor_metric)
        if score is not None:
            step = parent_trajectory_step_for_run(registry, parent_group_id, str(row["run_id"]))
            candidates.append(
                TrajectoryAnchor(
                    ref=str(row["commit_sha"]),
                    trajectory_step=step,
                    metric_value=float(score),
                )
            )

    if not candidates:
        return None
    return max(candidates, key=lambda item: _rank(item.metric_value, anchor_metric))


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
