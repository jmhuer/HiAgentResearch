#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GROUP_ID="${1:-model_architecture}"
WORKDIR="${2:-$ROOT}"
EVIDENCE_JSON="${HAC_EVIDENCE_JSON:-$ROOT/.hiagentresearch/state/evidence/model_architecture.json}"
AGENT_COMMAND="${HAC_AGENT_COMMAND:-}"
AGENT_BACKEND="${HAC_AGENT_BACKEND:-cursor_sdk}"
AGENT_MODEL="${HAC_AGENT_MODEL:-composer-2.5}"

PYTHON="${HAC_PYTHON:-${ROOT}/.venv/bin/python}"
export PYTHONPATH="$ROOT"

"$PYTHON" -m hiagentresearch.src.orchestrator init
run_args=(
  --group-id "$GROUP_ID"
  --workdir "$WORKDIR"
  --quick
  --evidence-json "$EVIDENCE_JSON"
  --agent-backend "$AGENT_BACKEND"
  --agent-model "$AGENT_MODEL"
)
if [[ -n "$AGENT_COMMAND" ]]; then
  run_args+=(--agent-command "$AGENT_COMMAND")
fi
"$PYTHON" -m hiagentresearch.src.orchestrator run-group "${run_args[@]}"
