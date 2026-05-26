# HiAgentResearch

Standalone Cursor-first research runtime with a thin Python control plane.

## Repository layout

- `hiagentresearch/` package source, contracts, docs, and scripts
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
python -m hiagentresearch.src.orchestrator init
scripts/run_three_loops.sh model_architecture research/model-architecture 3
```
