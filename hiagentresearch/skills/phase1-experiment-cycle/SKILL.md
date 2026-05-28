# Phase1 Experiment Cycle Skill

Use this skill when running a phase-1 research group cycle.

## Goal

Produce a real, hypothesis-driven experiment with planning artifacts before code execution.

## Required sequence

1. Inspect evidence in:
   - the workspace (`workdir`) source you will change
   - the read-only evaluation zone (the `evaluation.entrypoint` directory) so you know exactly how you are scored
2. Write `experiment_intent.json` with:
   - `run_id`, `group_id`, `objective`
   - `hypothesis_id`, `hypothesis`
   - `evidence_refs`, `planned_code_changes`
   - `target_files` (all under the workspace), `success_criteria`, `rollback_plan`
3. Write `experiment_plan.md` with headings:
   - `## Evidence`
   - `## Planned Edit`
   - `## Risk and Rollback`
   - `## Eval Expectations`
4. Apply one bounded edit to a workspace source file.
5. Return a JSON summary of changed files and artifact paths.

## Constraints

- Do not skip planning artifacts.
- Do not make marker-only runs.
- Do not create branch-memory Python files for hypotheses or markers.
- Add project experiment dependencies to the workspace requirements file when needed.
- Keep edits small, reversible, and syntax-safe.
- The workspace is yours to edit and restructure; the evaluation zone is read-only. Read eval code to understand scoring, but never edit or run it.
- For feedback, write and run your own quick CPU-bounded unit/smoke tests; do not launch long training or full eval.
- Metric-producing training/eval is owned by the orchestrator and GitHub eval nodes.
- Treat metric regressions as research evidence, not execution failures.
- Keep retrying through repair, pivot, reset, or continue decisions until configured output expectations are met or the group is explicitly blocked.
- Only choose revert when the current branch state is worse for future research than an auditable rollback.
- Do not add ad-hoc guardrails to force success; improve the canonical contract or fix the root issue.
