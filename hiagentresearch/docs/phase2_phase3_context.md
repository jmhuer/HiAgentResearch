# Phase 2/3 Context Reference (HiAgentResearch)

This document keeps Phase 2/3 planning context inside the clean `HiAgentResearch` repository.

## Source documents

Primary source:
- `/home/jmhuer/.cursor/plans/cursor_autoresearch_v2_0c398a9f.plan.md`

Secondary source:
- `/home/jmhuer/github/HiAgentControl/cursor-autoresearch-architecture.json`

## Normalized repository target

All future references should use this repository as the execution root:
- `/home/jmhuer/github/HiAgentResearch`

Not the legacy path:
- `/home/jmhuer/github/HiAgentControl/hiagentresearch`

## Phase lock

- Phase 1: stable runtime skeleton, deterministic eval integration, registry integrity, onboarding contract.
- Phase 2: merge system (promotion + scoring + gated merge + rollback checkpoints).
- Phase 3: plugin packaging and optional ecosystem integrations.

## Phase 2 concrete targets in this repo

- `hiagentresearch/src/merge_controller.py`
- `hiagentresearch/src/score_model.py`
- `hiagentresearch/docs/merge-policy.md`
- `.github/workflows/merge-eval.yml`

## Phase 3 concrete targets in this repo

- `hiagentresearch/.cursor-plugin/plugin.json`
- `hiagentresearch/skills/`
- `hiagentresearch/commands/`
- `hiagentresearch/rules/`

## Carry-forward constraints from the v2 plan

- Hybrid orchestration model: SDK policy + GitHub Actions execution.
- Thin Python control plane; no Python-heavy context babysitting.
- CLI-first operations for operators.
- Structured artifact contract and deterministic failure classification.
- State-machine-based transitions and append-only run events.
