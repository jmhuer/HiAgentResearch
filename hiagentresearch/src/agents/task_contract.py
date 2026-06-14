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
            "Work like a staff engineer: take full implementation ownership and shape the idea into its "
            "best-engineered form — structure, clarity, robustness, maintainability. Whatever you build "
            "must be the thing the frozen eval actually runs: wire it onto the evaluated path and enable "
            "it via code defaults THIS cycle so it is genuinely exercised and tested. Never leave new "
            "functionality built-but-disabled. This is engineering, not a metric experiment: do not aim "
            "to change the core mechanism that drives the score, and do not gamble on changes hoping a "
            "number moves."
        ),
        metric_directive=(
            "The eval metric is a guardrail, not a target. Your enabled change must not regress it below "
            "the value you inherited — a regression is a failed cycle to repair (fix or revert), not a "
            "finding. Positive movement is welcome but not expected; don't chase the score. Building "
            "infrastructure the frozen eval never executes is an incomplete cycle, not a safe one."
        ),
        plan_heading="## Verification",
        plan_expectation="state how you wired the change onto the frozen-eval path (the effective_config it produces) and how you confirmed the metric is preserved.",
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
