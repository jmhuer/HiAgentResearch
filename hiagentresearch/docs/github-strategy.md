# GitHub Strategy

Phase 1 uses GitHub as the committed-branch eval authority.

## Current Contract

- Research groups live on long-lived `research/**` branches.
- Pushes to research branches trigger `hiagentresearch-research-eval`.
- The workflow resolves the group and eval command from the active config file (`configs/standard.yaml` by default).
- CI writes canonical artifacts: `stdout.txt`, `stderr.txt`, `metrics.json`, `failure_class.json`, `research_outcome.json`, `run_meta.json`, registry event output, and `evals.db`.
- Commit messages carry `HiAgentResearch-Run-ID: <run_id>` so local and GitHub runs share a correlation ID.
- Branch concurrency prevents stale eval pileups.

## Agent Freedom

Agents should have enough room to inspect code, use tools, form hypotheses, and make bounded edits. The boundaries are canonical:

- configured editable paths,
- run-local observability artifacts,
- frozen eval authority under `.hiagentresearch/eval/`,
- deterministic execution failure classes,
- explicit research outcomes for baseline improvement versus measured regression,
- registry-backed evidence and metrics.

Do not make agents brittle by hardcoding project paths into core runtime prompts. Generate guidance from config, group metadata, policy mode, and the intent packet.

## Quality Loop

A passing CI run means the eval authority executed cleanly. Metric bounds and output expectations in the active config file define whether the experiment improved baseline. If a valid change regresses or stays neutral, that is research evidence, not an infrastructure failure: the orchestrator carries the outcome forward in the intent packet and the next cycle continues (or repairs, when a metric regressed). Trying a different direction is structural — a separate fan-out leaf — not an in-lineage pivot.
