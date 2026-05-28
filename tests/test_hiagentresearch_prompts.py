from pathlib import Path

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.core.models import IntentPacket
from hiagentresearch.src.agents.prompts import build_phase1_prompt


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

    assert "workspace is `mnist/`" in prompt
    assert "Do not create branch-memory source files" in prompt
    assert "read-only evaluation zone" in prompt.lower()
    assert ".hiagentresearch/eval/" in prompt
    assert "mnist/AGENTS.md" in prompt
    assert "hiagentresearch/AGENTS.md" in prompt
    assert "hiagentresearch/skills/phase1-experiment-cycle/SKILL.md" in prompt
    assert group.evaluation.command not in prompt
    assert "orchestrator and GitHub eval nodes" in prompt
    assert "Research north star" in prompt
    # No MNIST-internal source paths are hardcoded into the prompt anymore.
    assert "mnist/src/model.py" not in prompt
    assert "mnist/pipeline" not in prompt
