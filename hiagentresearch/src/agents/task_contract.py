from __future__ import annotations

from dataclasses import dataclass

from hiagentresearch.src.core.models import TaskKind


@dataclass(frozen=True, slots=True)
class TaskContract:
    cycle_instruction: str
    plan_heading: str
    plan_expectation: str


TASK_CONTRACTS: dict[TaskKind, TaskContract] = {
    "metric_experiment": TaskContract(
        cycle_instruction=(
            "Make one bounded, hypothesis-driven change per cycle, grounded in evidence and a written plan. "
            "Treat metric regressions as learning, not execution failure."
        ),
        plan_heading="## Eval Expectations",
        plan_expectation="state how you expect the orchestrator eval metrics to move and why.",
    ),
    "engineering": TaskContract(
        cycle_instruction=(
            "Make one bounded build/refactor change per cycle with full implementation ownership. "
            "Choose a creative but disciplined approach that serves the requested outcome."
        ),
        plan_heading="## Verification",
        plan_expectation="state how you will verify behavior is preserved and watch performance impact.",
    ),
}


def task_contract(task_kind: TaskKind | str) -> TaskContract:
    return TASK_CONTRACTS.get(str(task_kind), TASK_CONTRACTS["metric_experiment"])
