# Results Ingester Skeleton

## Inputs

- canonical artifacts (`metrics.json`, `failure_class.json`, `run_meta.json`)
- planning artifacts (`experiment_intent.json`, `experiment_plan.md`)
- group id and branch id

## Outputs

- registry run row
- metric rows
- transition and artifact rows

## Rules

- Reject ingestion if required artifacts are missing.
- Never auto-convert missing artifacts into success.
- Keep planning artifacts linked to each run for auditability.
