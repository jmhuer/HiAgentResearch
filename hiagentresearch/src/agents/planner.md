# Planner Skeleton

## Inputs

- group charter
- prior intent packet
- latest eval summary

## Outputs

- `hypothesis_id`
- `hypothesis_text`
- `expected_impact`
- `risk_notes`
- `eval_request`
- `evidence_plan` (code paths and optional web sources)

## Rules

- One hypothesis per cycle.
- If prior failure class is `code_failure`, generate repair-focused hypothesis.
