from pathlib import Path

import pytest

from hiagentresearch.src.config import load_config, resolve_group_id_for_branch


def test_load_root_config() -> None:
    config = load_config(Path("config.yaml"))

    assert config.project_id == "mnist"
    assert config.frozen_eval_entrypoint == ".hiagentresearch/eval/run_phase1_eval.py"
    assert ".hiagentresearch/eval/" in config.frozen_paths
    assert "mnist/data/" in config.generated_paths
    assert "metrics.json" in config.artifact_contract.required
    assert config.evaluation.parser == "canonical_json_stdout"
    assert config.agent_tools.validation_commands[0].name == "kwta_unit_tests"
    assert "model_architecture" in config.research_groups_by_id()
    assert "mnist/requirements.txt" in config.editable_paths
    assert config.dependency_files == ["mnist/requirements.txt"]
    assert config.dependency_file_paths(Path(".").resolve())[0].name == "requirements.txt"
    assert "mnist/requirements.txt" in config.group_by_id("model_architecture").allowed_paths
    assert config.agent_contract.supporting_artifacts == []
    assert "mnist/pipeline/research_hypotheses.py" not in config.editable_paths
    group = config.research_groups_by_id()["model_architecture"]
    assert group.validation_commands[0].command.startswith("python -m pytest")


def test_group_resolution_from_branch() -> None:
    config = load_config(Path("config.yaml"))

    assert resolve_group_id_for_branch("research/model-architecture", config) == "model_architecture"
    assert resolve_group_id_for_branch("research/model-architecture/try-1", config) == "model_architecture"
    assert resolve_group_id_for_branch("feature/other", config) == "unknown"


def test_config_rejects_paths_outside_editable_contract(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: .
editable_paths:
  - src/app.py
frozen_eval_entrypoint: .hiagentresearch/eval/run.py
evaluation:
  command_template: "python .hiagentresearch/eval/run.py"
  parser: pytest_exit_code
artifact_contract:
  required: [metrics.json]
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: explore
    allowed_paths:
      - src/app.py
      - secrets.env
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="allowed_paths"):
        load_config(config_path)


def test_config_rejects_dependency_files_outside_editable_contract(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: .
editable_paths:
  - src/app.py
dependency_files:
  - requirements.txt
frozen_eval_entrypoint: .hiagentresearch/eval/run.py
evaluation:
  command_template: "python .hiagentresearch/eval/run.py"
  parser: pytest_exit_code
artifact_contract:
  required: [metrics.json]
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: explore
    allowed_paths:
      - src/app.py
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="dependency_files"):
        load_config(config_path)


def test_config_rejects_frozen_paths_inside_editable_contract(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: .
editable_paths:
  - src/app.py
  - eval/run.py
frozen_eval_entrypoint: eval/run.py
evaluation:
  command_template: "python eval/run.py"
  parser: pytest_exit_code
artifact_contract:
  required: [metrics.json]
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: explore
    allowed_paths:
      - src/app.py
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="frozen paths"):
        load_config(config_path)
