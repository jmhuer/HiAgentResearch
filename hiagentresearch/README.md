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
- `../.hiagentresearch/state/` research-group definitions and registry state
- `../.hiagentresearch/runs/` per-run artifacts
- `workflows/` reusable workflow templates

## Quick start (local)

```bash
export CURSOR_API_KEY="cursor_..."
python -m hiagentresearch.src.config validate
python -m hiagentresearch.src.orchestrator init
python -m hiagentresearch.src.orchestrator run-group --group-id model_architecture --workdir . --quick
python -m hiagentresearch.src.orchestrator status --group-id model_architecture
```

`run-group` always uses the Cursor SDK agent backend. `CURSOR_API_KEY` is required.

```bash
python -m hiagentresearch.src.orchestrator run-group \
  --group-id model_architecture \
  --workdir . \
  --agent-model composer-2.5
```

The run command writes visibility artifacts under:

- `.hiagentresearch/runs/<run_id>/`
- `.hiagentresearch/state/intent_packets/<group_id>.json`
- `.hiagentresearch/state/events.jsonl`

Project-specific context, editable paths, eval commands, artifact requirements, and quality retry expectations are generated from `config.yaml`.
