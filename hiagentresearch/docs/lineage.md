# Research lineage

Each research group declares how its branch is bootstrapped:

- `baseline` — create or continue from `orchestration.baseline_ref` (default `main`).
- `inherit` — create from a parent group's anchor commit (`last_commit` or `best_commit`).
- `force` — reserved for Phase 2 promotion; not implemented yet.

`hiagentresearch loops-all` runs groups in `orchestration.execution_waves` so parents finish before children that inherit from them.

Registry columns on `experiments` record `lineage_mode`, `lineage_parent_group_id`, `lineage_anchor_sha`, and `lineage_anchor_policy` for dashboard tooltips.

The dashboard plots **lineage trajectory** on a shared research axis:

- **L0** — frozen-eval baseline on `orchestration.baseline_ref` (recorded in the registry when `loops-all` or `dashboard build` runs). Shown as a diamond marker per group series.
- **L1+** — loop results positioned by `orchestration.execution_waves`. Groups in the same wave with the same `loop_index` share the same x position (parallel wave-1 baselines align).
- **Inherited groups** — continue after the deepest loop completed in all prior waves. The child series includes a solid connector segment (child color) from the parent run whose `commit_sha` matches the child’s recorded `lineage_anchor_sha` (`best_commit` / `last_commit` bootstrap), not from L0 baseline or the parent’s latest loop by default.

Inheritance anchors come from ingested GitHub eval runs (`gh_*` + `commit_sha`). `best_commit` selects the highest metric among those canonical runs; it does not fall back to “latest local run.” When a child wave starts, inherit-mode branches are hard-reset to the resolved anchor if they already exist (clean worktree required). Threshold `markLine`s use a neutral color separate from series lines.
