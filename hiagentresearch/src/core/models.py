from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


# Policy stance handed to the agent's prompt. "reset" was removed (2026-06): it was never
# selected by any group nor wired to behavior — the same dormant-knob smell as the removed
# pivot machinery. Fan-out already provides "start over" structurally (a fresh leaf), and the
# best_commit floor guards against a spent idea. If loop-thrashing ever needs handling, the
# honest fix is an ORCHESTRATOR anti-thrash policy (N loops below the inherited floor → roll
# the branch back to the floor commit and reseed), not an agent-driven reset (the agent must
# not move HEAD). Tracked as a deferred item.
PolicyMode = Literal["exploit", "explore"]
# Task kinds, in the project's vocabulary: "metric_experiment" == a Research task
# (try a hypothesis, experiment) and "engineering" == a Build task (implementation
# ownership). Prompt/validation differences live entirely in TASK_CONTRACTS; the
# execution engine is task-kind agnostic.
TaskKind = Literal["metric_experiment", "engineering", "merge"]
FailureClass = Literal["none", "infra_failure", "code_failure", "eval_failure", "invalid_cycle"]
GroupState = Literal[
    "idle",
    "running_agent_cycle",
    "ready_for_wake",
    "blocked",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class EvaluationSpec:
    command: str


@dataclass(slots=True)
class ResearchGroup:
    id: str
    branch: str
    objective: str
    policy_mode: PolicyMode | str
    evaluation: EvaluationSpec
    task_kind: TaskKind = "metric_experiment"
    workdir: str = "."
    policy_mode_description: str = ""
    reference_paths: list[str] = field(default_factory=list)
    generated_paths: list[str] = field(default_factory=list)
    hidden_paths: list[str] = field(default_factory=list)
    editable_paths: list[str] = field(default_factory=list)
    research_output_expectations: list[str] = field(default_factory=list)
    guidance_files: list[str] = field(default_factory=list)
    workspace_agents_path: str = ""
    # Optional natural-language scope override; replaces the task kind's default scope
    # heuristic in the prompt when set. None = use the task kind's default.
    change_scope: str | None = None
    # The single idea a fan-out leaf carries (seeds its first intent packet); empty for
    # non-fan-out groups. ``area``/``role`` tag the group's place in the hierarchy so the
    # dashboard can group leaves + collapse under one tab.
    seed_approach: str = ""
    area: str = ""
    role: str = ""


@dataclass(slots=True)
class IntentPacket:
    group_id: str
    active_goal_id: str
    goal_text: str
    attempt_count: int
    last_failure_class: FailureClass
    next_action: Literal["repair", "continue"]
    # Short, actionable feedback for the next cycle's agent (e.g. a metric regression
    # to restore). Surfaced in the prompt; empty when there is nothing to flag.
    last_note: str = ""
    # Relative path to the preserved CI eval bundle directory (runs/<id>/ci/), flat
    # eval-node artifacts per framework contract. Empty until a CI-backed cycle completes.
    last_feedback_ref: str = ""
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScoreContext:
    """Numeric grounding handed to the agent's prompt so it optimizes against a real
    gradient instead of an unreachable absolute target.

    All values are direction-aware via ``minimize`` (derived from the metric's target):
    the agent is told whether higher or lower is better. ``trajectory`` is THIS group's
    own committed (``gh_*``) scores in loop order — the line it is moving along.
    ``inherited_floor`` is the metric at the inherited/merge base commit (the best_commit
    floor it must not regress below); ``None`` for a fresh baseline group. ``attempt_index``
    / ``total_attempts`` are this cycle's "attempt X of N" so a final attempt can consolidate.
    """

    metric_name: str
    minimize: bool
    baseline_value: float | None
    trajectory: tuple[tuple[int, float], ...]
    inherited_floor: float | None
    attempt_index: int
    total_attempts: int


@dataclass(slots=True)
class TransitionEvent:
    run_id: str
    group_id: str
    from_state: GroupState
    to_state: GroupState
    reason: str
    actor: str
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
