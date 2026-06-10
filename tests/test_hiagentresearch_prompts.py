from pathlib import Path

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.core.models import IntentPacket, ScoreContext
from hiagentresearch.src.agents.prompts import build_research_cycle_prompt
from hiagentresearch.src.agents.task_contract import COMMON_PLAN_HEADINGS, task_contract


def test_required_headings_derive_from_task_contract() -> None:
    """Plan validation and prompt construction share one source: the contract.
    Each kind's required headings are the common set plus its own plan heading."""
    for kind in ("metric_experiment", "engineering"):
        contract = task_contract(kind)
        assert contract.required_headings == (*COMMON_PLAN_HEADINGS, contract.plan_heading)
    assert task_contract("engineering").plan_heading == "## Verification"
    assert task_contract("metric_experiment").plan_heading == "## Eval Expectations"


def test_prompt_is_config_backed() -> None:
    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id,
        active_goal_id="h1",
        goal_text="test goal",
        attempt_count=0,
        last_failure_class="none",
        next_action="continue",
    )

    prompt = build_research_cycle_prompt(group=group, intent_packet=packet, run_id="run_abc")

    assert "workspace is `mnist/`" in prompt
    assert "Do not create branch-memory source files" in prompt
    assert "evaluation zone is read-only" in prompt.lower()
    assert ".hiagentresearch/eval/" in prompt
    assert "mnist/AGENTS.md" in prompt
    assert ".hiagentresearch/AGENTS.md" in prompt
    assert group.evaluation.command not in prompt
    assert "owned by the GitHub eval node" in prompt
    # The orchestrator is the sole committer: the agent must leave edits uncommitted and
    # never move HEAD (a self-commit makes the working tree look empty to the edit-boundary).
    assert "git commit" in prompt
    assert "UNCOMMITTED" in prompt
    assert "git diff" in prompt  # read-only git is still allowed (needed for merges)
    # The prompt gives the agent an explicit completion signal so the run finishes cleanly,
    # and tells it to self-review (read its own diff) + smoke-check before stopping.
    assert "stop and" in prompt
    assert "re-read your own diff" in prompt
    assert "Don't keep exploring once the change is reviewed and complete" in prompt
    # The policy mode is sent with its meaning, not just the bare label.
    assert config.policy_modes[group.policy_mode] in prompt
    assert "## Eval Expectations" in prompt
    # No MNIST-internal source paths are hardcoded into the prompt anymore.
    assert "mnist/src/model.py" not in prompt
    assert "mnist/pipeline" not in prompt


def test_default_scope_differs_per_kind() -> None:
    """Experiments isolate one variable; engineering sizes a cohesive set to the goal;
    merge is an integration step. The scope line is driven by the contract default."""
    assert "one experimental axis" in task_contract("metric_experiment").default_scope
    assert "restructuring spanning multiple files" in task_contract("engineering").default_scope
    assert "integration step" in task_contract("merge").default_scope


def test_change_scope_override_replaces_default() -> None:
    """When a group sets change_scope, it REPLACES the kind's default scope outright —
    there is only ever one scope statement, so nothing can contradict it."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, ResearchGroupConfig

    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"exploit": "Exploit."},
        research_groups=[
            ResearchGroupConfig(id="polish", branch="research/polish", policy_mode="exploit",
                                task_kind="engineering",
                                change_scope="Re-architecture is in scope — restructure across modules freely.",
                                objective="Refactor.", lineage=LineageConfig(mode="baseline")),
        ],
    )
    group = config.research_groups_by_id()["polish"]
    packet = IntentPacket(
        group_id="polish", active_goal_id="c1", goal_text="refactor",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    prompt = build_research_cycle_prompt(group=group, intent_packet=packet, run_id="run_e")
    assert "Scope this cycle: Re-architecture is in scope" in prompt
    # The engineering default scope is replaced, not appended.
    assert "Size the change to the goal" not in prompt
    # Role grounding lives in the (stable) cycle instruction, so it SURVIVES a scope
    # override — the user only respecifies breadth, never loses "work like a staff engineer".
    assert "staff engineer" in prompt


def test_change_scope_absent_uses_kind_default() -> None:
    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id, active_goal_id="h1", goal_text="x",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    prompt = build_research_cycle_prompt(group=group, intent_packet=packet, run_id="run_g")
    # Scope is pure breadth (isolate one variable); no file-count dial.
    assert "Scope this cycle: Change one experimental axis" in prompt
    assert "Risk dial" not in prompt
    assert "file(s)" not in prompt
    # Role grounding lives in the stable cycle instruction, not in the overridable scope.
    assert "Work like a scientist" in prompt
    assert "Scope this cycle: Work like" not in prompt


def test_merge_prompt_keeps_improving_when_sources_exhausted() -> None:
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, ResearchGroupConfig
    from hiagentresearch.src.lineage.resolve import BranchBootstrap

    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"exploit": "Exploit."},
        research_groups=[
            ResearchGroupConfig(id="merge_best", branch="research/merge-best", policy_mode="exploit",
                                task_kind="merge", lineage=LineageConfig(mode="inherit")),
        ],
    )
    group = config.research_groups_by_id()["merge_best"]
    packet = IntentPacket(
        group_id="merge_best", active_goal_id="m1", goal_text="combine",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    bootstrap = BranchBootstrap(
        branch="research/merge-best", mode="inherit", start_ref="basesha1234567",
        parent_group_id="polish_code", anchor_metric="accuracy",
        merge_sources=(
            {"group_id": "data_augmentation", "branch": "research/data-augmentation",
             "commit_sha": "datasha7654321", "metric_value": 0.92},
        ),
    )
    prompt = build_research_cycle_prompt(
        group=group, intent_packet=packet, run_id="run_merge", lineage_bootstrap=bootstrap,
    )
    # The merge never returns empty: after integrating sources it keeps refining.
    assert "do NOT return empty" in prompt
    assert "keep improving the merged result" in prompt


def test_engineering_prompt_uses_verification_heading() -> None:
    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["polish_code"]
    packet = IntentPacket(
        group_id=group.id,
        active_goal_id="h1",
        goal_text="refactor and improve structure",
        attempt_count=0,
        last_failure_class="none",
        next_action="continue",
    )
    prompt = build_research_cycle_prompt(group=group, intent_packet=packet, run_id="run_eng")
    assert group.task_kind == "engineering"
    assert "## Verification" in prompt
    assert "implementation ownership" in prompt.lower()
    # Engineering is framed as engineering, not a metric cycle, and is told to
    # PRESERVE metrics (a regression is a failed cycle), unlike metric_experiment.
    assert "engineering agent" in prompt
    assert "change goal" in prompt
    assert "preserve" in prompt.lower()
    assert "guardrail" in prompt.lower()


def test_metric_experiment_prompt_frames_regressions_as_findings() -> None:
    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id,
        active_goal_id="h1",
        goal_text="try a change",
        attempt_count=0,
        last_failure_class="none",
        next_action="continue",
    )
    prompt = build_research_cycle_prompt(group=group, intent_packet=packet, run_id="run_me")
    assert "research agent" in prompt
    assert "your objective" in prompt.lower()
    assert "valid finding" in prompt.lower()


def test_prompt_grounds_agent_with_score_context() -> None:
    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id, active_goal_id="h1", goal_text="try a change",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    score = ScoreContext(
        metric_name="accuracy", minimize=False, baseline_value=0.972,
        trajectory=((1, 0.974), (2, 0.978)), inherited_floor=0.975,
        attempt_index=2, total_attempts=3,
    )
    prompt = build_research_cycle_prompt(
        group=group, intent_packet=packet, run_id="run_g", score_context=score,
    )
    # The metric, its direction, the baseline, the floor, the trajectory, and the
    # best-so-far are all surfaced so the agent optimizes a real gradient.
    assert "Scoreboard for `accuracy`" in prompt
    assert "higher is better" in prompt
    assert "Baseline (L0): 0.972" in prompt
    assert "Inherited floor" in prompt and "0.975" in prompt
    assert "loop 1: 0.974" in prompt and "loop 2: 0.978" in prompt
    assert "Best so far in this group: 0.978" in prompt
    assert "attempt 2 of 3" in prompt
    # Never hands the agent the unreachable absolute target as its bar.
    assert "0.985" not in prompt


def test_grounding_is_role_aware_metric_is_guardrail_for_engineering() -> None:
    """For engineering (preserve_metrics), the scoreboard frames the metric as a guardrail
    to HOLD — never 'beat it' — and surfaces persist-but-vary once there is history."""
    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["polish_code"]  # engineering
    packet = IntentPacket(
        group_id=group.id, active_goal_id="c1", goal_text="refactor",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    score = ScoreContext(
        metric_name="accuracy", minimize=False, baseline_value=0.97,
        trajectory=((1, 0.974), (2, 0.973)), inherited_floor=0.974,
        attempt_index=2, total_attempts=3,
    )
    prompt = build_research_cycle_prompt(
        group=group, intent_packet=packet, run_id="run_eng", score_context=score,
    )
    assert "guardrail, not a number to chase" in prompt
    assert "Best held so far" in prompt
    assert "your job is to beat it" not in prompt  # engineering never chases the score
    # Persist-but-vary appears once there is committed history.
    assert "don't resubmit one that already scored" in prompt


def test_prompt_grounding_final_attempt_says_consolidate() -> None:
    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id, active_goal_id="h1", goal_text="x",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    score = ScoreContext(
        metric_name="accuracy", minimize=False, baseline_value=0.97,
        trajectory=(), inherited_floor=None, attempt_index=3, total_attempts=3,
    )
    prompt = build_research_cycle_prompt(
        group=group, intent_packet=packet, run_id="run_g", score_context=score,
    )
    assert "attempt 3 of 3" in prompt
    assert "Final attempt" in prompt
    assert "No committed scores yet" in prompt


def test_prompt_omits_grounding_when_absent() -> None:
    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id, active_goal_id="h1", goal_text="x",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    prompt = build_research_cycle_prompt(group=group, intent_packet=packet, run_id="run_g")
    assert "Scoreboard for" not in prompt


def test_merge_contract_preserves_metrics() -> None:
    contract = task_contract("merge")
    assert contract.preserve_metrics is True
    assert contract.detail_intent_label == "Merge goal"
    assert contract.agent_role == "merge agent"


def test_merge_prompt_lists_ranked_sources_with_git_guidance() -> None:
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, ResearchGroupConfig
    from hiagentresearch.src.lineage.resolve import BranchBootstrap

    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"exploit": "Exploit."},
        research_groups=[
            ResearchGroupConfig(id="merge_best", branch="research/merge-best", policy_mode="exploit",
                                task_kind="merge", lineage=LineageConfig(mode="inherit")),
        ],
    )
    group = config.research_groups_by_id()["merge_best"]
    assert group.task_kind == "merge"
    assert "Combine the strongest" in group.objective  # auto objective
    packet = IntentPacket(
        group_id="merge_best", active_goal_id="m1", goal_text="combine the lineages",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    bootstrap = BranchBootstrap(
        branch="research/merge-best", mode="inherit", start_ref="basesha1234567",
        parent_group_id="polish_code", anchor_metric="accuracy",
        merge_sources=(
            {"group_id": "data_augmentation", "branch": "research/data-augmentation",
             "commit_sha": "datasha7654321", "metric_value": 0.92},
        ),
    )
    prompt = build_research_cycle_prompt(
        group=group, intent_packet=packet, run_id="run_merge", lineage_bootstrap=bootstrap,
    )
    assert "merge agent" in prompt
    assert "Merge:" in prompt
    assert "priority order" in prompt
    assert "data_augmentation" in prompt
    assert "git diff HEAD" in prompt
