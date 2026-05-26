# New Repo Onboarding

HiAgentResearch uses one small root `config.yaml` to stitch a project into the research runtime. The Python control plane stays generic; project-specific behavior belongs in config, editable project files, and the frozen eval adapter.

## 15-Minute Path

1. Put project code under the configured `workdir`.
2. Create or update root `config.yaml`:
   - `project_id`
   - `workdir`
   - `editable_paths`
   - `frozen_eval_entrypoint`
   - `agent_tools.validation_commands`
   - `evaluation.command_template`
   - `evaluation.parser`
   - `research_groups`
   - `artifact_contract`
   - `policy_modes`
3. Put the runtime-owned eval adapter under `.hiagentresearch/eval/`.
4. Keep native project eval code in `<workdir>/eval/` only if it is part of the editable project. The frozen adapter in `.hiagentresearch/eval/` is what HiAgentResearch and CI execute.
5. Validate config:

```bash
hiagentresearch config validate
```

6. Initialize state:

```bash
hiagentresearch init
```

7. Run one group:

```bash
hiagentresearch run-group --group-id model_architecture --workdir . --quick
```

8. Inspect status:

```bash
hiagentresearch status --group-id model_architecture
```

## Contract Boundaries

- Agents may inspect code, use tools, form hypotheses, and edit configured `editable_paths`.
- Agents may run configured validation commands for local feedback.
- Agents may write run-local observability artifacts under `.hiagentresearch/runs/<run_id>/`.
- Agents must not edit frozen eval adapters.
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

Use project-owned validation commands for cheap agent feedback, for example unit
tests or smoke evals under the workdir. Keep the final judge in the frozen eval
adapter and have it print canonical JSON. Prefer `evaluation.parser:
canonical_json_stdout`; only add a parser profile when a truly reusable output
format cannot be adapted in the frozen eval file.
