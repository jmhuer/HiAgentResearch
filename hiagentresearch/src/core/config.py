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


class RetryPolicyConfig(BaseModel):
    max_repair_attempts: int = 1
    max_loops_without_quality_output: int = 3


class GitHubConfig(BaseModel):
    workflow_name: str = "hiagentresearch-research-eval"
    remote: str = "origin"
    run_lookup_attempts: int = 30
    run_lookup_sleep_sec: float = 3.0


class DashboardConfig(BaseModel):
    enabled: bool = False
    title: str = "HiAgentResearch"
    metrics: list[str] = Field(default_factory=lambda: ["accuracy", "latency_ms"])
    output_dir: str = ".hiagentresearch/dashboard"


class AgentContractConfig(BaseModel):
    research_output_expectations: list[str] = Field(default_factory=list)
    retry_policy: RetryPolicyConfig = Field(default_factory=RetryPolicyConfig)


class LineageConfig(BaseModel):
    mode: Literal["baseline", "inherit", "force"] = "baseline"
    inherit_from: str | None = None
    anchor_policy: Literal["last_commit", "best_commit"] = "last_commit"
    anchor_metric: str = "accuracy"

    @model_validator(mode="after")
    def validate_inherit(self) -> "LineageConfig":
        if self.mode == "inherit" and not self.inherit_from:
            raise ValueError("lineage.inherit_from is required when mode is inherit")
        return self


class OrchestrationConfig(BaseModel):
    baseline_ref: str = "main"
    execution_waves: list[list[str]] | None = None
    execution_order: list[str] | None = None
    max_parallel_groups: int = 2
    worktree_root: str = ".hiagentresearch/worktrees"


class ResearchGroupConfig(BaseModel):
    id: str
    branch: str
    objective: str
    policy_mode: str
    lineage: LineageConfig = Field(default_factory=LineageConfig)


class HiAgentResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    workdir: str
    dependency_files: list[str] = Field(default_factory=list)
    generated_paths: list[str] = Field(default_factory=list)
    hidden_paths: list[str] = Field(default_factory=list)
    evaluation: EvaluationConfig
    research_groups: list[ResearchGroupConfig]
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    policy_modes: dict[str, str]
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    agent_contract: AgentContractConfig = Field(default_factory=AgentContractConfig)

    @model_validator(mode="after")
    def validate_references(self) -> "HiAgentResearchConfig":
        group_ids = [group.id for group in self.research_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("research group ids must be unique")

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
            if group.lineage.mode == "inherit" and group.lineage.inherit_from not in group_ids:
                raise ValueError(
                    f"group {group.id} lineage.inherit_from references unknown group: {group.lineage.inherit_from}"
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
            if group.lineage.mode != "inherit" or not group.lineage.inherit_from:
                continue
            parent = group.lineage.inherit_from
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
        return ResearchGroup(
            id=group.id,
            branch=group.branch,
            objective=group.objective,
            policy_mode=group.policy_mode,
            policy_mode_description=self.policy_modes.get(group.policy_mode, ""),
            evaluation=self.evaluation_for_group(group),
            workdir=self.workdir,
            reference_paths=self.all_reference_paths(),
            generated_paths=self.generated_paths_resolved(),
            hidden_paths=list(self.hidden_paths),
            research_output_expectations=list(self.agent_contract.research_output_expectations),
            guidance_files=list(default_guidance_files()),
            workspace_agents_path=self.workspace_agents_path(),
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
