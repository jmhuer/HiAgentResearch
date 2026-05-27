# Research lineage

Each research group declares how its branch is bootstrapped:

- `baseline` — create or continue from `orchestration.baseline_ref` (default `main`).
- `inherit` — create from a parent group's anchor commit (`last_commit` or `best_commit`).
- `force` — reserved for Phase 2 promotion; not implemented yet.

`hiagentresearch loops-all` runs groups in `orchestration.execution_waves` so parents finish before children that inherit from them.

Registry columns on `experiments` record `lineage_mode`, `lineage_parent_group_id`, `lineage_anchor_sha`, and `lineage_anchor_policy` for dashboard tooltips.
