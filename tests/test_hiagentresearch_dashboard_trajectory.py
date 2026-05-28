from hiagentresearch.src.dashboard.trajectory import (
    assign_trajectory_positions,
    baseline_metric_points,
    parent_anchor_loop_index,
)


def test_parallel_baseline_groups_count_loops_independently() -> None:
    topology = {
        "groups": {
            "model_architecture": {"mode": "baseline"},
            "data_augmentation": {"mode": "baseline"},
        },
        "execution_waves": [["model_architecture", "data_augmentation"]],
    }
    points = [
        {"group_id": "model_architecture", "loop_index": 1, "metric_value": 0.86},
        {"group_id": "data_augmentation", "loop_index": 1, "metric_value": 0.93},
    ]
    positioned = assign_trajectory_positions(points, topology)
    by_group = {row["group_id"]: row["trajectory_x"] for row in positioned}
    assert by_group["model_architecture"] == 1
    assert by_group["data_augmentation"] == 1


def test_inherited_group_offsets_by_parent_anchor_loop_index() -> None:
    topology = {
        "groups": {
            "model_architecture": {"mode": "baseline"},
            "optimization_strategy": {"mode": "inherit", "inherit_from": "model_architecture"},
        },
        "inherit_anchors": {
            "optimization_strategy": {
                "parent_group_id": "model_architecture",
                "parent_anchor_loop_index": 2,
                "commit_sha": "parentsha",
            }
        },
    }
    points = [
        {"group_id": "model_architecture", "loop_index": 2, "metric_value": 0.84},
        {"group_id": "optimization_strategy", "loop_index": 1, "metric_value": 0.88},
    ]
    positioned = assign_trajectory_positions(points, topology)
    by_group = {row["group_id"]: row["trajectory_x"] for row in positioned}
    assert by_group["model_architecture"] == 2
    assert by_group["optimization_strategy"] == 3


def test_parent_anchor_loop_index_resolves_from_parent_runs() -> None:
    experiments = [
        {"group_id": "model_architecture", "run_id": "gh_1", "loop_index": 1},
        {"group_id": "model_architecture", "run_id": "gh_2", "loop_index": 2},
        {
            "group_id": "optimization_strategy",
            "run_id": "run_opt",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "sha_loop2",
        },
    ]
    runs = [
        {"run_id": "gh_1", "commit_sha": "sha_loop1"},
        {"run_id": "gh_2", "commit_sha": "sha_loop2"},
        {"run_id": "run_opt", "commit_sha": "optsha"},
    ]
    assert (
        parent_anchor_loop_index(
            parent_group_id="model_architecture",
            commit_sha="sha_loop2",
            experiments=experiments,
            runs=runs,
        )
        == 2
    )


def test_trajectory_axis_includes_l0_when_baseline_present() -> None:
    topology = {
        "groups": {"model_architecture": {"mode": "baseline"}},
        "execution_waves": [["model_architecture"]],
        "baseline_snapshot": {"ref": "main", "metrics": {"accuracy": 0.81}},
    }
    points = [
        {"group_id": "model_architecture", "loop_index": 1, "metric_value": 0.9, "is_baseline_anchor": False},
        {
            "group_id": "model_architecture",
            "loop_index": 0,
            "metric_value": 0.81,
            "is_baseline_anchor": True,
            "trajectory_x": 0,
        },
    ]
    positioned = assign_trajectory_positions(points, topology)
    trajectory_values = sorted({row["trajectory_x"] for row in positioned})
    assert trajectory_values[0] == 0


def test_baseline_anchor_points_use_l0() -> None:
    anchors = baseline_metric_points(
        metric_name="accuracy",
        group_ids=["model_architecture", "data_augmentation"],
        baseline_snapshot={"ref": "main", "metrics": {"accuracy": 0.81}},
    )
    assert len(anchors) == 2
    assert all(point["trajectory_x"] == 0 for point in anchors)
    assert all(point["is_baseline_anchor"] for point in anchors)


def test_assign_trajectory_positions_keeps_baseline_at_l0() -> None:
    topology = {
        "groups": {"model_architecture": {"mode": "baseline"}},
        "execution_waves": [["model_architecture"]],
        "baseline_snapshot": {"ref": "main", "metrics": {"accuracy": 0.81}},
    }
    points = [
        {
            "group_id": "model_architecture",
            "loop_index": 0,
            "metric_value": 0.81,
            "is_baseline_anchor": True,
        },
        {"group_id": "model_architecture", "loop_index": 1, "metric_value": 0.9},
    ]
    positioned = assign_trajectory_positions(points, topology)
    baseline = next(row for row in positioned if row.get("is_baseline_anchor"))
    assert baseline["trajectory_x"] == 0
