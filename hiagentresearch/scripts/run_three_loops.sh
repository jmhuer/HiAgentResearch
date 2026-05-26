#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${HAC_PYTHON:-${ROOT}/.venv/bin/python}"
GROUP_ID="${1:-model_architecture}"
BRANCH="${2:-research/model-architecture}"
LOOPS="${3:-3}"

cd "$ROOT"
export PYTHONPATH="$ROOT"

"$PYTHON" -m hiagentresearch.src.loop_controller \
  --group-id "$GROUP_ID" \
  --branch "$BRANCH" \
  --loops "$LOOPS" \
  --workdir "$ROOT" \
  --quick \
  --run-exact-loops
