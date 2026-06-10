# hiagentresearch (Phase 1)

This directory contains the first implementation of the branch-based research runtime.

## Phase 1 goals

- Set up config-backed GitHub automation for research branches.
- Define research groups and agent contracts from `configs/standard.yaml` (or another config file).
- Run one research-group cycle with strong observability outputs.
- Keep orchestration thin and deterministic.

## Scope boundaries

- Merge orchestration is phase 2.
- Plugin packaging is phase 3.
- The Python control plane is intentionally thin:
  - state transitions,
  - registry writes,
  - eval lifecycle integration,
  - intent packet persistence.

## Layout

- `src/` runtime modules and CLIs
- `docs/` design contracts
- `skills/` Cursor-first skill contracts
- `../configs/standard.yaml` canonical default project stitch contract
- `../.hiagentresearch/state/evals.db` local registry read model
- `../.hiagentresearch/runs/` per-run artifacts
- `../.hiagentresearch/dashboard/` optional generated static dashboard bundle

## Quick start (local)

```bash
export CURSOR_API_KEY="cursor_..."
hiagentresearch config validate
hiagentresearch init
hiagentresearch run-group --group-id model_architecture --workdir .
hiagentresearch status --group-id model_architecture
```

`run-group` always uses the Cursor SDK agent backend. `CURSOR_API_KEY` is required.

```bash
hiagentresearch run-group \
  --group-id model_architecture \
  --workdir . \
  --agent-model composer-2.5
```

The run command writes visibility artifacts under:

- `.hiagentresearch/runs/<run_id>/`
- `.hiagentresearch/state/evals.db`
- `.hiagentresearch/cycles/<group_id>/<run_id>.json` on research branches

The agent-owned workspace (`workdir`), read-only eval zone (derived from `evaluation.entrypoint`),
eval command, targets, and quality retry expectations come from the active config file (`configs/standard.yaml` by default). The workspace
`AGENTS.md` is generated from it.

The single framework guidance document (read before each cycle) is materialized by
`hiagentresearch init` at the stable project-facing path:

- `.hiagentresearch/AGENTS.md` — control-plane rules and the per-cycle contract

The prompt also prepends `<workdir>/AGENTS.md` automatically (the project-scoped eval
command and targets). To change framework behavior for a fork, edit the runtime source
contract at `hiagentresearch/AGENTS.md`, then run `hiagentresearch init` in the project;
do not add framework guidance paths to the config file.

Agents get fast feedback from their own quick tests in the workspace. The frozen
eval adapter under `.hiagentresearch/eval/` remains the final authority and emits
canonical JSON (health flags plus the metric keys named in `evaluation.targets`).

## Optional dashboard

The dashboard is an isolated Phase 2 module. It reads registry data or downloaded
GitHub artifacts and writes a static bundle without changing the research loop:

```bash
hiagentresearch dashboard build
hiagentresearch dashboard build-from-artifacts --artifact-root dashboard-artifacts
```

`dashboard.enabled` in the active config controls the optional GitHub Pages workflow;
explicit local build commands remain useful for inspection and testing.
