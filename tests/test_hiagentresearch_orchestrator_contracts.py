from pathlib import Path

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.core.models import IntentPacket
from hiagentresearch.src.runtime.orchestrator import (
    _apply_agent_intent_update,
    _metadata_payload,
    _is_generated_path,
    _validate_agent_intent_contract,
    _validate_agent_tool_boundary,
)


def test_agent_intent_contract_rejects_targets_outside_allowed_paths(tmp_path) -> None:
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
  "evidence_refs": ["mnist/pipeline/model.py"],
  "planned_code_changes": ["edit"],
  "target_files": ["mnist/pipeline/model.py", "secrets.env"],
  "success_criteria": ["tests pass"],
  "rollback_plan": "revert"
}
""",
        encoding="utf-8",
    )
    (run_dir / "experiment_plan.md").write_text(
        """
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
""",
        encoding="utf-8",
    )

    valid, error = _validate_agent_intent_contract(run_dir=run_dir, group=group, run_id="run_abc")

    assert valid is False
    assert "target_files outside allowed paths" in error


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
        rollback_anchor_sha="",
    )

    updated = _apply_agent_intent_update(run_dir=run_dir, prior=packet)

    assert updated.active_hypothesis_id == "model_architecture-h10"
    assert updated.hypothesis_text == "Latest agent-authored hypothesis."


def test_generated_paths_match_files_and_directories() -> None:
    generated = ["mnist/data/"]

    assert _is_generated_path("mnist/data/", generated) is True
    assert _is_generated_path("mnist/data/MNIST/raw/file", generated) is True
    assert _is_generated_path("mnist/pipeline/model.py", generated) is False


def test_agent_tool_boundary_rejects_full_training_commands(tmp_path) -> None:
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    (run_dir / "agent_stream.jsonl").write_text(
        """
{"raw":{"type":"tool_call","name":"shell","args":{"command":"python mnist/pipeline/train.py --quick"}}}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    valid, error = _validate_agent_tool_boundary(run_dir=run_dir)

    assert valid is False
    assert "orchestrator-owned training/eval" in error
    assert "mnist/pipeline/train.py" in error


def test_agent_tool_boundary_allows_git_inspection_of_core_paths(tmp_path) -> None:
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    (run_dir / "agent_stream.jsonl").write_text(
        """
{"raw":{"type":"tool_call","name":"shell","args":{"command":"cd /wt && git log --oneline -5 && git diff main -- mnist/pipeline/train.py 2>/dev/null | head -80"}}}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    valid, error = _validate_agent_tool_boundary(run_dir=run_dir)

    assert valid is True
    assert error == ""


def test_agent_tool_boundary_allows_unit_test_tools(tmp_path) -> None:
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    (run_dir / "agent_stream.jsonl").write_text(
        """
{"raw":{"type":"tool_call","name":"shell","args":{"command":"python -m pytest -q mnist/pipeline/test_kwta.py"}}}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    valid, error = _validate_agent_tool_boundary(run_dir=run_dir)

    assert valid is True
    assert error == ""


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
    assert payload["group"]["validation_commands"][0]["name"] == "kwta_unit_tests"
