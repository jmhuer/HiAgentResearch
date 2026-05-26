from pathlib import Path

import pytest

from hiagentresearch.src.config import load_config, resolve_group_id_for_branch


def test_load_root_config() -> None:
    config = load_config(Path("config.yaml"))

    assert config.project_id == "mnist"
    assert config.frozen_eval_entrypoint == ".hiagentresearch/eval/run_phase1_eval.py"
    assert "mnist/data/" in config.generated_paths
    assert "metrics.json" in config.artifact_contract.required
    assert "model_architecture" in config.research_groups_by_id()
    assert config.agent_contract.supporting_artifacts == []
    assert "mnist/pipeline/research_hypotheses.py" not in config.editable_paths


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
