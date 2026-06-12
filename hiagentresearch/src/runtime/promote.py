from __future__ import annotations

from dataclasses import asdict, dataclass

from hiagentresearch.src.core.config import HiAgentResearchConfig, load_config
from hiagentresearch.src.git.service import GitService, GitServiceError
from hiagentresearch.src.lineage.promotion import PromotionAnchor, resolve_promotion_anchor
from hiagentresearch.src.paths import REPO_ROOT, resolve_state_dir
from hiagentresearch.src.registry.store import Registry


@dataclass(frozen=True, slots=True)
class PromotionResult:
    anchor: PromotionAnchor
    target_branch: str
    target_start_ref: str
    target_created: bool
    target_sha_before: str
    promoted_sha: str
    committed: bool
    pushed: bool
    dry_run: bool
    diff_stat: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["anchor"] = self.anchor.to_dict()
        return payload


def promote_research_baseline(
    *,
    config: HiAgentResearchConfig | None = None,
    group_id: str = "",
    commit_sha: str = "",
    target_branch: str = "",
    dry_run: bool = False,
    push: bool = False,
    git: GitService | None = None,
    registry: Registry | None = None,
) -> PromotionResult:
    loaded = config or load_config()
    git_service = git or GitService(REPO_ROOT)
    registry_store = registry or Registry(resolve_state_dir())
    registry_store.init()

    anchor = resolve_promotion_anchor(
        config=loaded,
        registry=registry_store,
        git=git_service,
        group_id=group_id,
        commit_sha=commit_sha,
    )
    if not git_service.commit_exists(anchor.commit_sha):
        raise GitServiceError(f"promotion commit not found locally: {anchor.commit_sha}")

    resolved_target = target_branch.strip() or anchor.baseline_ref
    target_exists = git_service.branch_exists(resolved_target)
    target_start_ref = resolved_target if target_exists else anchor.baseline_ref
    target_sha_before = (
        git_service.resolve_ref(resolved_target) if target_exists else git_service.resolve_ref(anchor.baseline_ref)
    )
    diff_stat = git_service.diff_stat(target_start_ref, anchor.commit_sha, anchor.workdir)

    if dry_run:
        return PromotionResult(
            anchor=anchor,
            target_branch=resolved_target,
            target_start_ref=target_start_ref,
            target_created=not target_exists,
            target_sha_before=target_sha_before,
            promoted_sha=target_sha_before,
            committed=False,
            pushed=False,
            dry_run=True,
            diff_stat=diff_stat,
        )

    if git_service.has_changed_files_under(anchor.workdir) or git_service.has_changed_files_under(
        anchor.workdir, staged=True
    ):
        raise GitServiceError(f"{anchor.workdir}/ has uncommitted changes; commit or stash before promoting")

    git_service.checkout_or_create(resolved_target, start_ref=anchor.baseline_ref)
    git_service.restore_path_from_ref(anchor.commit_sha, anchor.workdir)
    git_service.stage_path(anchor.workdir)

    committed = False
    promoted_sha = git_service.head_sha()
    if git_service.has_changed_files_under(anchor.workdir, staged=True):
        promoted_sha = git_service.commit(
            subject=f"Promote {anchor.promote_from_group} policy-selected product tree onto {resolved_target}.",
            body=(
                f"Source: {anchor.commit_sha} "
                f"({anchor.anchor_metric}={anchor.metric_value}, policy={anchor.top_commit_policy})\n"
                f"Baseline: {anchor.baseline_ref} ({anchor.baseline_sha})"
            ),
        )
        committed = True

    pushed = False
    if push:
        git_service.push(remote=loaded.github.remote, branch=resolved_target)
        pushed = True

    return PromotionResult(
        anchor=anchor,
        target_branch=resolved_target,
        target_start_ref=target_start_ref,
        target_created=not target_exists,
        target_sha_before=target_sha_before,
        promoted_sha=promoted_sha,
        committed=committed,
        pushed=pushed,
        dry_run=False,
        diff_stat=diff_stat,
    )
