# Agent Skeletons (Phase 1)

These are role contracts, not fully autonomous implementations yet.

- `planner.md` defines hypothesis planning output contract.
- `group_runner.md` defines execution-cycle behavior.
- `evaluator_adapter.md` defines eval normalization contract.
- `results_ingester.md` defines registry-write and lifecycle event behavior.

All roles must emit structured artifacts and avoid free-form "done" claims without evidence.
