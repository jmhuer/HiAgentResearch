from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hiagentresearch.src.core.config import HiAgentResearchConfig, ResearchGroupConfig
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.lineage.anchors import best_trajectory_anchor, parent_trajectory_step_for_run

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
    start_ref, parent_anchor_step = _resolve_inherit_ref(
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
    )


def _resolve_inherit_ref(
    *,
    parent: ResearchGroupConfig,
    anchor_policy: str,
    anchor_metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
) -> tuple[str, int | None]:
    if anchor_policy == "best_commit":
        anchor = best_trajectory_anchor(
            parent_group_id=parent.id,
            anchor_metric=anchor_metric,
            baseline_ref=baseline_ref,
            registry=registry,
            git=git,
        )
        if anchor:
            return anchor.ref, anchor.trajectory_step
    elif anchor_policy == "last_commit":
        row = registry.last_github_run(parent.id)
        if row and row.get("commit_sha"):
            step = parent_trajectory_step_for_run(registry, parent.id, str(row["run_id"]))
            return str(row["commit_sha"]), step
    else:
        raise LineageError(f"unknown anchor_policy: {anchor_policy}")

    for ref in (f"origin/{parent.branch}", parent.branch):
        if git.ref_exists(ref):
            return git.resolve_ref(ref), None
    raise LineageError(
        f"could not resolve inherit anchor for parent group {parent.id} (branch {parent.branch})"
    )
