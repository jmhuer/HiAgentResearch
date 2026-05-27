#!/usr/bin/env bash
# Reset local HiAgentResearch state so you can restart validation from main.
#
# Edit the variables below. There are no command-line flags.
# FULL_CLEANUP=true (default) enables every step; set it false to pick steps individually.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- cleanup options (edit these) ---
FULL_CLEANUP=true
STOP_PROCESSES=true
CLEAN_BRANCHES=true
CLEAN_REGISTRY=true
CLEAN_WORKTREES=true
CLEAN_REMOTE_BRANCHES=true
# ---

if [[ "${FULL_CLEANUP}" == "true" ]]; then
  STOP_PROCESSES=true
  CLEAN_BRANCHES=true
  CLEAN_REGISTRY=true
  CLEAN_WORKTREES=true
  CLEAN_REMOTE_BRANCHES=true
fi

RESEARCH_BRANCHES=(
  research/model-architecture
  research/data-augmentation
  research/optimization-strategy
  research/hyperparameter-optimization
)

log() {
  printf '==> %s\n' "$*"
}

stop_processes() {
  log "Stopping hiagentresearch / eval processes"
  pkill -f "hiagentresearch loops-all" 2>/dev/null || true
  pkill -f "hiagentresearch.cli loops" 2>/dev/null || true
  pkill -f "run_phase1_eval.py" 2>/dev/null || true
  sleep 2
  if pgrep -af "hiagentresearch|run_phase1_eval" >/dev/null 2>&1; then
    log "Force-stopping remaining hiagentresearch processes"
    pkill -9 -f "hiagentresearch" 2>/dev/null || true
    pkill -9 -f "run_phase1_eval" 2>/dev/null || true
    sleep 1
  fi
}

clean_worktrees() {
  log "Removing git worktrees under ${REPO_ROOT}"
  git checkout main >/dev/null 2>&1 || true
  while IFS= read -r wt_path; do
    [[ -z "${wt_path}" ]] && continue
    if [[ "${wt_path}" == "${REPO_ROOT}" ]]; then
      continue
    fi
    git worktree remove --force "${wt_path}" 2>/dev/null || true
  done < <(git worktree list --porcelain | awk '/^worktree / {print $2}')
  rm -rf .hiagentresearch/worktrees/*
}

clean_branches() {
  log "Checking out main and deleting local research branches"
  git checkout main
  for branch in "${RESEARCH_BRANCHES[@]}"; do
    git branch -D "${branch}" 2>/dev/null || true
  done
}

clean_remote_branches() {
  log "Deleting remote research branches on origin"
  for branch in "${RESEARCH_BRANCHES[@]}"; do
    git push origin --delete "${branch}" 2>/dev/null || true
  done
}

clean_registry() {
  log "Wiping local registry and run artifacts"
  rm -f .hiagentresearch/state/evals.db
  rm -rf .hiagentresearch/runs/*
  mkdir -p .hiagentresearch/runs .hiagentresearch/state
}

print_status() {
  log "Clean slate status"
  echo "  repo:        ${REPO_ROOT}"
  echo "  branch:      $(git branch --show-current)"
  echo "  main/origin: $(git rev-parse main) / $(git rev-parse origin/main 2>/dev/null || echo n/a)"
  echo "  worktrees:   $(git worktree list | wc -l | tr -d ' ')"
  echo "  evals.db:    $([[ -f .hiagentresearch/state/evals.db ]] && echo present || echo absent)"
  echo "  run dirs:    $(find .hiagentresearch/runs -mindepth 1 2>/dev/null | wc -l | tr -d ' ')"
  echo "  wt dirs:     $(ls -A .hiagentresearch/worktrees 2>/dev/null | wc -l | tr -d ' ')"
  echo "  remote research/*: $(git ls-remote --heads origin 'research/*' 2>/dev/null | wc -l | tr -d ' ')"
  if pgrep -af "hiagentresearch|run_phase1_eval" >/dev/null 2>&1; then
    echo "  processes:   still running (see pgrep -af hiagentresearch)"
  else
    echo "  processes:   none"
  fi
}

main() {
  log "HiAgentResearch clean slate (FULL_CLEANUP=${FULL_CLEANUP})"

  if [[ "${STOP_PROCESSES}" == "true" ]]; then
    stop_processes
  fi
  if [[ "${CLEAN_WORKTREES}" == "true" ]]; then
    clean_worktrees
  fi
  if [[ "${CLEAN_BRANCHES}" == "true" ]]; then
    clean_branches
  fi
  if [[ "${CLEAN_REMOTE_BRANCHES}" == "true" ]]; then
    clean_remote_branches
  fi
  if [[ "${CLEAN_REGISTRY}" == "true" ]]; then
    clean_registry
  fi

  print_status
  log "Done. Sync main if needed: git pull origin main"
}

main "$@"
