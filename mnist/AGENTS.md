# HiAgentControl MNIST — architecture invariants

## Pattern 3 (default)

1. **Python** seeds `state/current/plan.json` skeleton.
2. **Single `/ulw-loop`** with **Sisyphus** enriches `plan.json` using skill **`hac-assess-plan`** (bound in `oh-my-openagent.jsonc`).
3. **Skill owns the loop** — research, lint, gate, hard exit; no duplicate bash commands in the ulw prompt.
4. **Review committee (Python)** — `<promise>DONE</promise>` from `run_plan_gate` stdout only.

### Loop contract

- **Success signal:** gate stdout contains exact `<promise>DONE</promise>`; agent emits only that tag and stops.
- **No dynamic agent sequencing** — do not run Hephaestus→Sisyphus pipelines or `team_task_create` chains during pattern 3.
- **Continuation:** HiAgentControl may kill the OMO session after a stable gate PASS; the agent must not declare done without gate stdout.

### Delegation

- **Default:** `explore` / `librarian` with `run_in_background=false`.
- **Opt-in background:** max 2 concurrent; must `background_output` before next tool.
- **Forbidden:** Sisyphus-Junior, hephaestus, implementation subagents during plan loop.

### Artifacts

- **Required:** `state/current/plan.json`
- **Optional:** `state/current/draft.md` (scratch only)

## Legacy pipeline (`--pattern legacy`)

PI → Atlas → format → gate. See `skills/_legacy/hac-plan-pipeline/`.

## Task shape in plan.json

- **task** — research area title
- **scope** — TRY:/FILES:/CHANGE:/VERIFY: (≥120 chars)
- **goal_type** — survey, codebase_recon, experiment, architecture, hygiene, feature, ablation_study

## Must not

- No phantom paths (e.g. `mnist_cnn.py`); model code is `pipeline/model.py`.
- Do not use Atlas as the pattern-3 primary agent (use Sisyphus).
- Do not declare loop done without gate printing `<promise>DONE</promise>`.

## Rework

On gate failure, read `state/current/targeted_rework.md` and fix `plan.json`.
