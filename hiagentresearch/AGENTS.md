# HiAgentResearch Agent Contract

This project uses a Cursor-first phase-1 research loop with a thin Python control-plane.

## Core cycle contract

1. Research and planning happen before code edits.
2. Each run writes planning artifacts under `.hiagentresearch/runs/<run_id>/`:
   - `experiment_intent.json`
   - `experiment_plan.md`
3. Each run applies at least one bounded code edit to a configured core implementation file.
4. Each committed experiment includes a concise `.hiagentresearch/experiments/<group_id>/<run_id>.json` manifest.
5. Do not create branch-memory Python files for hypotheses or markers.
6. Every run must finish with eval artifacts and an auditable trail.

## Editing boundaries

- Keep code edits inside configured `allowed_paths` and run-local observability artifacts.
- Prefer minimal changes with explicit hypotheses and rollback plan.
- Do not claim successful research without evaluation outputs.
- If a valid experiment does not improve baseline, record the outcome as evidence and continue through the intent packet (`repair`, `pivot`, `reset`, or `continue`) until output quality matches expectations or the group is explicitly blocked.
- Only revert when the branch state itself is a worse basis for future research; prefer auditable corrective commits over hidden history rewrites.
- Fix boundary problems canonically through config, eval adapters, registry invariants, or operator commands; do not add ad-hoc guardrails that weaken the architecture.

## Evidence expectations

- Cite concrete code or baseline references in planning artifacts.
- Include measurable success criteria tied to evaluation metrics.
