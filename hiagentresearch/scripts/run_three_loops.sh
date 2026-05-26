#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${HAC_PYTHON:-${ROOT}/.venv/bin/python}"
GROUP_ID="${1:-model_architecture}"
BRANCH="${2:-research/model-architecture}"
LOOPS="${3:-3}"
WORKDIR="${ROOT}"
EVIDENCE_JSON="${ROOT}/.hiagentresearch/state/evidence/model_architecture.json"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing python interpreter at $PYTHON" >&2
  exit 1
fi

cd "$ROOT"
export PYTHONPATH="$ROOT"
git checkout "$BRANCH"

if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  echo "CURSOR_API_KEY is required for real cursor-agent loops." >&2
  exit 1
fi

"$PYTHON" -m hiagentresearch.src.orchestrator init

for i in $(seq 1 "$LOOPS"); do
  echo "=== Loop $i/$LOOPS ==="
  out="$("$PYTHON" -m hiagentresearch.src.orchestrator run-group \
    --group-id "$GROUP_ID" \
    --workdir "$WORKDIR" \
    --quick \
    --evidence-json "$EVIDENCE_JSON" \
    --agent-backend cursor_sdk \
    --agent-model composer-2.5)"
  echo "$out"
  run_id="$("$PYTHON" - <<'PY' "$out"
import re, sys
m = re.search(r'"run_id"\s*:\s*"([^"]+)"', sys.argv[1])
if not m:
    raise SystemExit("Could not parse run_id")
print(m.group(1))
PY
)"

  git add \
    mnist/pipeline/research_markers.py \
    mnist/pipeline/research_hypotheses.py \
    mnist/pipeline/model.py \
    mnist/pipeline/train.py \
    mnist/eval/run_eval.py
  staged="$(git diff --cached --name-only)"
  has_core_change=0
  while IFS= read -r staged_file; do
    case "$staged_file" in
      mnist/pipeline/model.py|mnist/pipeline/train.py|mnist/eval/run_eval.py)
        has_core_change=1
        break
        ;;
    esac
  done <<< "$staged"
  if [[ "$has_core_change" -ne 1 ]]; then
    echo "Loop ${i} produced no staged core experiment change." >&2
    printf '%s\n' "$staged" >&2
    exit 1
  fi
  git commit -m "Phase1 loop ${i}: ${GROUP_ID} planned experiment update."
  git push

  head_sha="$(git rev-parse HEAD)"
  gh_run_id=""
  for _ in $(seq 1 30); do
    runs_json="$(gh run list --branch "$BRANCH" --limit 20 --json databaseId,headSha,name,status)"
    gh_run_id="$("$PYTHON" - <<'PY' "$runs_json" "$head_sha"
import json, sys
runs = json.loads(sys.argv[1]); sha = sys.argv[2]
for run in runs:
    if run.get("headSha") == sha and run.get("name") == "hiagentresearch-mnist-phase1":
        print(run["databaseId"])
        raise SystemExit(0)
print("")
PY
)"
    if [[ -n "$gh_run_id" ]]; then
      break
    fi
    sleep 3
  done
  if [[ -z "$gh_run_id" ]]; then
    echo "No GitHub run found for ${head_sha}" >&2
    exit 1
  fi

  gh run watch "$gh_run_id" --exit-status
  dl_dir="${ROOT}/.hiagentresearch/state/github_runs/${gh_run_id}"
  rm -rf "$dl_dir"
  mkdir -p "$dl_dir"
  gh run download "$gh_run_id" --dir "$dl_dir"

  metrics_file="$(ls "$dl_dir"/hiagentresearch-*/metrics.json)"
  failure_file="$(ls "$dl_dir"/hiagentresearch-*/failure_class.json)"
  "$PYTHON" - <<'PY' "$metrics_file" "$failure_file" "$run_id" "$gh_run_id"
import json, pathlib, sys
metrics = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
failure = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
assert failure.get("failure_class") == "none", failure
assert int(metrics.get("tests_passed", 0)) >= 1, metrics
print(json.dumps({
    "local_run_id": sys.argv[3],
    "github_run_id": sys.argv[4],
    "tests_passed": metrics.get("tests_passed"),
    "tests_failed": metrics.get("tests_failed"),
    "duration_sec": metrics.get("duration_sec"),
}, indent=2))
PY
done

echo "Three-loop test succeeded for ${GROUP_ID} on ${BRANCH}."
