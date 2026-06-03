from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hiagentresearch.src.core.config import HiAgentResearchConfig, ResearchGroupConfig
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.lineage.anchors import (
    TrajectoryAnchor,
    best_trajectory_anchor,
    last_trajectory_anchor,
)

if TYPE_CHECKING:
    from hiagentresearch.src.registry.store import Registry


class LineageError(ValueError):
    """Raised when lineage configuration or resolution fails."""


@dataclass(frozen=True, slots=True)
class BranchBootstrap:
    branch: str
    mode: str
    start_ref: str
    parent_group_id: str | None = None
    anchor_policy: str | None = None
    anchor_metric: str | None = None
    parent_anchor_step: int | None = None
    # Group that actually owns the anchor commit; may be an ancestor (not the
    # immediate parent) when the parent never beat the baseline it inherited.
    anchor_source_group_id: str | None = None


def resolve_branch_bootstrap(
    group: ResearchGroupConfig,
    config: HiAgentResearchConfig,
    *,
    registry: Registry,
    git: GitService,
) -> BranchBootstrap:
    lineage = group.lineage
    mode = lineage.mode
    if mode == "force":
        raise LineageError(
            "lineage mode 'force' is reserved for Phase 2 promotion and is not implemented yet"
        )
    if mode == "baseline":
        start_ref = git.resolve_ref(config.orchestration.baseline_ref)
        return BranchBootstrap(
            branch=group.branch,
            mode=mode,
            start_ref=start_ref,
        )
    if mode != "inherit":
        raise LineageError(f"unknown lineage mode: {mode}")
    if not lineage.inherit_from:
        raise LineageError(f"group {group.id} requires lineage.inherit_from when mode is inherit")

    parent = config.group_by_id(lineage.inherit_from)
    start_ref, parent_anchor_step, anchor_source_group = _resolve_inherit_ref(
        parent=parent,
        anchor_policy=lineage.anchor_policy,
        anchor_metric=lineage.anchor_metric,
        baseline_ref=config.orchestration.baseline_ref,
        registry=registry,
        git=git,
    )
    return BranchBootstrap(
        branch=group.branch,
        mode=mode,
        start_ref=start_ref,
        parent_group_id=parent.id,
        anchor_policy=lineage.anchor_policy,
        anchor_metric=lineage.anchor_metric,
        parent_anchor_step=parent_anchor_step,
        anchor_source_group_id=anchor_source_group,
    )


def _resolve_inherit_ref(
    *,
    parent: ResearchGroupConfig,
    anchor_policy: str,
    anchor_metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
) -> tuple[str, int | None, str | None]:
    anchor = _policy_anchor(
        policy=anchor_policy,
        parent_group_id=parent.id,
        anchor_metric=anchor_metric,
        baseline_ref=baseline_ref,
        registry=registry,
        git=git,
    )
    if anchor is not None:
        return anchor.ref, anchor.trajectory_step, anchor.source_group_id

    for ref in (f"origin/{parent.branch}", parent.branch):
        if git.ref_exists(ref):
            return git.resolve_ref(ref), None, None
    raise LineageError(
        f"could not resolve inherit anchor for parent group {parent.id} (branch {parent.branch})"
    )


def _policy_anchor(
    *,
    policy: str,
    parent_group_id: str,
    anchor_metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
) -> TrajectoryAnchor | None:
    if policy == "best_commit":
        return best_trajectory_anchor(
            parent_group_id=parent_group_id,
            anchor_metric=anchor_metric,
            baseline_ref=baseline_ref,
            registry=registry,
            git=git,
        )
    if policy == "last_commit":
        return last_trajectory_anchor(
            parent_group_id=parent_group_id,
            anchor_metric=anchor_metric,
            baseline_ref=baseline_ref,
            registry=registry,
            git=git,
        )
    raise LineageError(f"unknown anchor_policy: {policy}")
