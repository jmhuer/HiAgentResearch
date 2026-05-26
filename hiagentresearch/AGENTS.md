# HiAgentResearch Agent Contract

This project uses a Cursor-first phase-1 research loop with a thin Python control-plane.

## Core cycle contract

1. Research and planning happen before code edits.
2. Each run writes planning artifacts under `.hiagentresearch/runs/<run_id>/`:
   - `experiment_intent.json`
   - `experiment_plan.md`
3. Each run applies at least one bounded code edit to a core MNIST implementation file.
4. Marker/hypothesis files are supporting artifacts, not a substitute for real experiments.
5. Every run must finish with eval artifacts and an auditable trail.

## Editing boundaries

- Keep code edits inside group `allowed_paths` and run-local observability artifacts.
- Prefer minimal changes with explicit hypotheses and rollback plan.
- Do not claim successful research without evaluation outputs.

## Evidence expectations

- Cite concrete code or baseline references in planning artifacts.
- Include measurable success criteria tied to evaluation metrics.
