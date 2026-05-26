# hiagentresearch (Phase 1)

This directory contains the first implementation of the branch-based research runtime.

## Phase 1 goals

- Set up config-backed GitHub automation for research branches.
- Define research groups and agent contracts from root `config.yaml`.
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
- `../config.yaml` canonical project stitch contract
- `../.hiagentresearch/state/evals.db` local registry read model
- `../.hiagentresearch/runs/` per-run artifacts
- `../.hiagentresearch/dashboard/` optional generated static dashboard bundle

## Quick start (local)

```bash
export CURSOR_API_KEY="cursor_..."
hiagentresearch config validate
hiagentresearch init
hiagentresearch run-group --group-id model_architecture --workdir . --quick
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
- `.hiagentresearch/experiments/<group_id>/<run_id>.json` on research branches

Project-specific context, editable paths, eval commands, artifact requirements, and quality retry expectations are generated from `config.yaml`.

Agent validation commands in `config.yaml` are local feedback tools. The frozen
eval adapter remains the final authority and should emit canonical JSON for the
generic parser.

## Optional dashboard

The dashboard is an isolated Phase 2 module. It reads registry data or downloaded
GitHub artifacts and writes a static bundle without changing the research loop:

```bash
hiagentresearch dashboard build
hiagentresearch dashboard build-from-artifacts --artifact-root dashboard-artifacts
```

`dashboard.enabled` in `config.yaml` controls the optional GitHub Pages workflow;
explicit local build commands remain useful for inspection and testing.
