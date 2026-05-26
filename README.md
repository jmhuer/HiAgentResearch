# HiAgentResearch

Standalone Cursor-first research runtime with a thin Python control plane.

## Repository layout

- `hiagentresearch/` package source, contracts, docs, and scripts
- `config.yaml` root project contract for workdir, editable paths, eval, groups, artifacts, and policy modes
- `mnist/` first workdir (agent-editable project code)
- `.hiagentresearch/eval/` frozen evaluation entrypoints
- `.hiagentresearch/runs/` per-run observability artifacts
- `.hiagentresearch/state/` registry, group config, and intent packets
- `.github/workflows/` GitHub automation for research branches
- `scripts/` root wrappers that call the package scripts

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
export CURSOR_API_KEY="$(<credentials/cursor_secret.txt)"
python -m hiagentresearch.src.config validate
python -m hiagentresearch.src.orchestrator init
scripts/run_phase1_loops.sh model_architecture research/model-architecture 3
python -m hiagentresearch.src.orchestrator status --group-id model_architecture
```

The runtime is config-first: project-specific paths and quality expectations belong in `config.yaml`, not in core Python prompts or workflows.
