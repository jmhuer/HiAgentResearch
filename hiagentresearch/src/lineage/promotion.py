from __future__ import annotations

from dataclasses import asdict, dataclass

from hiagentresearch.src.core.config import HiAgentResearchConfig
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.lineage.resolve import LineageError, resolve_policy_anchor
from hiagentresearch.src.registry.store import Registry


@dataclass(frozen=True, slots=True)
class PromotionAnchor:
    project_id: str
    baseline_ref: str
    baseline_sha: str
    promote_from_group: str
    promote_branch: str
    top_commit_policy: str
    anchor_metric: str
    commit_sha: str
    metric_value: float
    source_group_id: str | None
    trajectory_step: int
    workdir: str

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_promotion_anchor(
    *,
    config: HiAgentResearchConfig,
    registry: Registry,
    git: GitService,
    group_id: str = "",
    commit_sha: str = "",
) -> PromotionAnchor:
    """Resolve the policy-selected research commit to promote onto a baseline branch.

    ``group_id`` overrides ``orchestration.promote_from_group``. When neither is
    set, choose the configured group whose own policy-selected anchor has the
    strongest score, respecting the metric direction from config.
    ``commit_sha`` is an operator override for the final commit while preserving
    the same provenance fields where possible.
    """
    resolved_group_id = group_id.strip() or config.orchestration.promote_from_group.strip()
    if not resolved_group_id:
        resolved_group_id = _auto_promote_group_id(config=config, registry=registry, git=git)

    group = config.group_by_id(resolved_group_id)
    anchor_metric = group.lineage.anchor_metric
    anchor = resolve_policy_anchor(
        policy=group.lineage.top_commit_policy,
        parent_group_id=group.id,
        anchor_metric=anchor_metric,
        baseline_ref=config.orchestration.baseline_ref,
        registry=registry,
        git=git,
        minimize=config.evaluation.metric_minimizes(anchor_metric),
    )
    override_sha = commit_sha.strip()
    if anchor is None and not override_sha:
        raise LineageError(
            f"no promotion commit for group {resolved_group_id} "
            f"(policy={group.lineage.top_commit_policy}, metric={anchor_metric})"
        )
    selected_sha = override_sha or (anchor.ref if anchor else "")
    if not selected_sha:
        raise LineageError(f"no promotion commit selected for group {resolved_group_id}")

    return PromotionAnchor(
        project_id=config.project_id,
        baseline_ref=config.orchestration.baseline_ref,
        baseline_sha=git.resolve_ref(config.orchestration.baseline_ref),
        promote_from_group=resolved_group_id,
        promote_branch=group.branch,
        top_commit_policy=group.lineage.top_commit_policy,
        anchor_metric=anchor_metric,
        commit_sha=selected_sha,
        metric_value=float(anchor.metric_value) if anchor is not None else 0.0,
        source_group_id=anchor.source_group_id if anchor is not None else None,
        trajectory_step=int(anchor.trajectory_step) if anchor is not None else 0,
        workdir=config.workdir,
    )


def _auto_promote_group_id(
    *,
    config: HiAgentResearchConfig,
    registry: Registry,
    git: GitService,
) -> str:
    selected_group_id = ""
    selected_rank = float("-inf")
    for group in config.research_groups:
        if group.task_kind == "merge" and group.role == "final_merge":
            continue
        anchor_metric = group.lineage.anchor_metric
        minimize = config.evaluation.metric_minimizes(anchor_metric)
        anchor = resolve_policy_anchor(
            policy=group.lineage.top_commit_policy,
            parent_group_id=group.id,
            anchor_metric=anchor_metric,
            baseline_ref=config.orchestration.baseline_ref,
            registry=registry,
            git=git,
            minimize=minimize,
        )
        if anchor is None:
            continue
        rank = -anchor.metric_value if minimize else anchor.metric_value
        if rank > selected_rank:
            selected_rank = rank
            selected_group_id = group.id
    if not selected_group_id:
        raise LineageError("could not auto-resolve promote group: no scored policy-selected anchors in registry")
    return selected_group_id
