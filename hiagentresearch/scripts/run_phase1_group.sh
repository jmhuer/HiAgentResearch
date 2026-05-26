#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GROUP_ID="${1:-model_architecture}"
WORKDIR="${2:-$ROOT}"
EVIDENCE_JSON="${HAC_EVIDENCE_JSON:-$ROOT/.hiagentresearch/state/evidence/model_architecture.json}"
AGENT_MODEL="${HAC_AGENT_MODEL:-composer-2.5}"

PYTHON="${HAC_PYTHON:-${ROOT}/.venv/bin/python}"
export PYTHONPATH="$ROOT"

"$PYTHON" -m hiagentresearch.src.orchestrator init
run_args=(
  --group-id "$GROUP_ID"
  --workdir "$WORKDIR"
  --quick
  --evidence-json "$EVIDENCE_JSON"
  --agent-model "$AGENT_MODEL"
)
"$PYTHON" -m hiagentresearch.src.orchestrator run-group "${run_args[@]}"
