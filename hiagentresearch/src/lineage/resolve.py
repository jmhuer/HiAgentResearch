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
    # Merge groups only: the OTHER lineage winners to fold in, ranked best→worst.
    # Each entry: {"group_id", "branch", "commit_sha", "metric_value"}. The branch
    # starts from the strongest (parent_group_id / start_ref); these are integrated.
    merge_sources: tuple[dict, ...] = ()


def resolve_branch_bootstrap(
    group: ResearchGroupConfig,
    config: HiAgentResearchConfig,
    *,
    registry: Registry,
    git: GitService,
) -> BranchBootstrap:
    lineage = group.lineage
    mode = lineage.mode
    if group.task_kind == "merge":
        return _resolve_merge_bootstrap(group, config, registry=registry, git=git)
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
        anchor_policy=lineage.inherit_policy,
        anchor_metric=lineage.anchor_metric,
        baseline_ref=config.orchestration.baseline_ref,
        registry=registry,
        git=git,
        minimize=config.evaluation.metric_minimizes(lineage.anchor_metric),
    )
    return BranchBootstrap(
        branch=group.branch,
        mode=mode,
        start_ref=start_ref,
        parent_group_id=parent.id,
        anchor_policy=lineage.inherit_policy,
        anchor_metric=lineage.anchor_metric,
        parent_anchor_step=parent_anchor_step,
        anchor_source_group_id=anchor_source_group,
    )


def _non_merge_lineage_leaves(config: HiAgentResearchConfig) -> list[str]:
    """The tip of each non-merge lineage: a non-merge group that no other non-merge
    group inherits from. Each leaf's best_trajectory_anchor is that lineage's winner."""
    non_merge = [g for g in config.research_groups if g.task_kind != "merge"]
    inherited = {g.lineage.inherit_from for g in non_merge if g.lineage.inherit_from}
    return [g.id for g in non_merge if g.id not in inherited]


def _lineage_ancestor_group_ids(config: HiAgentResearchConfig, group_id: str) -> set[str]:
    """The groups strictly upstream of ``group_id`` on the lineage DAG — its ancestor
    set, by walking ``inherit_from`` edges to the root. This is OUR graph (the desugared
    config wires every leaf/collapse to its parent), so reachability questions are answered
    here, not by shelling out to the git commit DAG."""
    seen: set[str] = set()
    cur = config.group_by_id(group_id).lineage.inherit_from
    while cur and cur not in seen:
        seen.add(cur)
        cur = config.group_by_id(cur).lineage.inherit_from
    return seen


def _area_result_node(config: HiAgentResearchConfig, group_id: str) -> str:
    """Map a group to the node that REPRESENTS its area on the lineage DAG. A leaf's winning
    commit is folded into / adopted by its area's collapse, so the area-level identity of a
    leaf is that collapse (lineage edges run collapse→collapse, never through leaves). A
    collapse / final-merge / flat group already is its own representative."""
    group = config.group_by_id(group_id)
    if group.role == "leaf" and group.area:
        for candidate in config.research_groups:
            if candidate.area == group.area and candidate.role in ("collapse", "final_merge"):
                return candidate.id
    return group_id


def _resolve_merge_bootstrap(
    group: ResearchGroupConfig,
    config: HiAgentResearchConfig,
    *,
    registry: Registry,
    git: GitService,
) -> BranchBootstrap:
    """Auto-resolve a merge: rank every source lineage's winner by the anchor metric,
    start the branch from the strongest, and carry the rest (best→worst) as merge
    sources for the agent to integrate. Sources default to all non-merge lineage tips;
    an explicit inherit_from/draw_from overrides the discovery."""
    lineage = group.lineage
    metric = lineage.anchor_metric
    minimize = config.evaluation.metric_minimizes(metric)
    baseline_ref = config.orchestration.baseline_ref

    if lineage.inherit_from or lineage.draw_from:
        source_ids = [s for s in [lineage.inherit_from, *lineage.draw_from] if s]
    else:
        source_ids = _non_merge_lineage_leaves(config)

    resolved: list[dict] = []
    for gid in source_ids:
        # Pick each source's representative commit by ITS OWN top_commit_policy, not always
        # best_commit. An engineering source (e.g. a polish area) preserves the metric, so its
        # winning commit is its latest one (last_commit) — best_commit would fall back past it
        # to the ancestor that owns the metric peak, dropping the engineering work from the merge.
        source_policy = config.group_by_id(gid).lineage.top_commit_policy
        anchor = _policy_anchor(
            policy=source_policy,
            parent_group_id=gid,
            anchor_metric=metric,
            baseline_ref=baseline_ref,
            registry=registry,
            git=git,
            minimize=minimize,
        )
        if anchor is None or not anchor.ref:
            continue
        # source_group_id is None when the BASELINE (L0) is this source's best — no commit on it
        # beat the baseline it started from. Keep that honest (do NOT relabel baseline as the
        # leaf): owner None means "baseline / no contribution", with no leaf branch to fold in.
        owner = anchor.source_group_id
        resolved.append(
            {
                "group_id": owner,
                "branch": config.group_by_id(owner).branch if owner else "",
                "commit_sha": anchor.ref,
                "metric_value": anchor.metric_value,
                # Global axis position of the source's winning commit, so the merge can be
                # drawn as a CONTINUATION of the strongest source (its loops at base_step+k)
                # rather than floating back at L0.
                "trajectory_step": anchor.trajectory_step,
            }
        )
    if not resolved:
        raise LineageError(
            f"merge group {group.id}: no source lineage has a winning commit yet "
            f"(sources={source_ids})"
        )

    # Best first (direction-aware): higher score = better.
    resolved.sort(key=lambda s: (-s["metric_value"] if minimize else s["metric_value"]), reverse=True)
    base, *rest = resolved
    # A fold-in only contributes if it adds something the base does not ALREADY contain —
    # a reachability question on the lineage DAG. A source whose winning commit resolved back
    # to an ancestor of the base (it never beat the floor it inherited, so best_commit fell
    # through to a commit already on the base's path) integrates nothing. Drop those: map each
    # source's commit-owner to its area node and keep it only when that node is NOT upstream of
    # the base. This subsumes the old "group_id is None" (frozen L0) guard — L0 is upstream of
    # everything — into the single graph rule, and is direction/policy agnostic.
    base_ancestors = _lineage_ancestor_group_ids(config, base["group_id"]) if base["group_id"] else set()
    merge_sources = tuple(
        s
        for s in rest
        if s["group_id"] and _area_result_node(config, s["group_id"]) not in base_ancestors
    )
    return BranchBootstrap(
        branch=group.branch,
        mode="inherit",
        start_ref=base["commit_sha"],
        parent_group_id=base["group_id"],
        anchor_policy=lineage.inherit_policy,
        anchor_metric=metric,
        parent_anchor_step=base.get("trajectory_step"),
        anchor_source_group_id=base["group_id"],
        merge_sources=merge_sources,
    )


def _resolve_inherit_ref(
    *,
    parent: ResearchGroupConfig,
    anchor_policy: str,
    anchor_metric: str,
    baseline_ref: str,
    registry: Registry,
    git: GitService,
    minimize: bool = False,
) -> tuple[str, int | None, str | None]:
    anchor = _policy_anchor(
        policy=anchor_policy,
        parent_group_id=parent.id,
        anchor_metric=anchor_metric,
        baseline_ref=baseline_ref,
        registry=registry,
        git=git,
        minimize=minimize,
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
    minimize: bool = False,
) -> TrajectoryAnchor | None:
    if policy == "best_commit":
        return best_trajectory_anchor(
            parent_group_id=parent_group_id,
            anchor_metric=anchor_metric,
            baseline_ref=baseline_ref,
            registry=registry,
            git=git,
            minimize=minimize,
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
