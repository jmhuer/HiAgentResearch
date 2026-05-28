# Research lineage

Each research group declares how its branch is bootstrapped:

- `baseline` — create or continue from `orchestration.baseline_ref` (default `main`).
- `inherit` — create from a parent group's best point on its **trajectory** (`last_commit` or `best_commit`).
- `force` — reserved for Phase 2 promotion; not implemented yet.

`hiagentresearch loops-all` runs groups in `orchestration.execution_waves` so parents finish before children that inherit from them.

Registry columns on `experiments` record `lineage_mode`, `lineage_parent_group_id`, `lineage_anchor_sha`, `lineage_anchor_policy`, and `lineage_parent_anchor_step` for dashboard tooltips.

## Trajectory model (one axis for everyone)

Every group has the same lineage axis: **how many accepted agent loops to reach a state**.

| Step | Meaning |
|------|---------|
| **L0** | Frozen eval on `orchestration.baseline_ref` (`main`). Recorded in the registry at `loops-all` / dashboard build. |
| **L1…L*k*** | Research loop *k* on that group's branch (GitHub eval on pushed commits). |

Parallel baseline branches (`model_architecture`, `data_augmentation`) each have their own L0→L1→… timeline. Inherited children continue the parent's step count.

**Child position:** loop *k* is plotted at **L(parent_trajectory_step + k)** where `parent_trajectory_step` is the parent's anchor step (0 for L0 baseline, 1 for loop 1, etc.).

## `best_commit` inheritance

`best_commit` picks the **best metric on the parent's full trajectory**, not only GitHub loop runs:

1. **L0** — frozen baseline metrics from `baseline_snapshot`
2. **L1+** — best ingested `gh_*` run on the parent branch

Whichever wins sets `lineage_anchor_sha` (`main` for L0, parent commit otherwise) and `lineage_parent_anchor_step` (0, 1, 2, …). The dashboard connector attaches at that parent **L** step.

Example: baseline 0.949 at L0 beats model-arch loop 1 at 0.935 → optimization inherits `main` at step 0 → optimization loop 1 plots at **L1** (0+1), with a connector from parent **L0**.

When a child wave starts, inherit-mode branches hard-reset to the resolved anchor if they already exist (clean worktree required).
