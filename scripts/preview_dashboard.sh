#!/usr/bin/env bash
# Build the dashboard from the local registry and serve it for visual review.
# Does not clear registry or restart loops-all.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STATE_DIR=".hiagentresearch/state"
OUTPUT_DIR=".hiagentresearch/dashboard-preview"
PORT=8765

if [[ ! -d .venv ]]; then
  echo "error: .venv not found" >&2
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

printf '==> Building dashboard from %s -> %s\n' "${STATE_DIR}" "${OUTPUT_DIR}"
hiagentresearch dashboard build --state-dir "${STATE_DIR}" --output-dir "${OUTPUT_DIR}"

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
