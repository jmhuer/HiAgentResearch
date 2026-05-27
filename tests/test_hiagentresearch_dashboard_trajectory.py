from hiagentresearch.src.dashboard.trajectory import assign_trajectory_positions, baseline_metric_points, wave_depths


def test_parallel_wave_one_groups_share_trajectory_for_loop_one() -> None:
    topology = {
        "execution_waves": [
            ["model_architecture", "data_augmentation"],
            ["optimization_strategy"],
            ["hyperparameter_optimization"],
        ],
        "chains": [
            ["model_architecture", "optimization_strategy", "hyperparameter_optimization"],
            ["data_augmentation"],
        ],
    }
    points = [
        {"group_id": "model_architecture", "loop_index": 1, "metric_value": 0.86},
        {"group_id": "data_augmentation", "loop_index": 1, "metric_value": 0.93},
    ]
    positioned = assign_trajectory_positions(points, topology)
    by_group = {row["group_id"]: row["trajectory_x"] for row in positioned}
    assert by_group["model_architecture"] == 1
    assert by_group["data_augmentation"] == 1


def test_inherited_group_starts_after_prior_wave_depth() -> None:
    topology = {
        "execution_waves": [
            ["model_architecture", "data_augmentation"],
            ["optimization_strategy"],
        ],
        "chains": [["model_architecture", "optimization_strategy"]],
    }
    points = [
        {"group_id": "model_architecture", "loop_index": 2, "metric_value": 0.84},
        {"group_id": "data_augmentation", "loop_index": 1, "metric_value": 0.93},
        {"group_id": "optimization_strategy", "loop_index": 1, "metric_value": 0.88},
    ]
    positioned = assign_trajectory_positions(points, topology)
    by_group = {row["group_id"]: row["trajectory_x"] for row in positioned}
    assert by_group["model_architecture"] == 2
    assert by_group["data_augmentation"] == 1
    assert by_group["optimization_strategy"] == 3
    assert wave_depths(points, topology["execution_waves"]) == [0, 2]


def test_trajectory_axis_includes_l0_when_baseline_present() -> None:
    topology = {
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
