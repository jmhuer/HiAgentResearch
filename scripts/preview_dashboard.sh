#!/usr/bin/env bash
# Build the dashboard from the local registry and serve it for visual review.
# Does not clear registry or restart loops-all.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# The config is a REQUIRED positional argument — it is not read from an env var on purpose.
# The dashboard's lineage topology (the scaffold the plots are drawn on) is built from the
# CONFIG, while the run data comes from the registry. They MUST be the same config the run
# used, or the topology nodes won't match the recorded groups and the plots render empty.
# `dashboard build` does NOT read HIAGENTRESEARCH_CONFIG, so always pass the run's config.
#   Usage: scripts/preview_dashboard.sh <config-path>
#   e.g.   scripts/preview_dashboard.sh configs/full_pipeline.yaml
CONFIG="${1:-}"
if [[ -z "${CONFIG}" ]]; then
  echo "usage: scripts/preview_dashboard.sh <config-path>" >&2
  echo "  the config must match the one the run used (it builds the plot topology)" >&2
  echo "  e.g. scripts/preview_dashboard.sh configs/full_pipeline.yaml" >&2
  exit 2
fi

STATE_DIR=".hiagentresearch/state"
OUTPUT_DIR=".hiagentresearch/dashboard-preview"
PORT=8765

if [[ ! -d .venv ]]; then
  echo "error: .venv not found" >&2
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

if [[ ! -f "${CONFIG}" ]]; then
  echo "error: config not found: ${CONFIG} (set CONFIG=path/to/configs/<name>.yaml)" >&2
  exit 1
fi

printf '==> Building dashboard from %s (config: %s) -> %s\n' "${STATE_DIR}" "${CONFIG}" "${OUTPUT_DIR}"
hiagentresearch dashboard --config "${CONFIG}" build --state-dir "${STATE_DIR}" --output-dir "${OUTPUT_DIR}" --prefer-json

python3 - <<'PY'
import json
from pathlib import Path

summary = json.loads(Path(".hiagentresearch/dashboard-preview/summary.json").read_text())
baseline = (summary.get("lineage_topology") or {}).get("baseline_snapshot") or {}
metrics = baseline.get("metrics") or {}
print("baseline_snapshot metrics:", ", ".join(sorted(metrics)) or "(none)")
for name in ("accuracy", "latency_ms"):
    if name not in metrics:
        print(f"warning: baseline missing {name!r} — L0 will not render for that metric")
PY

printf '\n==> Preview: http://127.0.0.1:%s/\n' "${PORT}"
printf '    Stop with Ctrl+C\n\n'
exec python3 -m http.server "${PORT}" --directory "${OUTPUT_DIR}"
