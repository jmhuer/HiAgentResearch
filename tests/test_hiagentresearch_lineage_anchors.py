import subprocess

import pytest

from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.lineage.anchors import best_trajectory_anchor
from hiagentresearch.src.registry.store import Registry


def test_best_trajectory_anchor_compares_l0_and_loops(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.95})
    registry.record_run(
        run_id="gh_1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.99},
        commit_sha="betterloop",
    )
    registry.record_experiment_manifest(
        run_id="gh_1",
        manifest_path=".hiagentresearch/experiments/model_architecture/gh_1.json",
        manifest={"group_id": "model_architecture", "loop_index": 1},
    )
    anchor = best_trajectory_anchor(
        parent_group_id="model_architecture",
        anchor_metric="accuracy",
        baseline_ref="main",
        registry=registry,
        git=GitService(tmp_path),
    )
    assert anchor is not None
    assert anchor.ref == "betterloop"
    assert anchor.trajectory_step == 1
    assert anchor.metric_value == 0.99


def test_best_trajectory_anchor_inherit_parent_can_pick_origin_from_grandparent(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.80})
    registry.record_run(
        run_id="gh_model_2",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.95},
        commit_sha="modelbestsha",
    )
    registry.record_experiment_manifest(
        run_id="gh_opt_1",
        manifest_path=".hiagentresearch/experiments/optimization_strategy/gh_opt_1.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "modelbestsha",
            "lineage_parent_anchor_step": 2,
        },
    )
    registry.record_run(
        run_id="gh_opt_1",
        group_id="optimization_strategy",
        branch="research/optimization-strategy",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.91},
        commit_sha="optloop1sha",
    )
    anchor = best_trajectory_anchor(
        parent_group_id="optimization_strategy",
        anchor_metric="accuracy",
        baseline_ref="main",
        registry=registry,
        git=GitService(tmp_path),
    )
    assert anchor is not None
    assert anchor.ref == "modelbestsha"
    assert anchor.trajectory_step == 2
    assert anchor.metric_value == 0.95
