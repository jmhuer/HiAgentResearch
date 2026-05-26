from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


PolicyMode = Literal["exploit", "explore", "reset"]
FailureClass = Literal["none", "infra_failure", "code_failure", "eval_failure", "invalid_cycle"]
GroupState = Literal[
    "idle",
    "queued_for_wake",
    "running_agent_cycle",
    "awaiting_eval",
    "ingesting_results",
    "ready_for_wake",
    "merge_candidate",
    "blocked",
    "retired",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class EvaluationSpec:
    command: str
    parser: str


@dataclass(slots=True)
class ResearchGroup:
    id: str
    branch: str
    objective: str
    policy_mode: PolicyMode
    allowed_paths: list[str]
    evaluation: EvaluationSpec


@dataclass(slots=True)
class IntentPacket:
    group_id: str
    active_hypothesis_id: str
    hypothesis_text: str
    attempt_count: int
    last_failure_class: FailureClass
    next_action: Literal["repair", "continue", "pivot", "reset"]
    rollback_anchor_sha: str
    key_evidence_refs: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
