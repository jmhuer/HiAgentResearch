"""Seed a local registry with a full, realistic lineage for offline dashboard preview.

Produces: an L0 baseline, two baseline branches (model_architecture, data_augmentation),
and the inherit chain optimization_strategy -> hyperparameter_optimization -> polish_code.
The polish runs deliberately regress on the final loop to prove the dashboard stars the
LATEST polish commit (top_commit_policy=last_commit), not the best one.

Usage: .venv/bin/python scripts/seed_preview_registry.py <state_dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from hiagentresearch.src.registry.store import Registry


def _run(registry, *, run_id, group, branch, acc, latency, sha, loop, lineage=None):
    registry.record_run(
        run_id=run_id,
        group_id=group,
        branch=branch,
        status="finished",
        failure_class="none",
        metrics={"accuracy": acc, "latency_ms": latency},
        commit_sha=sha,
    )
    manifest = {"group_id": group, "loop_index": loop, "goal": f"{group} loop {loop}"}
    if lineage:
        manifest.update(lineage)
    registry.record_cycle_manifest(
        run_id=run_id,
        manifest_path=f".hiagentresearch/cycles/{group}/{run_id}.json",
        manifest=manifest,
    )
    registry.record_research_outcome(
        run_id=run_id,
        outcome={"research_outcome": "met_targets" if acc >= 0.95 else "below_targets", "next_action": "continue"},
    )


def main(state_dir: Path) -> None:
    registry = Registry(state_dir)
    registry.init()

    # L0 frozen baseline (now stored as a first-class run row).
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.880, "latency_ms": 12.0})

    # Baseline-mode branches.
    _run(registry, run_id="gh_m1", group="model_architecture", branch="research/model-architecture", acc=0.901, latency=10.5, sha="modelsha1", loop=1)
    _run(registry, run_id="gh_m2", group="model_architecture", branch="research/model-architecture", acc=0.942, latency=10.2, sha="modelsha2", loop=2)
    _run(registry, run_id="gh_m3", group="model_architecture", branch="research/model-architecture", acc=0.921, latency=10.9, sha="modelsha3", loop=3)
    _run(registry, run_id="gh_d1", group="data_augmentation", branch="research/data-augmentation", acc=0.911, latency=11.0, sha="dssha1", loop=1)

    # Inherit chain. optimization inherits model_architecture's best (modelsha2 @ L2).
    opt_lineage = {
        "lineage_mode": "inherit",
        "lineage_parent_group_id": "model_architecture",
        "lineage_anchor_sha": "modelsha2",
        "lineage_parent_anchor_step": 2,
    }
    _run(registry, run_id="gh_o1", group="optimization_strategy", branch="research/optimization-strategy", acc=0.951, latency=9.8, sha="optsha1", loop=1, lineage=opt_lineage)

    hyper_lineage = {
        "lineage_mode": "inherit",
        "lineage_parent_group_id": "optimization_strategy",
        "lineage_anchor_sha": "optsha1",
        "lineage_parent_anchor_step": 3,
    }
    _run(registry, run_id="gh_h1", group="hyperparameter_optimization", branch="research/hyperparameter-optimization", acc=0.971, latency=9.6, sha="hypersha1", loop=1, lineage=hyper_lineage)
    _run(registry, run_id="gh_h2", group="hyperparameter_optimization", branch="research/hyperparameter-optimization", acc=0.964, latency=9.7, sha="hypersha2", loop=2, lineage=hyper_lineage)

    # Polish inherits the BEST hyperparam commit (hypersha1) but stars its LATEST run.
    polish_lineage = {
        "lineage_mode": "inherit",
        "lineage_parent_group_id": "hyperparameter_optimization",
        "lineage_anchor_sha": "hypersha1",
        "lineage_parent_anchor_step": 4,
    }
    _run(registry, run_id="gh_p1", group="polish_code", branch="research/polish-code", acc=0.970, latency=9.5, sha="polishsha1", loop=1, lineage=polish_lineage)
    # Final polish loop regresses on accuracy — it must still be the starred top commit.
    _run(registry, run_id="gh_p2", group="polish_code", branch="research/polish-code", acc=0.965, latency=9.4, sha="polishsha2", loop=2, lineage=polish_lineage)

    print(f"seeded registry at {state_dir / 'evals.db'}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else ".hiagentresearch/state"))
