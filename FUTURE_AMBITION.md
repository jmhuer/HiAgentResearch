# Future Ambition: HiAgentResearch

This is the forward plan for agents continuing work in this repository.

Primary context source:
- `/home/jmhuer/.cursor/plans/cursor_autoresearch_v2_0c398a9f.plan.md`

Secondary reference source:
- `/home/jmhuer/github/HiAgentControl/cursor-autoresearch-architecture.json`

Clean in-repo reference (canonical for this repo):
- [`hiagentresearch/docs/phase2_phase3_context.md`](hiagentresearch/docs/phase2_phase3_context.md)

## Decision Summary (aligned to v2 plan)

- Orchestration model: hybrid (`Cursor SDK` policy layer + `GitHub Actions` eval execution).
- Registry model (current): `SQLite + JSONL`, with explicit migration path to Postgres/ClickHouse.
- Agent runtime model: wake cycles (scheduled/event-driven), not always-on loops.
- Control-plane philosophy: Python remains thin (state/scheduling/gates/persistence).
- Operator interface: CLI-first Python package commands.
- Phase lock:
  - Phase 1: stable execution skeleton + observability + registry + onboarding.
  - Phase 2: merge system.
  - Phase 3: plugin packaging + optional ecosystem integrations.

## Non-negotiable system constraints

- Cursor-first behavior; no heavy Python context babysitting.
- Plan-before-code contract for each run.
- Frozen eval authority outside agent-editable code.
- Deterministic failure classes (`infra_failure`, `code_failure`, `eval_failure`, `invalid_cycle`).
- Append-only, auditable run evidence and transitions.

## Phase 1 requirements still open (must finish before Phase 2)

## 1) Evaluation registry must be production-shaped

Must finish:
- Canonical registry schema coverage for runs, metrics, transitions, artifacts, intent packets.
- Schema versioning + migration tests.
- Idempotent ingest behavior and duplicate protection for metrics.
- Validation gates for missing/malformed artifacts.
- Drift checks between local run state and GitHub-ingested state.

Acceptance:
- Clean bootstrap and upgrade both pass in CI.
- Registry writes are deterministic and queryable across repeated runs.

## 2) Onboarding strategy must be finalized

Decision we should keep unless revised with evidence:
- New project code remains in `<workdir>/`.
- Eval contract is frozen in `.hiagentresearch/eval/` (default).
- Agents can modify only configured editable project paths, not frozen eval.

Need to document explicitly:
- New-project intake flow.
- Whether native `<workdir>/eval/` is mirrored or replaced.
- Minimal steps to onboard a repo in under 15 minutes.

## 3) Long-horizon loop reliability and intent persistence

Must implement and test:
- 12-loop and 24-loop runs.
- Intent continuity checks (`active_hypothesis`, `next_action`, evidence lineage).
- Fault injection scenarios:
  - malformed logs/artifacts,
  - missing metrics/failure files,
  - transient CI/eval failures,
  - stale/noisy logs.
- Verify that the next loop performs repair/pivot behavior rather than degrading intent quality.

## 4) `config.yaml` as project stitch contract

Must add one root config contract for onboarding and generalization.

Required fields:
- `project_id`
- `workdir`
- `editable_paths`
- `frozen_eval_entrypoint`
- `evaluation.command_template`
- `evaluation.parser`
- `research_groups`
- `artifact_contract`
- `policy_modes`

Runtime requirement:
- Prompt assembly and behavior contracts are generated from config + group metadata.
- Core runtime code stays project-agnostic.

## 5) Generalize internal prompts/behavior

Confirmed direction:
- Internal prompts should be template-driven and config-backed.
- No hardcoded MNIST assumptions in core orchestrator/agent backend paths.
- Root config owns project-specific adaptation; library code remains generic.

## 6) Additional Phase 1 hardening for stable simple execution

- Post-edit allowlist enforcement from git diff against configured editable paths.
- Correlation IDs across local run, GitHub run, and registry events.
- Secret handling hardening (no secret values in run artifacts/logs).
- CI quality gates for runtime contracts and smoke loops.
- Registry-backed status/report command for operators.

## Phase 2 scope (directly aligned to v2 plan)

Phase 2 deliverables:
- Merge candidate promotion and compatibility evaluation.
- Evidence-backed merge controller decisions with rollback-safe checkpoints.
- Policy-driven scoring model and merge thresholds.
- Merge-specific workflow and docs.

Planned implementation targets in this repo:
- `hiagentresearch/src/merge_controller.py`
- `hiagentresearch/src/score_model.py`
- `hiagentresearch/docs/merge-policy.md`
- `.github/workflows/merge-eval.yml`

## Phase 3 scope (directly aligned to v2 plan)

Phase 3 deliverables:
- Package stabilized skills/commands/rules as plugin assets.
- Keep pluginization optional until Phase 1/2 runtime is proven stable.
- Add optional ecosystem integrations only where complexity decreases.

Planned implementation targets in this repo:
- `hiagentresearch/.cursor-plugin/plugin.json`
- `hiagentresearch/skills/`
- `hiagentresearch/commands/`
- `hiagentresearch/rules/`

## Immediate execution order

1. Finalize `config.yaml` schema + loader.
2. Remove remaining project-specific prompt/path assumptions.
3. Complete registry versioning/migration and ingest tests.
4. Run long-loop soak tests with fault injection.
5. Lock onboarding runbook for new projects.

If future agents need to choose between speed and correctness, prioritize correctness of:
- frozen eval boundaries,
- deterministic contracts,
- registry integrity,
- reproducible state transitions.
