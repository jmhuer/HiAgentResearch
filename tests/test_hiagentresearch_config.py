from pathlib import Path

import pytest

from hiagentresearch.src.core.config import AgentContractConfig, HiAgentResearchConfig, load_config, resolve_group_id_for_branch
from hiagentresearch.src.core.guidance import DEFAULT_GUIDANCE_FILES


def test_load_root_config() -> None:
    config = load_config(Path("config.yaml"))

    assert config.project_id == "mnist"
    assert config.workdir == "mnist"
    assert config.evaluation.entrypoint == ".hiagentresearch/eval/run_phase1_eval.py"
    assert config.all_reference_paths() == [
        ".hiagentresearch/eval/",
        ".hiagentresearch/eval/run_phase1_eval.py",
    ]
    assert "reference_paths" not in HiAgentResearchConfig.model_fields
    assert "mnist/data/" in config.generated_paths_resolved()
    assert "mnist/src/checkpoints/" in config.generated_paths_resolved()
    assert "artifact_contract" not in HiAgentResearchConfig.model_fields
    assert config.dashboard.enabled is True
    assert config.dashboard.metrics == ["accuracy", "latency_ms"]
    command = config.format_eval_command(config.group_by_id("model_architecture"))
    assert "--group-id model_architecture" in command
    assert "--workdir mnist" in command
    assert "model_architecture" in config.research_groups_by_id()
    assert config.dependency_files == ["mnist/requirements.txt"]
    assert config.dependency_file_paths(Path(".").resolve())[0].name == "requirements.txt"
    assert config.workspace_agents_path() == "mnist/AGENTS.md"

    group = config.research_groups_by_id()["model_architecture"]
    assert group.workdir == "mnist"
    assert group.evaluation.command == command
    assert ".hiagentresearch/eval/" in group.reference_paths
    assert "mnist/data/" in group.generated_paths
    assert group.workspace_agents_path == "mnist/AGENTS.md"
    assert group.guidance_files == list(DEFAULT_GUIDANCE_FILES)
    assert "guidance_files" not in AgentContractConfig.model_fields


def test_group_resolution_from_branch() -> None:
    config = load_config(Path("config.yaml"))

    assert resolve_group_id_for_branch("research/model-architecture", config) == "model_architecture"
    assert resolve_group_id_for_branch("research/model-architecture/try-1", config) == "model_architecture"
    assert resolve_group_id_for_branch("feature/other", config) == "unknown"


def test_config_rejects_dependency_files_outside_workdir(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: app
dependency_files:
  - requirements.txt
evaluation:
  entrypoint: .hiagentresearch/eval/run.py
  command_template: "python {entrypoint} --workdir {workdir}"
  targets:
    f1:
      min: 0.9
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: explore
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="dependency_files"):
        load_config(config_path)


def test_config_rejects_eval_entrypoint_inside_workdir(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: app
evaluation:
  entrypoint: app/eval/run.py
  command_template: "python {entrypoint} --workdir {workdir}"
  targets:
    f1:
      min: 0.9
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: explore
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="entrypoint must live outside"):
        load_config(config_path)


def test_config_rejects_unknown_policy_mode(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: app
evaluation:
  entrypoint: .hiagentresearch/eval/run.py
  command_template: "python {entrypoint} --workdir {workdir}"
  targets:
    f1:
      min: 0.9
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: exploit
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="policy_mode"):
        load_config(config_path)
