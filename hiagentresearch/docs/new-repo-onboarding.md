# New Repo Onboarding

HiAgentResearch uses one small root `config.yaml` with two zones: an agent-owned **workspace** (`workdir`) and a read-only **evaluation zone** derived from `evaluation.entrypoint` (the entrypoint file and its parent directory, e.g. `.hiagentresearch/eval/`). The Python control plane stays generic; project-specific behavior belongs in config, the workspace, and the frozen eval adapter.

## 15-Minute Path

1. Put project code under the configured `workdir` (the agent owns this whole folder).
2. Create or update root `config.yaml` with the core fields:
   - `project_id`
   - `workdir` — agent-owned workspace (full read/write/create)
   - `evaluation` — `entrypoint`, `command_template`, and `targets` (the entrypoint’s directory is the read-only eval zone automatically)
   - `research_groups`
   - plus `policy_modes`, optional `agent_contract` (`research_output_expectations`, `retry_policy`), and optional `dependency_files` / `generated_paths` / `hidden_paths`
   - framework guidance doc paths live in `hiagentresearch/src/core/guidance.py` (not config); workspace `<workdir>/AGENTS.md` is generated from config
3. Put the runtime-owned eval adapter and scorer under `.hiagentresearch/eval/` (outside the workspace).
4. The agent may read the eval zone to understand scoring but never edit or run it. The frozen adapter in `.hiagentresearch/eval/` is what HiAgentResearch and CI execute.
5. Validate config:

```bash
hiagentresearch config validate
```

6. Initialize state:

```bash
hiagentresearch init
```

7. Generate the workspace contract (`<workdir>/AGENTS.md`) from config — also run automatically by `init`:

```bash
hiagentresearch render-workspace-docs
```

8. Run one group:

```bash
hiagentresearch run-group --group-id model_architecture --workdir .
```

9. Inspect status:

```bash
hiagentresearch status --group-id model_architecture
```

## Framework artifact contracts

Artifact filenames are fixed in `hiagentresearch/src/core/artifacts.py` (not in config):

- **Eval node** (flat dir after eval or GitHub artifact root): four canonical JSON files (`metrics.json`, `failure_class.json`, `research_outcome.json`, `run_meta.json`) plus optional `stdout.txt`, `stderr.txt`, and `parsed_eval.json`. Metric keys come from `evaluation.targets`; the frozen adapter shapes stdout JSON.
- **Run cycle** (`.hiagentresearch/runs/<run_id>/`): `experiment_intent.json`, `experiment_plan.md`, `agent_actions.jsonl`.
- **Experiment manifest** (when present): copied to the GH bundle as `experiment_manifest.json`.

## Contract Boundaries

- Agents own the workspace (`workdir`): inspect code, form hypotheses, add tests/dependencies, restructure, and edit any file under it.
- Agents may write run-local observability artifacts under `.hiagentresearch/runs/<run_id>/`.
- The evaluation zone (derived from `evaluation.entrypoint`) is read-only: agents read it to understand scoring but must not edit or run it. Edits there are rejected as an invalid cycle.
- Agents get fast feedback from their own quick unit/smoke tests; metric-producing training/eval is owned by the orchestrator and GitHub eval nodes.
- GitHub Actions is the committed-branch eval authority.
- Git-tracked experiment manifests are the durable source for per-run intent; the SQLite registry is the local read model for metrics, outcomes, artifacts, transitions, and tactical intent.

## Quality Retry Policy

Research groups keep cycling until configured output expectations and metric bounds are met, or until a configured stop condition blocks the group with a clear reason. Retries should use the persisted intent packet:

- `repair` for deterministic code failures,
- `pivot` for underperforming hypotheses,
- `reset` when a rollback anchor is required,
- `continue` when evidence supports another iteration.

Do not patch around failures with special-case guardrails. If a boundary is missing, fix the canonical contract: config schema, eval adapter, registry invariant, or operator command.

## Eval Setup

Have agents write their own quick unit/smoke tests under the workspace for cheap
feedback. Keep the final judge in the frozen eval adapter under
`.hiagentresearch/eval/` and have it print **canonical JSON** to stdout: a
top-level object with `passed` / `execution_passed` health flags plus the metric
keys named in `evaluation.targets`. Canonical JSON is the only eval contract —
there is no `parser` field; if your scoring tool emits a different shape, reshape
it inside the readable frozen adapter.
