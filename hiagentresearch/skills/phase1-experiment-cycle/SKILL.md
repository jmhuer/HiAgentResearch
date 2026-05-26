# Phase1 Experiment Cycle Skill

Use this skill when running a phase-1 research group cycle.

## Goal

Produce a real, hypothesis-driven experiment with planning artifacts before code execution.

## Required sequence

1. Inspect evidence in:
   - `mnist/baseline.json`
   - target implementation files (for example `mnist/pipeline/model.py`)
   - latest `mnist/pipeline/research_hypotheses.py`
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
5. Update `mnist/pipeline/research_hypotheses.py` with the new hypothesis entry.
6. Update `mnist/pipeline/research_markers.py` with one marker entry.
7. Return a JSON summary of changed files and artifact paths.

## Constraints

- Do not skip planning artifacts.
- Do not make marker-only runs.
- Keep edits small, reversible, and syntax-safe.
