from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hiagentresearch.src.core.config import HiAgentResearchConfig, ResearchGroupConfig
from hiagentresearch.src.git.service import GitService

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
    start_ref = _resolve_inherit_ref(
        parent=parent,
        anchor_policy=lineage.anchor_policy,
        anchor_metric=lineage.anchor_metric,
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
    )


def _resolve_inherit_ref(
    *,
    parent: ResearchGroupConfig,
    anchor_policy: str,
    anchor_metric: str,
    registry: Registry,
    git: GitService,
) -> str:
    if anchor_policy == "best_commit":
        row = registry.best_github_run(parent.id, anchor_metric)
        if row and row.get("commit_sha"):
            return str(row["commit_sha"])
    elif anchor_policy == "last_commit":
        row = registry.last_github_run(parent.id)
        if row and row.get("commit_sha"):
            return str(row["commit_sha"])
    else:
        raise LineageError(f"unknown anchor_policy: {anchor_policy}")

    for ref in (f"origin/{parent.branch}", parent.branch):
        if git.ref_exists(ref):
            return git.resolve_ref(ref)
    raise LineageError(
        f"could not resolve inherit anchor for parent group {parent.id} (branch {parent.branch})"
    )
