# Phase1 Experiment Cycle Skill

Use this skill when running a phase-1 research group cycle.

## Goal

Produce a real, hypothesis-driven experiment with planning artifacts before code execution.

## Required sequence

1. Inspect evidence in:
   - configured context paths from `config.yaml`
   - target implementation files from the active group
   - latest configured supporting artifacts
2. Write `experiment_intent.json` with:
   - `run_id`, `group_id`, `objective`
   - `hypothesis_id`, `hypothesis`
   - `evidence_refs`, `planned_code_changes`
   - `target_files`, `success_criteria`, `rollback_plan`
3. Write `experiment_plan.md` with headings:
   - `## Evidence`
   - `## Planned Edit`
   - `## Risk and Rollback`
   - `## Eval Expectations`
4. Apply one bounded edit to a core allowed file.
5. Update configured supporting artifacts when applicable.
6. Return a JSON summary of changed files and artifact paths.

## Constraints

- Do not skip planning artifacts.
- Do not make marker-only runs.
- Keep edits small, reversible, and syntax-safe.
- Keep retrying through repair, pivot, reset, or continue decisions until configured output expectations are met or the group is explicitly blocked.
- Do not add ad-hoc guardrails to force success; improve the canonical contract or fix the root issue.
