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

PY="$([[ -x .venv/bin/python ]] && echo .venv/bin/python || echo python3)"
REMOTE="$("${PY}" -c 'from hiagentresearch.src.core.config import load_config; print(load_config().github.remote)' 2>/dev/null || echo origin)"
if [[ -n "${HIAGENTRESEARCH_RESEARCH_BRANCH_PREFIX:-}" ]]; then
  RESEARCH_BRANCH_PREFIX="${HIAGENTRESEARCH_RESEARCH_BRANCH_PREFIX}"
else
  RESEARCH_BRANCH_PREFIX="$("${PY}" -c 'from hiagentresearch.src.core.config import load_config; print(load_config().orchestration.branch_prefix)' 2>/dev/null || true)"
  RESEARCH_BRANCH_PREFIX="${RESEARCH_BRANCH_PREFIX:-hiagentresearch}"
fi

collect_research_branches() {
  local -n _out=$1
  declare -A seen=()
  while IFS= read -r branch; do
    [[ -z "${branch}" ]] && continue
    [[ "${branch}" == "${RESEARCH_BRANCH_PREFIX}/"* ]] || continue
    if [[ -z "${seen[${branch}]+x}" ]]; then
      seen["${branch}"]=1
      _out+=("${branch}")
    fi
  done < <(git branch --format='%(refname:short)' || true)
  while IFS= read -r branch; do
    [[ -z "${branch}" ]] && continue
    if [[ -z "${seen[${branch}]+x}" ]]; then
      seen["${branch}"]=1
      _out+=("${branch}")
    fi
  done < <(git ls-remote --heads "${REMOTE}" "refs/heads/${RESEARCH_BRANCH_PREFIX}/*" 2>/dev/null \
    | awk '{print $2}' | sed 's|^refs/heads/||' || true)
}

log() {
  printf '==> %s\n' "$*"
}

stop_processes() {
  log "Stopping hiagentresearch / eval / cursor agent processes"
  pkill -f "hiagentresearch loops-all" 2>/dev/null || true
  pkill -f "hiagentresearch.cli loops" 2>/dev/null || true
  pkill -f "run_phase1_eval.py" 2>/dev/null || true
  pkill -f "cursor-sdk-bridge" 2>/dev/null || true
  sleep 2
  if pgrep -af "hiagentresearch|run_phase1_eval|cursor-sdk-bridge" >/dev/null 2>&1; then
    log "Force-stopping remaining hiagentresearch processes"
    pkill -9 -f "hiagentresearch" 2>/dev/null || true
    pkill -9 -f "run_phase1_eval" 2>/dev/null || true
    pkill -9 -f "cursor-sdk-bridge" 2>/dev/null || true
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
  local branches=()
  collect_research_branches branches
  log "Checking out main and deleting ${#branches[@]} local ${RESEARCH_BRANCH_PREFIX}/* branches"
  git checkout main
  for branch in "${branches[@]}"; do
    git branch -D "${branch}" 2>/dev/null || true
  done
}

clean_remote_branches() {
  local branches=()
  collect_research_branches branches
  log "Deleting ${#branches[@]} remote ${RESEARCH_BRANCH_PREFIX}/* branches on ${REMOTE}"
  for branch in "${branches[@]}"; do
    git push "${REMOTE}" --delete "${branch}" 2>/dev/null || true
  done
}

clean_registry() {
  log "Wiping local registry and run artifacts"
  rm -f .hiagentresearch/state/evals.db
  rm -rf .hiagentresearch/runs/*
  rm -rf .hiagentresearch/cycles/*
  rm -rf .hiagentresearch/dashboard-preview
  rm -f .hiagentresearch/*.log
  mkdir -p .hiagentresearch/runs .hiagentresearch/state .hiagentresearch/cycles
}

print_status() {
  log "Clean slate status"
  echo "  repo:        ${REPO_ROOT}"
  echo "  branch:      $(git branch --show-current)"
  echo "  main/${REMOTE}: $(git rev-parse main) / $(git rev-parse ${REMOTE}/main 2>/dev/null || echo n/a)"
  echo "  worktrees:   $(git worktree list | wc -l | tr -d ' ')"
  echo "  evals.db:    $([[ -f .hiagentresearch/state/evals.db ]] && echo present || echo absent)"
  echo "  run dirs:    $(find .hiagentresearch/runs -mindepth 1 2>/dev/null | wc -l | tr -d ' ')"
  echo "  wt dirs:     $(ls -A .hiagentresearch/worktrees 2>/dev/null | wc -l | tr -d ' ')"
  echo "  remote ${RESEARCH_BRANCH_PREFIX}/*: $(git ls-remote --heads ${REMOTE} "${RESEARCH_BRANCH_PREFIX}/*" 2>/dev/null | wc -l | tr -d ' ')"
  if pgrep -af "hiagentresearch|run_phase1_eval|cursor-sdk-bridge" >/dev/null 2>&1; then
    echo "  processes:   still running (see pgrep -af 'hiagentresearch|cursor-sdk-bridge')"
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
  log "Done. Sync main if needed: git pull ${REMOTE} main"
}

main "$@"
