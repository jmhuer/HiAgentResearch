from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from string import Formatter
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from hiagentresearch.src.core.artifacts import FRAMEWORK_ARTIFACT_CONTRACT_VERSION
from hiagentresearch.src.core.guidance import default_guidance_files
from hiagentresearch.src.core.models import EvaluationSpec, ResearchGroup
from hiagentresearch.src.core.pathspec import is_within
from hiagentresearch.src.paths import DEFAULT_CONFIG_PATH, REPO_ROOT


class MetricExpectation(BaseModel):
    min: float | None = None
    max: float | None = None


class EvaluationConfig(BaseModel):
    entrypoint: str
    command_template: str
    targets: dict[str, MetricExpectation] = Field(default_factory=dict)

    def metric_minimizes(self, metric_name: str) -> bool:
        """Whether lower values are better for a metric, derived from its target.

        Direction is generic and config-driven — no metric names are hardcoded. A
        target that sets only ``max`` (e.g. ``latency_ms: {max: 13}``) means "stay
        under the cap", so lower is better → minimize. Anything else — a ``min``
        bound (e.g. ``accuracy: {min: 0.985}``), a min+max range, or no target —
        defaults to higher-is-better → maximize.
        """
        expectation = self.targets.get(metric_name)
        return bool(expectation and expectation.max is not None and expectation.min is None)


class GitHubConfig(BaseModel):
    workflow_name: str = "hiagentresearch-research-eval"
    remote: str = "origin"
    run_lookup_attempts: int = 30
    run_lookup_sleep_sec: float = 3.0


class DashboardConfig(BaseModel):
    enabled: bool = False
    title: str = "HiAgentResearch"
    # Metrics to chart. Empty means "use the project's evaluation targets", resolved
    # at config load — no project-specific metric names are baked in.
    metrics: list[str] = Field(default_factory=list)
    # Metrics rendered as discrete/step lines (e.g. counts) rather than smooth curves.
    discrete_metrics: list[str] = Field(default_factory=list)
    output_dir: str = ".hiagentresearch/dashboard"


class AgentContractConfig(BaseModel):
    research_output_expectations: list[str] = Field(default_factory=list)


class LineageConfig(BaseModel):
    mode: Literal["baseline", "inherit", "force"] = "baseline"
    inherit_from: str | None = None
    # Which parent-trajectory commit this group branches FROM (inherit mode).
    inherit_policy: Literal["best_commit", "last_commit"] = "best_commit"
    # Which of THIS group's own commits is the starred top commit.
    top_commit_policy: Literal["best_commit", "last_commit"] = "best_commit"
    # Metric the lineage ranks commits by. Empty means "use the project's primary
    # metric", resolved at config load — so the dashboard is not hardcoded to any
    # particular metric name.
    anchor_metric: str = ""
    # Other lineages a merge group folds in (group ids). Optional manual override: a
    # merge group leaves this empty and the orchestrator auto-resolves it (and the base)
    # from the ranked lineage winners after the source lineages finish.
    draw_from: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_lineage(self) -> "LineageConfig":
        # Note: the "inherit needs inherit_from" rule lives on ResearchGroupConfig,
        # because a merge group inherits with an auto-resolved base (no explicit parent).
        # draw_from is no longer coupled to mode: the area desugar uses it to wire a
        # collapse/merge group's sources structurally, independent of the lineage mode.
        return self


class OrchestrationConfig(BaseModel):
    baseline_ref: str = "main"
    # Research group whose top_commit_policy-selected commit is promoted by `hiagentresearch promote`.
    # Empty → auto-pick the strongest scored policy-selected anchor across configured groups.
    promote_from_group: str = ""
    # Namespace for auto-derived research branches. Existing flat configs can still
    # set explicit group.branch values; this only affects desugared area branches.
    branch_prefix: str = "research"
    execution_waves: list[list[str]] | None = None
    execution_order: list[str] | None = None
    max_parallel_groups: int = 2
    worktree_root: str = ".hiagentresearch/worktrees"


class AgentConfig(BaseModel):
    """Cursor agent execution settings, applied to every research group."""

    model: str = "composer-2.5"
    # Reasoning effort passed to the model (Cursor ModelSelection "thinking" param).
    # Empty means the model default; "high" is the strongest documented mode.
    thinking: str = "high"
    startup_attempts: int = 2
    startup_retry_backoff_sec: float = 2.0
    unary_timeout_sec: float = 1800.0
    stream_timeout_sec: float = 1800.0

    model_config = ConfigDict(extra="forbid")


class ResearchGroupConfig(BaseModel):
    id: str
    # Optional in the hierarchical (area) shape — auto-derived from
    # orchestration.branch_prefix at load time. Flat configs still set it explicitly.
    branch: str = ""
    # Optional: a merge group's objective is auto-generated at resolution; normal
    # research groups state their own.
    objective: str = ""
    policy_mode: str
    task_kind: Literal["metric_experiment", "engineering", "merge"] = "metric_experiment"
    lineage: LineageConfig = Field(default_factory=LineageConfig)
    # Optional natural-language scope override (prompt-only, not enforced). When set, it
    # REPLACES the task kind's default scope heuristic for this group — describe how bold or
    # deep one cycle's change should be (e.g. "polish only, no structural change" or
    # "re-architecture is in scope"). None = use the task kind's default scope.
    change_scope: str | None = None

    # --- Hierarchical "area" surface (user-facing) ---
    # Competing single-idea leaves for this area. When non-empty, the area fans out into
    # one leaf group per approach plus a collapse node (see the load-time desugar).
    approaches: list[str] = Field(default_factory=list)
    # How the area collapses its leaves: True (default) → merge collapse (integrate the
    # leaves, best_commit floor); False → select collapse (adopt the strongest leaf, no
    # integration loops). Ignored when the area has no approaches.
    combine: bool = True

    # --- Internal fields (desugar-set, not hand-authored) ---
    # The single approach a leaf group carries; seeds the leaf's first intent packet.
    seed_approach: str = ""
    # Per-group loop budget override. None → use the run's --loops; 0 → no agent cycles
    # (a select collapse just adopts the strongest leaf via branch creation).
    loops: int | None = None
    # The owning area id and this group's role within it — the dashboard tabs read these.
    area: str = ""
    role: Literal["", "leaf", "collapse", "final_merge"] = ""

    @model_validator(mode="after")
    def validate_group(self) -> "ResearchGroupConfig":
        # A merge group inherits from an auto-resolved base (the strongest lineage), so
        # it needs no explicit inherit_from. Every other inherit group does.
        if (
            self.lineage.mode == "inherit"
            and self.task_kind != "merge"
            and not self.lineage.inherit_from
        ):
            raise ValueError(f"group {self.id}: lineage.inherit_from is required when mode is inherit")
        # Apply the per-kind DEFAULT top_commit_policy from the task contract (the single source of
        # per-kind behavior) when the author didn't set one explicitly — so policy selection isn't an
        # inline task_kind branch here. Engineering's contract defaults to `last_commit` (its latest
        # commit carries the metric-neutral refactor, so "best commit" would discard it and a merge
        # would revert past it); experiments and merges default to `best_commit`.
        if "top_commit_policy" not in self.lineage.model_fields_set:
            from hiagentresearch.src.agents.task_contract import task_contract

            self.lineage.top_commit_policy = task_contract(self.task_kind).default_top_commit_policy
            self.lineage.model_fields_set.add("top_commit_policy")
        return self


class HiAgentResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    workdir: str
    dependency_files: list[str] = Field(default_factory=list)
    generated_paths: list[str] = Field(default_factory=list)
    hidden_paths: list[str] = Field(default_factory=list)
    editable_paths: list[str] = Field(default_factory=list)
    evaluation: EvaluationConfig
    research_groups: list[ResearchGroupConfig]
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    # Optional override of the auto final merge: the area ids whose collapsed results are
    # combined at the end. Empty → auto-merge every terminal area (one no other inherits).
    combine_areas: list[str] = Field(default_factory=list)
    # Top-commit policy for the auto-generated final merge. Defaults to best_commit (a
    # guardrail: a regressing integration step is not adopted). Set last_commit to keep the
    # fully-integrated latest commit regardless of score. Per-area collapses take this from
    # the area's own lineage.top_commit_policy.
    final_merge_top_commit_policy: Literal["best_commit", "last_commit"] = "best_commit"
    policy_modes: dict[str, str]
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    agent_contract: AgentContractConfig = Field(default_factory=AgentContractConfig)

    @model_validator(mode="after")
    def desugar_areas(self) -> "HiAgentResearchConfig":
        """Expand the hierarchical "area" shape into the flat leaf + collapse + merge
        groups the engine already runs, and auto-derive execution waves + the final merge.

        Activates only when at least one group declares ``approaches`` — a flat config
        (no approaches) passes through untouched, so today's behavior is verbatim. Runs
        before :meth:`validate_references`, so all downstream validation and anchor-metric
        resolution see the already-expanded groups. The generated merge/collapse groups
        wire their sources via ``lineage.draw_from``, so ``lineage/resolve.py`` is reused
        as-is (a collapse merges its area's leaves; the final merge combines area results)."""
        if not any(group.approaches for group in self.research_groups):
            return self

        areas = list(self.research_groups)
        by_id = {a.id: a for a in areas}
        for area in areas:
            parent = area.lineage.inherit_from
            if area.lineage.mode == "inherit" and parent and parent not in by_id:
                raise ValueError(f"area {area.id}: inherit_from references unknown area: {parent}")

        def result_node(area: ResearchGroupConfig) -> str:
            """The addressable node a downstream area inherits from: an area's collapse
            node when it fans out (>=2 approaches), else the area itself (a single lineage —
            0 or 1 approach, no collapse). Keeps "K approaches -> K-1 merges" exact."""
            return f"{area.id}__collapse" if len(area.approaches) >= 2 else area.id

        def level(area_id: str, seen: tuple[str, ...] = ()) -> int:
            if area_id in seen:
                raise ValueError(f"inherit cycle through area: {area_id}")
            area = by_id[area_id]
            parent = area.lineage.inherit_from
            if area.lineage.mode != "inherit" or not parent:
                return 0
            return 1 + level(parent, (*seen, area_id))

        def inherited_lineage(area: ResearchGroupConfig) -> LineageConfig:
            """Clone an area's lineage for its leaves, rewriting an area-level inherit_from
            to the upstream area's result node (collapse or itself)."""
            base = area.lineage
            inherit_from = base.inherit_from
            if base.mode == "inherit" and inherit_from:
                inherit_from = result_node(by_id[inherit_from])
            return LineageConfig(
                mode=base.mode,
                inherit_from=inherit_from,
                inherit_policy=base.inherit_policy,
                top_commit_policy=base.top_commit_policy,
                anchor_metric=base.anchor_metric,
            )

        expanded: list[ResearchGroupConfig] = []
        # level -> ("leaf" wave ids, "collapse" wave ids), preserving area order within a level.
        leaf_waves: dict[int, list[str]] = {}
        collapse_waves: dict[int, list[str]] = {}
        branch_prefix = self.orchestration.branch_prefix.strip("/") or "research"

        def research_branch(suffix: str) -> str:
            return f"{branch_prefix}/{suffix}"

        for area in areas:
            lvl = level(area.id)
            if len(area.approaches) <= 1:
                # 0 or 1 approach = a single lineage that IS the area (no collapse — there
                # is nothing to merge). K=0 and K=1 converge here, differing only in whether
                # the leaf carries a seed approach. This is also the no-fan-out path verbatim.
                expanded.append(
                    area.model_copy(
                        update={
                            "branch": area.branch or research_branch(area.id),
                            "lineage": inherited_lineage(area),
                            "approaches": [],
                            "seed_approach": area.approaches[0] if area.approaches else "",
                            "area": area.id,
                            "role": "leaf",
                        }
                    )
                )
                leaf_waves.setdefault(lvl, []).append(area.id)
                continue

            leaf_ids: list[str] = []
            for index, approach in enumerate(area.approaches, start=1):
                leaf_id = f"{area.id}__a{index}"
                leaf_ids.append(leaf_id)
                expanded.append(
                    ResearchGroupConfig(
                        id=leaf_id,
                        branch=research_branch(f"{area.id}-a{index}"),
                        objective=area.objective,
                        policy_mode=area.policy_mode,
                        task_kind=area.task_kind,
                        lineage=inherited_lineage(area),
                        change_scope=area.change_scope,
                        seed_approach=approach,
                        area=area.id,
                        role="leaf",
                    )
                )
            # Collapse node: a merge over the area's leaves. combine:false (select) runs
            # zero integration loops and just adopts the strongest leaf (branch created at
            # its commit); combine:true integrates the leaves over the run's loop budget.
            expanded.append(
                ResearchGroupConfig(
                    id=f"{area.id}__collapse",
                    branch=research_branch(f"{area.id}-collapse"),
                    policy_mode=area.policy_mode,
                    task_kind="merge",
                    lineage=LineageConfig(
                        mode="inherit",
                        draw_from=list(leaf_ids),
                        anchor_metric=area.lineage.anchor_metric,
                        # The area's keep-policy governs its collapsed result too (default
                        # best_commit; last_commit keeps the integrated result over the score).
                        top_commit_policy=area.lineage.top_commit_policy,
                    ),
                    loops=None if area.combine else 0,
                    area=area.id,
                    role="collapse",
                )
            )
            leaf_waves.setdefault(lvl, []).extend(leaf_ids)
            collapse_waves.setdefault(lvl, []).append(f"{area.id}__collapse")

        # Final merge across terminal areas (those no other area inherits from), unless
        # combine_areas: overrides which areas to combine. A single terminal area is
        # already the final result — no merge node.
        inherited_area_ids = {
            a.lineage.inherit_from
            for a in areas
            if a.lineage.mode == "inherit" and a.lineage.inherit_from
        }
        if self.combine_areas:
            final_area_ids = list(self.combine_areas)
            for aid in final_area_ids:
                if aid not in by_id:
                    raise ValueError(f"combine_areas references unknown area: {aid}")
        else:
            final_area_ids = [a.id for a in areas if a.id not in inherited_area_ids]
        final_participants = [result_node(by_id[aid]) for aid in final_area_ids]

        waves: list[list[str]] = []
        for lvl in sorted(set(leaf_waves) | set(collapse_waves)):
            if leaf_waves.get(lvl):
                waves.append(leaf_waves[lvl])
            if collapse_waves.get(lvl):
                waves.append(collapse_waves[lvl])

        if len(final_participants) >= 2:
            primary_metric = next(iter(self.evaluation.targets), "")
            expanded.append(
                ResearchGroupConfig(
                    id="final_merge",
                    branch=research_branch("final-merge"),
                    policy_mode=areas[0].policy_mode,
                    task_kind="merge",
                    lineage=LineageConfig(
                        mode="inherit",
                        draw_from=list(final_participants),
                        anchor_metric=primary_metric,
                        top_commit_policy=self.final_merge_top_commit_policy,
                    ),
                    area="final_merge",
                    role="final_merge",
                )
            )
            waves.append(["final_merge"])

        self.research_groups = expanded
        self.orchestration.execution_waves = waves
        self.orchestration.execution_order = None
        return self

    @model_validator(mode="after")
    def assign_area_metadata(self) -> "HiAgentResearchConfig":
        """Every group carries area/role even in a flat (non-fan-out) config, so the
        dashboard shows one tab per research group — a flat group is just a single-leaf
        area. Fan-out groups already have area/role from the desugar; this fills the rest:
        a merge group is a final_merge tab, everything else a leaf."""
        for group in self.research_groups:
            if not group.area:
                group.area = group.id
                group.role = "final_merge" if group.task_kind == "merge" else "leaf"
        return self

    @model_validator(mode="after")
    def validate_references(self) -> "HiAgentResearchConfig":
        group_ids = [group.id for group in self.research_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("research group ids must be unique")

        # Charted metrics default to the evaluation targets, so a project only has to
        # declare its targets once and the dashboard follows.
        if not self.dashboard.metrics:
            self.dashboard.metrics = list(self.evaluation.targets)

        # Resolve each group's lineage anchor metric to the project's primary metric
        # when not explicitly set, so lineage ranking is driven by config rather than
        # a hardcoded metric name. Primary = first configured dashboard metric, else
        # the first evaluation target.
        primary_metric = (
            self.dashboard.metrics[0]
            if self.dashboard.metrics
            else next(iter(self.evaluation.targets), "")
            or "accuracy"  # last-resort default only when no metrics are configured at all
        )
        for group in self.research_groups:
            if not group.lineage.anchor_metric:
                group.lineage.anchor_metric = primary_metric

        workdir = self.workdir.rstrip("/")
        if workdir not in ("", "."):
            dependency_outside = sorted(
                path for path in self.dependency_files if not is_within(path, workdir)
            )
            if dependency_outside:
                raise ValueError(
                    f"dependency_files must live inside workdir ({self.workdir}): {dependency_outside}"
                )
            entrypoint = self.evaluation.entrypoint.rstrip("/")
            if is_within(entrypoint, workdir):
                raise ValueError(
                    f"evaluation.entrypoint must live outside workdir ({self.workdir}) so agents cannot own it: "
                    f"{self.evaluation.entrypoint}"
                )
        for group in self.research_groups:
            if group.policy_mode not in self.policy_modes:
                raise ValueError(f"group {group.id} has unknown policy_mode: {group.policy_mode}")
            # inherit_from is optional for a merge group (base is auto-resolved); when set
            # (here or on any inherit group) it must reference a known group.
            if group.lineage.inherit_from and group.lineage.inherit_from not in group_ids:
                raise ValueError(
                    f"group {group.id} lineage.inherit_from references unknown group: {group.lineage.inherit_from}"
                )
            for source in group.lineage.draw_from:
                if source not in group_ids:
                    raise ValueError(
                        f"group {group.id} lineage.draw_from references unknown group: {source}"
                    )
        self._validate_orchestration_order(group_ids)
        return self

    def _validate_orchestration_order(self, group_ids: list[str]) -> None:
        waves = self.execution_waves()
        position: dict[str, int] = {}
        for wave_index, wave in enumerate(waves):
            for group_id in wave:
                if group_id not in group_ids:
                    raise ValueError(f"orchestration references unknown group_id: {group_id}")
                position[group_id] = wave_index
        for group in self.research_groups:
            explicit_sources = [s for s in [group.lineage.inherit_from, *group.lineage.draw_from] if s]
            # A merge group with NO explicit sources auto-discovers every other lineage
            # tip, so it must run after ALL non-merge groups (the legacy final-merge case).
            # A merge with explicit sources (an area collapse, or the desugared final merge)
            # only needs to run after THOSE sources, so it may be non-terminal.
            if group.task_kind == "merge" and not explicit_sources and group.id in position:
                later_than = [
                    other.id
                    for other in self.research_groups
                    if other.task_kind != "merge"
                    and other.id in position
                    and position[other.id] >= position[group.id]
                ]
                if later_than:
                    raise ValueError(
                        f"merge group {group.id} must run in a wave after every other group; "
                        f"these are not earlier: {later_than}"
                    )
                continue
            # Explicit inherit / draw_from parents (or merge sources) must run before this group.
            for parent in explicit_sources:
                if parent not in position or group.id not in position:
                    continue
                if position[parent] >= position[group.id]:
                    raise ValueError(
                        f"group {group.id} must run after parent {parent} in orchestration waves/order"
                    )

    def execution_waves(self) -> list[list[str]]:
        if self.orchestration.execution_waves:
            return self.orchestration.execution_waves
        if self.orchestration.execution_order:
            return [[group_id] for group_id in self.orchestration.execution_order]
        return [[group.id] for group in self.research_groups]

    def workdir_path(self, root: Path = REPO_ROOT) -> Path:
        path = Path(self.workdir)
        return path if path.is_absolute() else (root / path).resolve()

    def eval_entrypoint_path(self, root: Path = REPO_ROOT) -> Path:
        path = Path(self.evaluation.entrypoint)
        return path if path.is_absolute() else (root / path).resolve()

    def all_reference_paths(self) -> list[str]:
        """Read-only eval zone: entrypoint file plus its parent directory."""
        entrypoint = self.evaluation.entrypoint.replace("\\", "/").rstrip("/")
        parent = str(Path(entrypoint).parent).replace("\\", "/")
        paths: list[str] = []
        if parent and parent not in (".", ""):
            paths.append(parent.rstrip("/") + "/")
        paths.append(entrypoint)
        seen: list[str] = []
        for path in paths:
            if path not in seen:
                seen.append(path)
        return seen

    def generated_paths_resolved(self) -> list[str]:
        workdir = self.workdir.rstrip("/")
        resolved: list[str] = []
        for path in self.generated_paths:
            cleaned = path.lstrip("/")
            if workdir in ("", "."):
                resolved.append(cleaned)
            else:
                resolved.append(f"{workdir}/{cleaned}")
        return resolved

    def commit_excluded_paths(self) -> list[str]:
        """Directory prefixes that must never be staged or committed on research branches."""
        excluded = [
            *self.generated_paths_resolved(),
            *self.all_reference_paths(),
            *self.hidden_paths,
        ]
        seen: list[str] = []
        for path in excluded:
            normalized = path.replace("\\", "/").rstrip("/")
            if normalized and normalized not in seen:
                seen.append(normalized)
        return seen

    def workspace_agents_path(self) -> str:
        workdir = self.workdir.rstrip("/")
        return "AGENTS.md" if workdir in ("", ".") else f"{workdir}/AGENTS.md"

    def dependency_file_paths(self, root: Path = REPO_ROOT) -> list[Path]:
        paths: list[Path] = []
        for dependency_file in self.dependency_files:
            path = Path(dependency_file)
            paths.append(path if path.is_absolute() else (root / path).resolve())
        return paths

    def dashboard_output_path(self, root: Path = REPO_ROOT) -> Path:
        path = Path(self.dashboard.output_dir)
        return path if path.is_absolute() else (root / path).resolve()

    def group_by_id(self, group_id: str) -> ResearchGroupConfig:
        for group in self.research_groups:
            if group.id == group_id:
                return group
        raise KeyError(f"unknown group_id: {group_id}")

    def group_for_branch(self, branch: str) -> ResearchGroupConfig | None:
        for group in self.research_groups:
            if branch == group.branch or branch.startswith(f"{group.branch}/"):
                return group
        return None

    def format_eval_command(self, group: ResearchGroupConfig | None = None) -> str:
        values = {
            "project_id": self.project_id,
            "workdir": self.workdir,
            "entrypoint": self.evaluation.entrypoint,
            "group_id": group.id if group else "",
            "branch": group.branch if group else "",
        }
        return _safe_format(self.evaluation.command_template, values)

    def evaluation_for_group(self, group: ResearchGroupConfig) -> EvaluationSpec:
        return EvaluationSpec(command=self.format_eval_command(group))

    def to_research_group(self, group: ResearchGroupConfig) -> ResearchGroup:
        objective = group.objective
        if not objective and group.task_kind == "merge":
            objective = (
                "Combine the strongest commits across lineages: start from the best result "
                "and integrate the others in order of strength, preserving the metrics."
            )
        return ResearchGroup(
            id=group.id,
            branch=group.branch,
            objective=objective,
            policy_mode=group.policy_mode,
            task_kind=group.task_kind,
            policy_mode_description=self.policy_modes.get(group.policy_mode, ""),
            evaluation=self.evaluation_for_group(group),
            workdir=self.workdir,
            reference_paths=self.all_reference_paths(),
            generated_paths=self.generated_paths_resolved(),
            hidden_paths=list(self.hidden_paths),
            editable_paths=list(self.editable_paths),
            research_output_expectations=list(self.agent_contract.research_output_expectations),
            guidance_files=list(default_guidance_files()),
            workspace_agents_path=self.workspace_agents_path(),
            change_scope=group.change_scope,
            seed_approach=group.seed_approach,
            area=group.area,
            role=group.role,
        )

    def research_groups_by_id(self) -> dict[str, ResearchGroup]:
        return {group.id: self.to_research_group(group) for group in self.research_groups}


def _safe_format(template: str, values: dict[str, str]) -> str:
    used: dict[str, str] = {}
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            used[field_name] = values.get(field_name, "")
    return template.format(**used)


def load_config(path: Path | None = None) -> HiAgentResearchConfig:
    config_path = path or Path(os.environ.get("HIAGENTRESEARCH_CONFIG", str(DEFAULT_CONFIG_PATH)))
    config_path = config_path.resolve()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"config must be a mapping: {config_path}")
    return HiAgentResearchConfig.model_validate(payload)


def resolve_group_id_for_branch(branch: str, config: HiAgentResearchConfig | None = None) -> str:
    loaded = config or load_config()
    group = loaded.group_for_branch(branch)
    return group.id if group else "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HiAgentResearch config helpers.")
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="Validate config and print a compact summary.")
    resolve = sub.add_parser("resolve-group", help="Resolve a research group id from a branch name.")
    resolve.add_argument("--branch", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError, ValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1

    if args.cmd == "validate":
        print(
            json.dumps(
                {
                    "ok": True,
                    "project_id": config.project_id,
                    "workdir": config.workdir,
                    "groups": [group.id for group in config.research_groups],
                    "dashboard_enabled": config.dashboard.enabled,
                    "eval_entrypoint": config.evaluation.entrypoint,
                    "framework_artifact_contract_version": FRAMEWORK_ARTIFACT_CONTRACT_VERSION,
                },
                indent=2,
            )
        )
        return 0
    if args.cmd == "resolve-group":
        print(resolve_group_id_for_branch(args.branch, config))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
