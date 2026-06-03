from pathlib import Path

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.core.models import IntentPacket
from hiagentresearch.src.core.pathspec import is_within
from hiagentresearch.src.runtime.orchestrator import (
    _apply_agent_intent_update,
    _metadata_payload,
    _is_generated_path,
    _validate_agent_intent_contract,
)


def _plan_md() -> str:
    return """
## Evidence
test
## Planned Edit
test
## Risk and Rollback
test
## Eval Expectations
test
This is intentionally long enough to satisfy the planning text length requirement.
It contains enough concrete text to model an agent-written pre-code plan artifact.
"""


def _engineering_plan_md() -> str:
    return """
## Evidence
test
## Planned Edit
test
## Risk and Rollback
test
## Verification
test
This is intentionally long enough to satisfy the planning text length requirement.
It contains enough concrete text to model an agent-written pre-code plan artifact.
"""


def test_agent_intent_contract_rejects_targets_outside_workspace(tmp_path) -> None:
    config = load_config(Path("config.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    (run_dir / "experiment_intent.json").write_text(
        """
{
  "run_id": "run_abc",
  "group_id": "model_architecture",
  "objective": "test",
  "hypothesis_id": "h1",
  "hypothesis": "test",
  "evidence_refs": ["mnist/src/model.py"],
  "planned_code_changes": ["edit"],
  "target_files": ["mnist/src/model.py", "secrets.env"],
  "success_criteria": ["tests pass"],
  "rollback_plan": "revert"
}
""",
        encoding="utf-8",
    )
    (run_dir / "experiment_plan.md").write_text(_plan_md(), encoding="utf-8")

    valid, error = _validate_agent_intent_contract(run_dir=run_dir, group=group, run_id="run_abc")

    assert valid is False
    assert "workspace source files" in error


def test_agent_intent_contract_rejects_targets_in_reference_zone(tmp_path) -> None:
    config = load_config(Path("config.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    run_dir = tmp_path / "run_ref"
    run_dir.mkdir()
    (run_dir / "experiment_intent.json").write_text(
        """
{
  "run_id": "run_ref",
  "group_id": "model_architecture",
  "objective": "test",
  "hypothesis_id": "h1",
  "hypothesis": "test",
  "evidence_refs": ["mnist/src/model.py"],
  "planned_code_changes": ["edit"],
  "target_files": [".hiagentresearch/eval/score.py"],
  "success_criteria": ["tests pass"],
  "rollback_plan": "revert"
}
""",
        encoding="utf-8",
    )
    (run_dir / "experiment_plan.md").write_text(_plan_md(), encoding="utf-8")

    valid, error = _validate_agent_intent_contract(run_dir=run_dir, group=group, run_id="run_ref")

    assert valid is False
    assert "workspace source files" in error


def test_agent_intent_contract_accepts_workspace_targets(tmp_path) -> None:
    config = load_config(Path("config.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    run_dir = tmp_path / "run_ok"
    run_dir.mkdir()
    (run_dir / "experiment_intent.json").write_text(
        """
{
  "run_id": "run_ok",
  "group_id": "model_architecture",
  "objective": "test",
  "hypothesis_id": "h1",
  "hypothesis": "test",
  "evidence_refs": ["mnist/src/model.py"],
  "planned_code_changes": ["edit"],
  "target_files": ["mnist/src/model.py"],
  "success_criteria": ["accuracy improves"],
  "rollback_plan": "revert"
}
""",
        encoding="utf-8",
    )
    (run_dir / "experiment_plan.md").write_text(_plan_md(), encoding="utf-8")

    valid, error = _validate_agent_intent_contract(run_dir=run_dir, group=group, run_id="run_ok")

    assert valid is True
    assert error == ""


def test_agent_intent_contract_accepts_engineering_plan_heading(tmp_path) -> None:
    config = load_config(Path("config.yaml"))
    group = config.research_groups_by_id()["polish_code"]
    run_dir = tmp_path / "run_eng"
    run_dir.mkdir()
    (run_dir / "experiment_intent.json").write_text(
        """
{
  "run_id": "run_eng",
  "group_id": "polish_code",
  "objective": "test",
  "hypothesis_id": "h1",
  "hypothesis": "test",
  "evidence_refs": ["mnist/src/model.py"],
  "planned_code_changes": ["edit"],
  "target_files": ["mnist/src/model.py"],
  "success_criteria": ["behavior preserved"],
  "rollback_plan": "revert"
}
""",
        encoding="utf-8",
    )
    (run_dir / "experiment_plan.md").write_text(_engineering_plan_md(), encoding="utf-8")

    valid, error = _validate_agent_intent_contract(run_dir=run_dir, group=group, run_id="run_eng")
    assert valid is True
    assert error == ""


def test_agent_intent_update_preserves_latest_hypothesis(tmp_path) -> None:
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    (run_dir / "experiment_intent.json").write_text(
        """
{
  "hypothesis_id": "model_architecture-h10",
  "hypothesis": "Latest agent-authored hypothesis."
}
""",
        encoding="utf-8",
    )
    packet = IntentPacket(
        group_id="model_architecture",
        active_hypothesis_id="model_architecture-h9",
        hypothesis_text="previous",
        attempt_count=1,
        last_failure_class="eval_failure",
        next_action="pivot",
    )

    updated = _apply_agent_intent_update(run_dir=run_dir, prior=packet)

    assert updated.active_hypothesis_id == "model_architecture-h10"
    assert updated.hypothesis_text == "Latest agent-authored hypothesis."


def test_generated_paths_match_files_and_directories() -> None:
    generated = ["mnist/data/"]

    assert _is_generated_path("mnist/data/", generated) is True
    assert _is_generated_path("mnist/data/MNIST/raw/file", generated) is True
    assert _is_generated_path("mnist/src/model.py", generated) is False


def test_is_within_workdir() -> None:
    assert is_within("mnist/src/model.py", "mnist") is True
    assert is_within("mnist", "mnist") is True
    assert is_within(".hiagentresearch/eval/score.py", "mnist") is False
    assert is_within("anything", ".") is True


def test_metadata_payload_redacts_eval_command() -> None:
    config = load_config(Path("config.yaml"))
    group = config.research_groups_by_id()["model_architecture"]

    payload = _metadata_payload(
        run_id="run_abc",
        group=group,
        status="finished",
        failure_class="none",
        correlation_id="run_abc",
    )

    assert "evaluation" not in payload["group"]
    assert "command" not in payload["group"]
    assert payload["group"]["workdir"] == "mnist"
    assert ".hiagentresearch/eval/" in payload["group"]["reference_paths"]
