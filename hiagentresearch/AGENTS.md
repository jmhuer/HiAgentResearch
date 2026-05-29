# HiAgentResearch Agent Contract

This project uses a Cursor-first phase-1 research loop with a thin Python control-plane.

## Core cycle contract

1. Research and planning happen before code edits.
2. Each run writes planning artifacts under `.hiagentresearch/runs/<run_id>/`:
   - `experiment_intent.json`
   - `experiment_plan.md`
3. Each run applies one bounded, real code edit to a workspace source file; keep edits
   small, reversible, and syntactically valid. No marker-only or no-op runs.
4. Each committed experiment includes a concise `.hiagentresearch/experiments/<group_id>/<run_id>.json` manifest.
5. Do not create branch-memory Python files for hypotheses or markers.
6. Every run leaves an auditable trail. The orchestrator runs the eval *after* your edit
   and that result is authoritative; you do not produce metric/eval artifacts yourself.

## Editing boundaries

- The workspace (`workdir`) is yours: edit, add, restructure, and add tests/dependencies freely within it. The generated `AGENTS.md` in the workspace describes the exact eval command and targets.
- Keep edits inside the workspace plus run-local observability artifacts.
- The evaluation zone (the directory containing `evaluation.entrypoint`, e.g. `.hiagentresearch/eval/`) is read-only. Read it to understand exactly how you are scored; never edit or run it. The orchestrator and GitHub eval nodes own metric-producing training/eval, and edits to the eval zone are rejected as an invalid cycle.
- If an experiment needs a project dependency, add it to the workspace requirements file instead of core runtime dependencies.
- For your own feedback, write and run quick CPU-bounded unit/smoke tests; do not launch long training runs.
- Do not claim successful research without orchestrator or GitHub evaluation outputs.
- If a valid experiment does not improve the configured baseline, record the outcome as evidence and continue through the intent packet (`repair`, `pivot`, `reset`, or `continue`) until output quality matches expectations or the group is explicitly blocked.
- Only revert when the branch state itself is a worse basis for future research; prefer auditable corrective commits over hidden history rewrites.
- Fix boundary problems canonically through config, eval adapters, registry invariants, or operator commands; do not add ad-hoc guardrails that weaken the architecture.

## Evidence expectations

- Cite concrete code or metric target references in planning artifacts.
- Include measurable success criteria tied to evaluation metrics.
