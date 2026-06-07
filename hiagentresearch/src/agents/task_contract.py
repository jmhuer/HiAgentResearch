from __future__ import annotations

from dataclasses import dataclass

from hiagentresearch.src.core.models import TaskKind


# Headings every cycle plan must carry, regardless of task kind.
COMMON_PLAN_HEADINGS: tuple[str, ...] = ("## Evidence", "## Planned Edit", "## Risk and Rollback")


@dataclass(frozen=True, slots=True)
class TaskContract:
    agent_role: str
    intent_noun: str
    cycle_instruction: str
    metric_directive: str
    plan_heading: str
    plan_expectation: str
    preserve_metrics: bool
    detail_intent_label: str
    # The DEFAULT scope heuristic for one cycle, rendered as the prompt's "Scope this cycle"
    # line. Experiments isolate one variable; engineering sizes a cohesive change set to the
    # goal; merge lands one integration step. A group's optional `change_scope` config
    # REPLACES this default outright (see prompts.build_research_cycle_prompt).
    default_scope: str
    # The DEFAULT top_commit_policy for groups of this kind, applied only when the author didn't set
    # one explicitly (config honors an explicit choice). Engineering PRESERVES metrics, so its LATEST
    # commit — not a metric peak — is the top commit and the merge base; experiments and merges chase
    # the metric, so they default to the best commit. This is the single per-kind source for the
    # default (config.py reads it), keeping policy selection out of inline task_kind branches.
    default_top_commit_policy: str

    @property
    def required_headings(self) -> tuple[str, ...]:
        """Full set of plan headings this task kind must produce.

        Single source of truth for both prompt construction and plan validation,
        so adding a task kind never needs a second branch in the orchestrator.
        """
        return (*COMMON_PLAN_HEADINGS, self.plan_heading)


TASK_CONTRACTS: dict[TaskKind, TaskContract] = {
    "metric_experiment": TaskContract(
        agent_role="research agent",
        intent_noun="hypothesis",
        cycle_instruction=(
            "Work like a scientist: make a hypothesis-driven change grounded in evidence and a written "
            "plan, and isolate cause from effect so the result is attributable."
        ),
        metric_directive=(
            "The eval metrics are your objective: pursue improvement. An evidence-backed regression is a "
            "valid finding to learn from, not an execution failure."
        ),
        plan_heading="## Eval Expectations",
        plan_expectation="state how you expect the orchestrator eval metrics to move and why.",
        preserve_metrics=False,
        detail_intent_label="Hypothesis",
        default_scope=(
            "Change one experimental axis this cycle and hold the rest constant. Size it to the "
            "hypothesis — as bold as it genuinely needs (a new block, a new schedule), not a timid "
            "one-liner; never vary two axes at once."
        ),
        default_top_commit_policy="best_commit",
    ),
    "engineering": TaskContract(
        agent_role="engineering agent",
        intent_noun="change goal",
        cycle_instruction=(
            "Work like a staff engineer: take full implementation ownership to improve structure, "
            "clarity, robustness, or maintainability, keeping changes behavior-preserving and reviewable "
            "so any metric move stays attributable (don't fold an unrelated behavior change into a "
            "refactor). This is engineering work, not a metric experiment: do not chase the score or "
            "gamble on changes hoping a number moves."
        ),
        metric_directive=(
            "The eval metrics are a guardrail, not the goal. PRESERVE them: your change must keep every "
            "metric at or above the value you inherited. A regression means your change altered evaluated "
            "behavior — that is a failed cycle to be repaired (revert or fix), not a finding. If the "
            "previous cycle regressed a metric, restoring it is your job this cycle, while keeping the "
            "quality improvement."
        ),
        plan_heading="## Verification",
        plan_expectation="state how you will verify evaluated behavior is preserved (metrics held) and watch performance impact.",
        preserve_metrics=True,
        detail_intent_label="Change goal",
        default_scope=(
            "Size the change to the goal: a bounded refinement most cycles, but a larger restructuring "
            "spanning multiple files is the right call when the goal genuinely needs it. Keep it one "
            "cohesive, reviewable change set, with no unrelated padding."
        ),
        default_top_commit_policy="last_commit",
    ),
    "merge": TaskContract(
        agent_role="merge agent",
        intent_noun="merge goal",
        cycle_instruction=(
            "Work like an integration engineer: combine the best results of several research lineages "
            "into one branch — combine, don't reinvent — keeping each integration behavior-preserving so "
            "any regression is diagnosable. This branch already starts from the strongest lineage; build "
            "on what is already merged. Once every source has been integrated, keep improving the merged "
            "result: reconcile rough edges between the combined changes and strengthen the integrated "
            "behavior. Never end a cycle empty."
        ),
        metric_directive=(
            "Preserve the gains each source brought and combine them: the merged result must hold at or "
            "above the strongest input it started from, and should ideally exceed it as the improvements "
            "compound. Don't chase the score. If an integration regresses, it broke one of those gains: "
            "back THAT integration out and try a different way to combine it, or move on to a different "
            "source — do NOT fall back to the inherited base branch (it is already a selectable result, so "
            "ending where you started adds nothing). Never settle at the base you began from."
        ),
        plan_heading="## Verification",
        plan_expectation="state how you will verify the integrated behavior is preserved (metrics held at or above the base).",
        preserve_metrics=True,
        detail_intent_label="Merge goal",
        default_scope=(
            "Make one integration step per cycle — fold in a single source's compatible improvements, "
            "don't entangle multiple sources in one cycle. Integrate where compatible; where a source's "
            "change conflicts, take the part that composes and leave the rest (a justified selective graft "
            "is correct — not every change from every source has to survive). Once all sources are "
            "integrated, refine the merged result."
        ),
        default_top_commit_policy="best_commit",
    ),
}


def task_contract(task_kind: TaskKind | str) -> TaskContract:
    return TASK_CONTRACTS.get(str(task_kind), TASK_CONTRACTS["metric_experiment"])
