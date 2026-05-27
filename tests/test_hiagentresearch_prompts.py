from pathlib import Path

from hiagentresearch.src.config import load_config
from hiagentresearch.src.models import IntentPacket
from hiagentresearch.src.prompts import build_phase1_prompt


def test_prompt_is_config_backed() -> None:
    config = load_config(Path("config.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id,
        active_hypothesis_id="h1",
        hypothesis_text="test hypothesis",
        attempt_count=0,
        last_failure_class="none",
        next_action="continue",
        rollback_anchor_sha="",
    )

    prompt = build_phase1_prompt(group=group, intent_packet=packet, run_id="run_abc")

    assert "mnist/pipeline/model.py" in prompt
    assert "mnist/pipeline/research_hypotheses.py" not in prompt
    assert "Do not create branch-memory source files" in prompt
    assert "configured core experiment files" in prompt.lower()
    assert "Optional validation commands" in prompt
    assert "kwta_unit_tests" in prompt
    assert "GitHub final eval remains authoritative" in prompt
    assert "core MNIST" not in prompt
    assert "registry invariants" not in prompt
    assert "Research north star" in prompt
