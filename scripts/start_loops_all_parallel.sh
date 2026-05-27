#!/usr/bin/env bash
# Start the standard parallel Phase 1 validation (loops-all).
#
# Edit the variables below. Requires scripts/clean_slate.sh first for a fresh run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- run options (edit these) ---
LOOPS=3
AGENT_MODEL="composer-2.5"
RUN_EXACT_LOOPS=true
PARALLEL=true
LOG_FILE=".hiagentresearch/loops-all-parallel-3x4.log"
CREDENTIALS_FILE="credentials/cursor_secret.txt"
# ---

if [[ ! -d .venv ]]; then
  echo "error: .venv not found; create the venv before starting loops-all" >&2
  exit 1
fi

if [[ ! -f "${CREDENTIALS_FILE}" ]]; then
  echo "error: missing ${CREDENTIALS_FILE}" >&2
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate
export CURSOR_API_KEY="$(tr -d '\r\n' < "${CREDENTIALS_FILE}")"
export PYTHONUNBUFFERED=1

mkdir -p "$(dirname "${LOG_FILE}")"
: > "${LOG_FILE}"

args=(
  loops-all
  --loops "${LOOPS}"
  --agent-model "${AGENT_MODEL}"
)
if [[ "${RUN_EXACT_LOOPS}" == "true" ]]; then
  args+=(--run-exact-loops)
fi
if [[ "${PARALLEL}" == "true" ]]; then
  args+=(--parallel)
fi

printf '==> Starting: hiagentresearch %s\n' "${args[*]}"
printf '==> Log: %s\n' "${LOG_FILE}"

nohup hiagentresearch "${args[@]}" >>"${LOG_FILE}" 2>&1 &
pid=$!

sleep 2
if ! kill -0 "${pid}" 2>/dev/null; then
  echo "error: loops-all exited immediately; see ${LOG_FILE}" >&2
  tail -20 "${LOG_FILE}" >&2 || true
  exit 1
fi

echo "PID=${pid}"
echo "Monitor: tail -f ${LOG_FILE}"
echo "        pgrep -af 'hiagentresearch loops-all'"
