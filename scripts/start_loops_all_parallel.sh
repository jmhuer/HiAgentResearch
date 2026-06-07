#!/usr/bin/env bash
# Start the standard parallel Phase 1 validation (loops-all).
#
# Edit the variables below. Requires scripts/clean_slate.sh first for a fresh run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- run options (edit these) ---
# Which config to run: configs/standard.yaml (flat lineages) or configs/fanout.yaml
# (hierarchical areas). Override at call time:
#   CONFIG=configs/fanout.yaml ./scripts/start_loops_all_parallel.sh
CONFIG="${CONFIG:-configs/standard.yaml}"
LOOPS="${LOOPS:-3}"
AGENT_MODEL=""   # empty = use config.agent.model (+ config.agent.thinking)
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

if [[ ! -f "${CONFIG}" ]]; then
  echo "error: config not found: ${CONFIG}" >&2
  exit 1
fi
export HIAGENTRESEARCH_CONFIG="${CONFIG}"
echo "==> Config: ${CONFIG}"

# Fix for cursor-sdk local-agent InternalServerError (HTTP 500) behind a TLS-inspecting
# corporate proxy: the SDK's bundled Node bridge does not trust the macOS keychain, so
# its HTTPS call to Cursor's backend fails cert validation. Teach Node the system +
# corporate root CAs via NODE_EXTRA_CA_CERTS (additive to Node's built-ins). Honors an
# existing value; macOS only; trigger-only (no OS-specific code in the framework).
if [[ -z "${NODE_EXTRA_CA_CERTS:-}" && "$(uname)" == "Darwin" ]]; then
  CA_BUNDLE="${HOME}/.hiagentresearch-node-ca.pem"
  if [[ ! -s "${CA_BUNDLE}" ]]; then
    echo "==> Building Node CA bundle from macOS keychains -> ${CA_BUNDLE}"
    {
      security find-certificate -a -p /Library/Keychains/System.keychain
      security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain
    } > "${CA_BUNDLE}" 2>/dev/null || true
  fi
  if [[ -s "${CA_BUNDLE}" ]]; then
    export NODE_EXTRA_CA_CERTS="${CA_BUNDLE}"
    echo "==> NODE_EXTRA_CA_CERTS set (Cursor bridge will trust corporate root CA)"
  fi
fi

mkdir -p "$(dirname "${LOG_FILE}")"
: > "${LOG_FILE}"

args=(
  loops-all
  --loops "${LOOPS}"
)
if [[ -n "${AGENT_MODEL}" ]]; then
  args+=(--agent-model "${AGENT_MODEL}")
fi
if [[ "${RUN_EXACT_LOOPS}" == "true" ]]; then
  args+=(--run-exact-loops)
fi
if [[ "${PARALLEL}" == "true" ]]; then
  args+=(--parallel)
fi

printf '==> Starting: hiagentresearch %s\n' "${args[*]}"
printf '==> Log: %s\n' "${LOG_FILE}"

# Keep the machine awake for the duration of the run (macOS). caffeinate holds the
# idle-sleep assertion until the child process exits. This lives only in the trigger
# script — no OS-specific code in the framework. Note: on a laptop, closing the lid on
# battery (clamshell) can still sleep; keep the lid open or stay on AC power.
run_cmd=(hiagentresearch "${args[@]}")
if [[ "$(uname)" == "Darwin" ]] && command -v caffeinate >/dev/null 2>&1; then
  run_cmd=(caffeinate -i "${run_cmd[@]}")
  echo "==> Wrapped in caffeinate -i (machine stays awake while the run is active)"
fi

nohup "${run_cmd[@]}" >>"${LOG_FILE}" 2>&1 &
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
