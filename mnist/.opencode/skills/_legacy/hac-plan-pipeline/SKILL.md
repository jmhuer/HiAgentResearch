---
name: hac-plan-pipeline
description: Linear research-plan pipeline PI → Atlas → format → Python gate until DONE.
---

# Phases (strict order; do not skip)

## Phase 1 — PI (Prometheus)
- Write/update `.omo/plans/<name>.md` only.
- Per task area: title, scope (what to try), goal_type, where workers should look (literature / code / web).
- FORBIDDEN: task(), call_omo_agent(), background agents, plan.json, draft.md edits.

## Phase 2 — Postdoc (Atlas)
- Read latest `.omo/plans/*.md`.
- Delegate explore / librarian / sisyphus-junior per goal_type.
- Merge into `state/current/draft.md`; validate worker depth; fix content mistakes.
- FORBIDDEN: write plan.json; do not fix gate failures by editing JSON.

## Phase 3 — Formatter
- Format `state/current/draft.md` into `state/current/plan.json` using skill `hac-format-plan-json`.
- Prefer formatting directly in the current session (single `write` of plan.json).
- Optional delegation is allowed only if needed; output contract is unchanged.

## Phase 4 — Review committee (deterministic)
- Run: `python -m hiagentcontrol.tools.run_plan_gate --workdir . --num-tasks N` (use N from the user prompt).
- If stdout contains exact `<promise>DONE</promise>`: emit that string only and stop the loop.
- Else: read `state/current/targeted_rework.md` and restart at `rework_phase` (pi | atlas | format).

# Loop
- Bound to /ulw-loop; respect loop_max_iterations in oh-my-openagent.jsonc.
