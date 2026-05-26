# GitHub Strategy

Phase 1 uses GitHub as the committed-branch eval authority.

## Current Contract

- Research groups live on long-lived `research/**` branches.
- Pushes to research branches trigger `hiagentresearch-research-eval`.
- The workflow resolves the group and eval command from `config.yaml`.
- CI writes canonical artifacts: `stdout.txt`, `stderr.txt`, `metrics.json`, `failure_class.json`, `run_meta.json`, registry event output, and `evals.db`.
- Commit messages carry `HiAgentResearch-Run-ID: <run_id>` so local and GitHub runs share a correlation ID.
- Branch concurrency prevents stale eval pileups.

## Agent Freedom

Agents should have enough room to inspect code, use tools, form hypotheses, and make bounded edits. The boundaries are canonical:

- configured editable paths,
- run-local observability artifacts,
- frozen eval authority under `.hiagentresearch/eval/`,
- deterministic failure classes,
- registry-backed evidence and metrics.

Do not make agents brittle by hardcoding project paths into core runtime prompts. Generate guidance from config, group metadata, policy mode, and the intent packet.

## Quality Loop

A passing CI run is necessary but not always sufficient. Metric bounds and output expectations in `config.yaml` define whether research quality is acceptable. If output quality is low, the next cycle should repair, pivot, reset, or continue based on the intent packet rather than repeating the same behavior.
