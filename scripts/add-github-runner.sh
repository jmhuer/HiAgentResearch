#!/usr/bin/env bash
# Register an ADDITIONAL self-hosted GitHub Actions runner for HiAgentResearch.
#
# A single runner instance executes exactly ONE CI job at a time. To run N CI evals
# concurrently (e.g. all leaves of a fan-out wave at once), register N runners — one per
# desired concurrent slot — each in its own directory with a unique name and the same
# `self-hosted` label (so `runs-on: self-hosted` matches any of them).
#
# Usage:
#   scripts/add-github-runner.sh [NAME] [DIR]
#     NAME  runner name (default: macrunner-2). Must be unique across the repo's runners.
#     DIR   install dir  (default: ~/actions-runner-<suffix-after-last-dash-of-NAME>)
#
# Env overrides:
#   GH_HOST                GitHub host         (default: github.com)
#   HIAGENTRESEARCH_REPO   owner/repo          (default: jmhuer/HiAgentResearch)
#   ACTIONS_RUNNER_SRC     existing runner dir to copy the binaries from (default: ~/actions-runner)
#   START                  "run" (foreground) | "svc" (service) | "" (just register; default)
#
# The registration token is minted fresh here (they expire ~1h and are single-use), so there
# is never a stale-token / copy-paste mismatch — the cause of the 404 at .../runner-registration.
set -euo pipefail

GH_HOST="${GH_HOST:-github.com}"
REPO="${HIAGENTRESEARCH_REPO:-jmhuer/HiAgentResearch}"
URL="https://${GH_HOST}/${REPO}"
SRC="${ACTIONS_RUNNER_SRC:-$HOME/actions-runner}"
NAME="${1:-macrunner-2}"
DIR="${2:-$HOME/actions-runner-${NAME##*-}}"
START="${START:-}"

if [[ ! -d "$SRC" ]]; then
  echo "error: source runner dir not found: $SRC (set ACTIONS_RUNNER_SRC)" >&2
  exit 1
fi
if [[ -e "$DIR" ]]; then
  echo "error: target dir already exists: $DIR (remove it or pass a different DIR)" >&2
  exit 1
fi

echo "==> Minting a fresh registration token for $REPO"
TOKEN="$(GH_HOST="$GH_HOST" gh api -X POST \
  "repos/${REPO}/actions/runners/registration-token" --jq .token)"
[[ -n "$TOKEN" ]] || { echo "error: failed to mint registration token (is gh authenticated for $GH_HOST?)" >&2; exit 1; }

echo "==> Copying runner binaries: $SRC -> $DIR (dropping prior registration state)"
cp -R "$SRC" "$DIR"
( cd "$DIR" && rm -rf .runner .credentials .credentials_rsaparams _work _diag )

echo "==> Registering runner '$NAME' (label: self-hosted)"
( cd "$DIR" && ./config.sh --url "$URL" --token "$TOKEN" \
    --name "$NAME" --labels self-hosted --work _work --unattended )

echo "==> Registered: $NAME  (dir: $DIR)"
case "$START" in
  run) echo "==> Starting in foreground"; ( cd "$DIR" && ./run.sh ) ;;
  svc) echo "==> Installing + starting as a service"; ( cd "$DIR" && ./svc.sh install && ./svc.sh start ) ;;
  *)   echo "    Start it with:   ( cd '$DIR' && ./run.sh )           # foreground"
       echo "    Or as a service: ( cd '$DIR' && ./svc.sh install && ./svc.sh start )" ;;
esac
