# Group Runner Skeleton

## Inputs

- planner output
- group charter
- allowed file paths

## Required action log entries

- `start_cycle`
- `code_inspection`
- `intent_written`
- `plan_written`
- `change_proposal`
- `core_experiment_change`
- `eval_triggered`
- `eval_completed`
- `intent_packet_updated`

## Rules

- Do not claim success without eval artifacts.
- Plan before code: write `experiment_intent.json` and `experiment_plan.md` first.
- Every cycle must include at least one real change to a configured core allowed file.
- Continue through repair, pivot, reset, or continue decisions until configured quality expectations are met or the group is explicitly blocked.
- Persist action trace for each step.
