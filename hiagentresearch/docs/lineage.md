# Research lineage

Each research group declares how its branch is bootstrapped:

- `baseline` — create or continue from `orchestration.baseline_ref` (default `main`).
- `inherit` — create from a parent group's anchor commit (`last_commit` or `best_commit`).
- `force` — reserved for Phase 2 promotion; not implemented yet.

`hiagentresearch loops-all` runs groups in `orchestration.execution_waves` so parents finish before children that inherit from them.

Registry columns on `experiments` record `lineage_mode`, `lineage_parent_group_id`, `lineage_anchor_sha`, and `lineage_anchor_policy` for dashboard tooltips.

The dashboard plots **lineage trajectory** on a shared research axis where **L*n* counts accepted agent loops to reach that state**:

- **L0** — frozen-eval baseline on `orchestration.baseline_ref` (recorded in the registry when `loops-all` or `dashboard build` runs). Shown only on `baseline`-mode group series, not on inherited children.
- **Baseline groups** — loop *k* is plotted at **L*k*** (parallel branches such as `model_architecture` and `data_augmentation` each count their own loops from L1).
- **Inherited groups** — loop *k* is plotted at **L(parent_anchor_loop_index + k)** where `parent_anchor_loop_index` is the parent group’s `loop_index` at the inherited `lineage_anchor_sha` commit. Example: inherit after parent loop 2 → child loop 1 appears at **L3**, not L4.
- **Connectors** — child series includes a solid segment from the parent anchor point (matching `lineage_anchor_sha`), not from L0 or the parent’s latest loop by default.

Inheritance anchors come from ingested GitHub eval runs (`gh_*` + `commit_sha`). `best_commit` selects the highest metric among those canonical runs; it does not fall back to “latest local run.” When a child wave starts, inherit-mode branches are hard-reset to the resolved anchor if they already exist (clean worktree required). Threshold `markLine`s use a neutral color separate from series lines.
