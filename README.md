# HiAgentResearch

Standalone Cursor-first research runtime with a thin Python control plane.

## Repository layout

- `hiagentresearch/` package source, contracts, docs, and scripts
- `configs/standard.yaml` default project contract for workdir, editable paths, eval, groups, artifacts, and policy modes
- `mnist/` first workdir (agent-editable project code)
- `.hiagentresearch/eval/` frozen evaluation entrypoints
- `.hiagentresearch/runs/` per-run observability artifacts
- `.hiagentresearch/state/evals.db` local registry read model
- `.hiagentresearch/dashboard/` optional generated static dashboard bundle
- `.github/workflows/` GitHub automation for research branches

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
export CURSOR_API_KEY="$(<credentials/cursor_secret.txt)"
hiagentresearch config validate
hiagentresearch init
hiagentresearch loops --group-id model_architecture --branch research/model-architecture --loops 3 --quick
hiagentresearch status --group-id model_architecture
hiagentresearch registry summary
hiagentresearch dashboard build
```

The runtime is config-first: project-specific paths and quality expectations belong in `configs/*.yaml` (default `configs/standard.yaml`), not in core Python prompts or workflows.
See `hiagentresearch/docs/registry.md` for registry inspection and dashboard commands.
