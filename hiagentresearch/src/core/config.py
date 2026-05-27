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

from hiagentresearch.src.core.models import AgentValidationCommand, EvaluationSpec, ResearchGroup
from hiagentresearch.src.paths import REPO_ROOT

DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class MetricExpectation(BaseModel):
    min: float | None = None
    max: float | None = None


class EvaluationConfig(BaseModel):
    command_template: str
    parser: str
    success_metrics: dict[str, MetricExpectation] = Field(default_factory=dict)


class AgentValidationCommandConfig(BaseModel):
    name: str
    command: str
    description: str = ""


class AgentToolsConfig(BaseModel):
    validation_commands: list[AgentValidationCommandConfig] = Field(default_factory=list)


class ArtifactContract(BaseModel):
    required: list[str]
    optional: list[str] = Field(default_factory=list)

    @field_validator("required")
    @classmethod
    def required_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("artifact_contract.required must contain at least one artifact")
        return value


class SupportingArtifactConfig(BaseModel):
    path: str
    instruction: str = ""


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
    guidance_files: list[str] = Field(default_factory=list)
    context_paths: list[str] = Field(default_factory=list)
    supporting_artifacts: list[SupportingArtifactConfig] = Field(default_factory=list)
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
    allowed_paths: list[str]
    lineage: LineageConfig = Field(default_factory=LineageConfig)
    evaluation: EvaluationConfig | None = None
    context_paths: list[str] | None = None
    supporting_artifacts: list[SupportingArtifactConfig] | None = None
    research_output_expectations: list[str] | None = None

    @field_validator("allowed_paths")
    @classmethod
    def allowed_paths_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("research group allowed_paths must contain at least one path")
        return value


class HiAgentResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    workdir: str
    editable_paths: list[str]
    dependency_files: list[str] = Field(default_factory=list)
    generated_paths: list[str] = Field(default_factory=list)
    frozen_paths: list[str] = Field(default_factory=list)
    frozen_eval_entrypoint: str
    evaluation: EvaluationConfig
    research_groups: list[ResearchGroupConfig]
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    artifact_contract: ArtifactContract
    policy_modes: dict[str, str]
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    agent_tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)
    agent_contract: AgentContractConfig = Field(default_factory=AgentContractConfig)

    @field_validator("editable_paths")
    @classmethod
    def editable_paths_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("editable_paths must contain at least one path")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> "HiAgentResearchConfig":
        group_ids = [group.id for group in self.research_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("research group ids must be unique")

        editable = set(self.editable_paths)
        dependency_outside = sorted(path for path in self.dependency_files if path not in editable)
        if dependency_outside:
            raise ValueError(f"dependency_files must be listed in editable_paths: {dependency_outside}")
        frozen = set(self.all_frozen_paths())
        frozen_editable = sorted(path for path in editable if _overlaps_any(path, frozen))
        if frozen_editable:
            raise ValueError(f"frozen paths must not be listed in editable_paths: {frozen_editable}")
        for group in self.research_groups:
            if group.policy_mode not in self.policy_modes:
                raise ValueError(f"group {group.id} has unknown policy_mode: {group.policy_mode}")
            outside = sorted(path for path in group.allowed_paths if path not in editable)
            if outside:
                raise ValueError(
                    f"group {group.id} allowed_paths must be listed in editable_paths: {outside}"
                )
            frozen_allowed = sorted(path for path in group.allowed_paths if _overlaps_any(path, frozen))
            if frozen_allowed:
                raise ValueError(f"group {group.id} allowed_paths include frozen paths: {frozen_allowed}")
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

    def frozen_eval_path(self, root: Path = REPO_ROOT) -> Path:
        path = Path(self.frozen_eval_entrypoint)
        return path if path.is_absolute() else (root / path).resolve()

    def all_frozen_paths(self) -> list[str]:
        return [self.frozen_eval_entrypoint, *self.frozen_paths]

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
        eval_config = group.evaluation if group and group.evaluation else self.evaluation
        values = {
            "project_id": self.project_id,
            "workdir": self.workdir,
            "frozen_eval_entrypoint": self.frozen_eval_entrypoint,
            "group_id": group.id if group else "",
            "branch": group.branch if group else "",
        }
        return _safe_format(eval_config.command_template, values)

    def evaluation_for_group(self, group: ResearchGroupConfig) -> EvaluationSpec:
        eval_config = group.evaluation if group.evaluation else self.evaluation
        return EvaluationSpec(
            command=self.format_eval_command(group),
            parser=eval_config.parser,
        )

    def to_research_group(self, group: ResearchGroupConfig) -> ResearchGroup:
        supporting = group.supporting_artifacts or self.agent_contract.supporting_artifacts
        expectations = group.research_output_expectations or self.agent_contract.research_output_expectations
        return ResearchGroup(
            id=group.id,
            branch=group.branch,
            objective=group.objective,
            policy_mode=group.policy_mode,
            allowed_paths=list(group.allowed_paths),
            evaluation=self.evaluation_for_group(group),
            context_paths=list(group.context_paths or self.agent_contract.context_paths),
            supporting_artifacts=[artifact.path for artifact in supporting],
            supporting_artifact_instructions={artifact.path: artifact.instruction for artifact in supporting},
            research_output_expectations=list(expectations),
            validation_commands=[
                AgentValidationCommand(
                    name=tool.name,
                    command=tool.command,
                    description=tool.description,
                )
                for tool in self.agent_tools.validation_commands
            ],
            generated_paths=list(self.generated_paths),
            frozen_paths=list(self.all_frozen_paths()),
        )

    def research_groups_by_id(self) -> dict[str, ResearchGroup]:
        return {group.id: self.to_research_group(group) for group in self.research_groups}


def _safe_format(template: str, values: dict[str, str]) -> str:
    used: dict[str, str] = {}
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            used[field_name] = values.get(field_name, "")
    return template.format(**used)


def _overlaps_any(path: str, candidates: set[str]) -> bool:
    normalized = path.rstrip("/")
    for candidate in candidates:
        candidate_normalized = candidate.rstrip("/")
        if (
            normalized == candidate_normalized
            or normalized.startswith(f"{candidate_normalized}/")
            or candidate_normalized.startswith(f"{normalized}/")
        ):
            return True
    return False


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
                    "groups": [group.id for group in config.research_groups],
                    "dashboard_enabled": config.dashboard.enabled,
                    "agent_validation_tools": [tool.name for tool in config.agent_tools.validation_commands],
                    "required_artifacts": config.artifact_contract.required,
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
