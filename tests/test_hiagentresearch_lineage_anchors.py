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
    registry.record_cycle_manifest(
        run_id="gh_1",
        manifest_path=".hiagentresearch/cycles/model_architecture/gh_1.json",
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


def test_best_trajectory_anchor_minimize_picks_lowest(monkeypatch, tmp_path) -> None:
    """For a lower-is-better metric (minimize=True), 'best' is the LOWEST value — the
    direction is generic, supplied by the caller, not a hardcoded metric name."""
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"latency_ms": 10.0})
    for run_id, sha, latency, loop in (
        ("gh_1", "slow", 12.0, 1),
        ("gh_2", "fast", 6.0, 2),
        ("gh_3", "mid", 8.0, 3),
    ):
        registry.record_run(
            run_id=run_id,
            group_id="model_architecture",
            branch="research/model-architecture",
            status="finished",
            failure_class="none",
            metrics={"latency_ms": latency},
            commit_sha=sha,
        )
        registry.record_cycle_manifest(
            run_id=run_id,
            manifest_path=f".hiagentresearch/cycles/model_architecture/{run_id}.json",
            manifest={"group_id": "model_architecture", "loop_index": loop},
        )

    git = GitService(tmp_path)
    # maximize (wrong direction) would crown the slowest commit ...
    worst = best_trajectory_anchor(
        parent_group_id="model_architecture", anchor_metric="latency_ms",
        baseline_ref="main", registry=registry, git=git, minimize=False,
    )
    assert worst.ref == "slow" and worst.metric_value == 12.0
    # ... minimize correctly crowns the fastest.
    best = best_trajectory_anchor(
        parent_group_id="model_architecture", anchor_metric="latency_ms",
        baseline_ref="main", registry=registry, git=git, minimize=True,
    )
    assert best.ref == "fast"
    assert best.metric_value == 6.0


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
    registry.record_cycle_manifest(
        run_id="gh_opt_1",
        manifest_path=".hiagentresearch/cycles/optimization_strategy/gh_opt_1.json",
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


def test_inherit_from_select_collapse_resolves_metric_via_adopted_leaf(monkeypatch, tmp_path) -> None:
    """Inheriting from a select collapse: the named parent (the collapse) owns no commit/metric —
    its result IS the adopted leaf's commit. The origin must resolve the inherited commit's metric
    under the recorded owner (the leaf), so the trajectory step is the inherited step + the loop
    (L2), not a baseline fallback (which would wrongly collapse it to L1)."""
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.80})
    # The adopted leaf owns the commit ah2 and its metric — the select collapse has no run.
    registry.record_run(
        run_id="gh_arch_h2", group_id="architecture__a2", branch="research/architecture-a2",
        status="finished", failure_class="none", metrics={"accuracy": 0.93}, commit_sha="ah2",
    )
    registry.record_cycle_manifest(
        run_id="gh_arch_h2",
        manifest_path=".hiagentresearch/cycles/architecture__a2/gh_arch_h2.json",
        manifest={"group_id": "architecture__a2", "loop_index": 1},
    )
    # optimization__a2 inherits FROM the collapse (parent), but the commit is OWNED by the leaf.
    registry.record_cycle_manifest(
        run_id="gh_opt_h2",
        manifest_path=".hiagentresearch/cycles/optimization__a2/gh_opt_h2.json",
        manifest={
            "group_id": "optimization__a2",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "architecture__collapse",
            "lineage_anchor_sha": "ah2",
            "lineage_anchor_source_group": "architecture__a2",
            "lineage_parent_anchor_step": 1,
        },
    )
    registry.record_run(
        run_id="gh_opt_h2", group_id="optimization__a2", branch="research/optimization-a2",
        status="finished", failure_class="none", metrics={"accuracy": 0.96}, commit_sha="oh2",
    )
    anchor = best_trajectory_anchor(
        parent_group_id="optimization__a2",
        anchor_metric="accuracy",
        baseline_ref="main",
        registry=registry,
        git=GitService(tmp_path),
    )
    assert anchor is not None
    # Its own loop (oh2) is the best — at the inherited step (1) + its loop (1) = L2, not L1.
    assert anchor.ref == "oh2"
    assert anchor.trajectory_step == 2
    assert anchor.metric_value == 0.96
