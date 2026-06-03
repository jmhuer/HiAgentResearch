import json
import sqlite3
import subprocess

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.dashboard.build import build_from_artifacts, build_from_registry
from hiagentresearch.src.dashboard.cli import main
from hiagentresearch.src.registry.store import Registry


def test_dashboard_build_outputs_sanitized_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "jmhuer/HiAgentResearch")
    state_dir = tmp_path / "state"
    registry = _seed_registry(state_dir)
    artifact = tmp_path / "stdout.txt"
    artifact.write_text("{}", encoding="utf-8")
    registry.record_artifact(run_id="run_abc", artifact_path=artifact, artifact_type="local_eval", base_dir=tmp_path)

    output_dir = tmp_path / "dashboard"
    result = build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())

    assert result.database_path.exists()
    assert (output_dir / "index.html").exists()
    assert (output_dir / "app.js").exists()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dashboard_schema_version"] == 1
    assert manifest["sqlite"]["worker_url"] == "sqlite.worker.js"
    assert manifest["sqlite"]["wasm_url"] == "sql-wasm.wasm"
    assert manifest["repository"]["commit_url_template"] == "https://github.com/jmhuer/HiAgentResearch/commit/{commit_sha}"
    assert manifest["repository"]["workflow_run_url_template"] == "https://github.com/jmhuer/HiAgentResearch/actions/runs/{workflow_run_id}"
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    assert snapshot["metric_names"] == ["accuracy", "latency_ms"]
    assert snapshot["experiments"][0]["hypothesis_id"] == "h1"
    config = load_config()
    accuracy_min = config.evaluation.targets["accuracy"].min
    assert {
        "group_id": "model_architecture",
        "metric_name": "accuracy",
        "min": accuracy_min,
        "max": None,
        "source": "global",
    } in snapshot["metric_targets"]
    assert snapshot["lineage_topology"]["chains"] == [
        ["model_architecture", "optimization_strategy", "hyperparameter_optimization", "polish_code"],
        ["data_augmentation"],
    ]
    assert snapshot["lineage_topology"]["execution_waves"] == [
        ["model_architecture", "data_augmentation"],
        ["optimization_strategy"],
        ["hyperparameter_optimization"],
        ["polish_code"],
    ]
    assert all("trajectory_x" in row for row in snapshot["metrics"])

    conn = sqlite3.connect(result.database_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM metric_series").fetchone()[0] == 2
        assert (
            conn.execute(
                """
                SELECT min_value
                FROM metric_expectations
                WHERE group_id = 'model_architecture' AND metric_name = 'accuracy'
                """
            ).fetchone()[0]
            == accuracy_min
        )
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'intent_packets'").fetchone() is None
    finally:
        conn.close()


def test_dashboard_summary_includes_baseline_snapshot(tmp_path) -> None:
    state_dir = tmp_path / "state"
    _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())

    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    baseline = snapshot["lineage_topology"]["baseline_snapshot"]
    assert baseline["metrics"]["accuracy"] == 0.81
    assert summary["lineage_topology"]["baseline_snapshot"]["metrics"]["accuracy"] == 0.81
    assert "optimization_strategy" in summary["lineage_topology"]["inherit_anchors"]
    anchors = [row for row in snapshot["metrics"] if row.get("is_baseline_anchor")]
    assert anchors
    assert all(row["trajectory_x"] == 0 for row in anchors)


def test_dashboard_skips_l0_baseline_for_inherit_groups(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="gh_opt",
        group_id="optimization_strategy",
        branch="research/optimization-strategy",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.88, "latency_ms": 8.0},
        commit_sha="optsha",
    )
    registry.record_experiment_manifest(
        run_id="gh_opt",
        manifest_path=".hiagentresearch/experiments/optimization_strategy/gh_opt.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "parentsha",
            "lineage_anchor_policy": "best_commit",
        },
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    opt_baselines = [
        row
        for row in snapshot["metrics"]
        if row.get("group_id") == "optimization_strategy" and row.get("is_baseline_anchor")
    ]
    model_baselines = [
        row
        for row in snapshot["metrics"]
        if row.get("group_id") == "model_architecture" and row.get("is_baseline_anchor")
    ]
    assert not opt_baselines
    assert model_baselines


def test_dashboard_inherit_anchor_uses_resolved_best_commit(tmp_path, monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.949, "latency_ms": 6.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="gh_loop1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.914, "latency_ms": 8.0},
        commit_sha="loop1sha",
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    topology = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))["lineage_topology"]
    anchor = topology["inherit_anchors"]["optimization_strategy"]
    assert anchor["commit_sha"] == "mainsha"
    assert anchor["parent_trajectory_step"] == 0


def test_dashboard_topology_includes_inherit_anchors(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="gh_parent",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.95, "latency_ms": 10.0},
        commit_sha="parentsha",
    )
    registry.record_experiment_manifest(
        run_id="gh_parent",
        manifest_path=".hiagentresearch/experiments/model_architecture/gh_parent.json",
        manifest={
            "group_id": "model_architecture",
            "loop_index": 1,
            "hypothesis_id": "parent",
            "hypothesis": "Parent loop",
            "target_files": ["mnist/src/model.py"],
            "planned_code_changes": ["Edit model.py"],
        },
    )
    registry.record_experiment_manifest(
        run_id="run_child",
        manifest_path=".hiagentresearch/experiments/optimization_strategy/run_child.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "parentsha",
            "lineage_anchor_policy": "best_commit",
        },
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    topology = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))["lineage_topology"]
    anchor = topology["inherit_anchors"]["optimization_strategy"]
    assert anchor["commit_sha"] == "parentsha"
    assert anchor["parent_anchor_loop_index"] == 1


def test_dashboard_resolves_hyperparameter_anchor_from_optimization_origin(tmp_path, monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.80, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="gh_model_2",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.95, "latency_ms": 10.0},
        commit_sha="parentsha",
    )
    registry.record_experiment_manifest(
        run_id="gh_opt_1",
        manifest_path=".hiagentresearch/experiments/optimization_strategy/gh_opt_1.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "parentsha",
            "lineage_parent_anchor_step": 2,
            "lineage_anchor_policy": "best_commit",
        },
    )
    registry.record_run(
        run_id="gh_opt_1",
        group_id="optimization_strategy",
        branch="research/optimization-strategy",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.91, "latency_ms": 9.8},
        commit_sha="optloop1sha",
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    topology = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))["lineage_topology"]
    anchor = topology["inherit_anchors"]["hyperparameter_optimization"]
    assert anchor["commit_sha"] == "parentsha"
    assert anchor["parent_trajectory_step"] == 2
    # The anchor commit belongs to model_architecture (the grandparent peak), so the
    # dashboard must attribute the connector there, not to optimization_strategy.
    assert anchor["anchor_source_group"] == "model_architecture"


def test_dashboard_lineage_winners_include_polish_last_commit_and_row_flags(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = Registry(state_dir)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.80, "latency_ms": 50.0, "duration_sec": 1.0})
    registry.record_run(
        run_id="gh_data_1",
        group_id="data_augmentation",
        branch="research/data-augmentation",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.85, "latency_ms": 9.8},
        commit_sha="datasha",
    )
    registry.record_experiment_manifest(
        run_id="gh_data_1",
        manifest_path=".hiagentresearch/experiments/data_augmentation/gh_data_1.json",
        manifest={"group_id": "data_augmentation", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_model_1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.90, "latency_ms": 9.9},
        commit_sha="modelsha",
    )
    registry.record_experiment_manifest(
        run_id="gh_model_1",
        manifest_path=".hiagentresearch/experiments/model_architecture/gh_model_1.json",
        manifest={"group_id": "model_architecture", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_opt_1",
        group_id="optimization_strategy",
        branch="research/optimization-strategy",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.91, "latency_ms": 9.7},
        commit_sha="optsha",
    )
    registry.record_experiment_manifest(
        run_id="gh_opt_1",
        manifest_path=".hiagentresearch/experiments/optimization_strategy/gh_opt_1.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "modelsha",
            "lineage_anchor_policy": "best_commit",
            "lineage_parent_anchor_step": 1,
            "lineage_anchor_source_group": "model_architecture",
        },
    )
    registry.record_run(
        run_id="gh_hyper_1",
        group_id="hyperparameter_optimization",
        branch="research/hyperparameter-optimization",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.92, "latency_ms": 9.6},
        commit_sha="hypersha",
    )
    registry.record_experiment_manifest(
        run_id="gh_hyper_1",
        manifest_path=".hiagentresearch/experiments/hyperparameter_optimization/gh_hyper_1.json",
        manifest={
            "group_id": "hyperparameter_optimization",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "optimization_strategy",
            "lineage_anchor_sha": "optsha",
            "lineage_anchor_policy": "best_commit",
            "lineage_parent_anchor_step": 2,
            "lineage_anchor_source_group": "optimization_strategy",
        },
    )
    registry.record_run(
        run_id="gh_polish_1",
        group_id="polish_code",
        branch="research/polish-code",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.89, "latency_ms": 9.5},
        commit_sha="polishold",
    )
    registry.record_experiment_manifest(
        run_id="gh_polish_1",
        manifest_path=".hiagentresearch/experiments/polish_code/gh_polish_1.json",
        manifest={
            "group_id": "polish_code",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "hyperparameter_optimization",
            "lineage_anchor_sha": "hypersha",
            "lineage_anchor_policy": "last_commit",
            "lineage_parent_anchor_step": 3,
            "lineage_anchor_source_group": "hyperparameter_optimization",
        },
    )
    registry.record_run(
        run_id="gh_polish_2",
        group_id="polish_code",
        branch="research/polish-code",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.88, "latency_ms": 9.4},
        commit_sha="polishnew",
    )
    registry.record_experiment_manifest(
        run_id="gh_polish_2",
        manifest_path=".hiagentresearch/experiments/polish_code/gh_polish_2.json",
        manifest={
            "group_id": "polish_code",
            "loop_index": 2,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "hyperparameter_optimization",
            "lineage_anchor_sha": "hypersha",
            "lineage_anchor_policy": "last_commit",
            "lineage_parent_anchor_step": 3,
            "lineage_anchor_source_group": "hyperparameter_optimization",
        },
    )

    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    topology = snapshot["lineage_topology"]
    winners = topology["lineage_winners"]
    assert winners["model_architecture"]["winner_commit_sha"] == "polishnew"
    assert winners["model_architecture"]["leaf_group_id"] == "polish_code"
    assert winners["data_augmentation"]["leaf_group_id"] == "data_augmentation"
    row = next(row for row in snapshot["metrics"] if row.get("run_id") == "gh_polish_2")
    assert row["is_group_policy_winner"] is True
    assert row["is_lineage_winner"] is True
    anchor_row = next(row for row in snapshot["metrics"] if row.get("run_id") == "gh_hyper_1")
    assert anchor_row["is_inherit_anchor"] is True
    assert "polish_code" in anchor_row["inherit_anchor_for_groups"]


def test_lineage_winner_after_wave_one_uses_model_not_unrun_inherit_children(tmp_path) -> None:
    """Wave-1-only registry: model chain winner must not jump to baseline-only inherit groups."""
    state_dir = tmp_path / "state"
    registry = Registry(state_dir)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.879, "latency_ms": 50.0, "duration_sec": 1.0})
    for run_id, acc, sha, loop in (
        ("gh_m1", 0.861, "sha1", 1),
        ("gh_m2", 0.923, "sha2", 2),
        ("gh_m3", 0.815, "sha3", 3),
    ):
        registry.record_run(
            run_id=run_id,
            group_id="model_architecture",
            branch="research/model-architecture",
            status="finished",
            failure_class="none",
            metrics={"accuracy": acc, "latency_ms": 10.0},
            commit_sha=sha,
        )
        registry.record_experiment_manifest(
            run_id=run_id,
            manifest_path=f".hiagentresearch/experiments/model_architecture/{run_id}.json",
            manifest={"group_id": "model_architecture", "loop_index": loop},
        )
    registry.record_run(
        run_id="gh_d1",
        group_id="data_augmentation",
        branch="research/data-augmentation",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.939, "latency_ms": 10.0},
        commit_sha="dsha1",
    )
    registry.record_experiment_manifest(
        run_id="gh_d1",
        manifest_path=".hiagentresearch/experiments/data_augmentation/gh_d1.json",
        manifest={"group_id": "data_augmentation", "loop_index": 1},
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    winners = snapshot["lineage_topology"]["lineage_winners"]
    assert winners["model_architecture"]["leaf_group_id"] == "model_architecture"
    assert winners["model_architecture"]["winner_commit_sha"] == "sha2"
    model_star = next(
        row
        for row in snapshot["metrics"]
        if row.get("is_lineage_winner") and row.get("group_id") == "model_architecture"
    )
    assert model_star["run_id"] == "gh_m2"
    assert "optimization_strategy" not in snapshot["lineage_topology"]["group_trajectory_winners"]


def test_lineage_winner_uses_effective_leaf_when_configured_leaf_missing(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = Registry(state_dir)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.80, "latency_ms": 50.0, "duration_sec": 1.0})
    registry.record_run(
        run_id="gh_model_1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.90, "latency_ms": 9.9},
        commit_sha="modelsha",
    )
    registry.record_experiment_manifest(
        run_id="gh_model_1",
        manifest_path=".hiagentresearch/experiments/model_architecture/gh_model_1.json",
        manifest={"group_id": "model_architecture", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_hyper_1",
        group_id="hyperparameter_optimization",
        branch="research/hyperparameter-optimization",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.94, "latency_ms": 9.5},
        commit_sha="hypersha",
    )
    registry.record_experiment_manifest(
        run_id="gh_hyper_1",
        manifest_path=".hiagentresearch/experiments/hyperparameter_optimization/gh_hyper_1.json",
        manifest={
            "group_id": "hyperparameter_optimization",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "optimization_strategy",
            "lineage_anchor_sha": "optsha",
            "lineage_anchor_policy": "best_commit",
        },
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    winners = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))["lineage_topology"]["lineage_winners"]
    assert winners["model_architecture"]["leaf_group_id"] == "hyperparameter_optimization"
    assert winners["model_architecture"]["configured_leaf_group_id"] == "polish_code"
    lineage_stars = [
        row
        for row in json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))["metrics"]
        if row.get("is_lineage_winner")
    ]
    assert len(lineage_stars) == 1


def test_dashboard_build_from_artifacts(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts" / "hiagentresearch-123"
    artifact_dir.mkdir(parents=True)
    _write_artifacts(artifact_dir)

    output_dir = tmp_path / "site"
    result = build_from_artifacts(artifact_root=tmp_path / "artifacts", output_dir=output_dir, config=load_config())

    assert result.database_path.exists()
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert snapshot["runs"][0]["run_id"] == "gh_123"
    assert snapshot["experiments"][0]["hypothesis_id"] == "h1"
    assert snapshot["lineage_topology"]["baseline_snapshot"]["metrics"]["accuracy"] == 0.81
    assert summary["metric_targets"]


def test_dashboard_build_from_artifacts_synthesizes_missing_manifest(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts" / "hiagentresearch-456"
    artifact_dir.mkdir(parents=True)
    _write_artifacts(artifact_dir)
    (artifact_dir / "experiment_manifest.json").unlink()

    output_dir = tmp_path / "site"
    build_from_artifacts(artifact_root=tmp_path / "artifacts", output_dir=output_dir, config=load_config())

    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    experiment = snapshot["experiments"][0]
    assert experiment["hypothesis_id"] == "model_architecture-direct-eval"
    assert "missing" in (experiment["hypothesis"] or "").lower()


def test_dashboard_excludes_runs_before_orchestration_session(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = _seed_registry(state_dir)
    conn = sqlite3.connect(registry.db_path)
    try:
        conn.execute(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", "run_abc"),
        )
        conn.commit()
    finally:
        conn.close()
    registry.record_baseline_snapshot(
        ref="main",
        metrics={"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="run_current",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.92, "latency_ms": 11.0},
        correlation_id="run_current",
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    run_ids = {row["run_id"] for row in snapshot["runs"]}
    assert "run_abc" not in run_ids
    assert "run_current" in run_ids
    assert snapshot["orchestration_session"]["started_at"]


def test_dashboard_cli_build(tmp_path, capsys) -> None:
    state_dir = tmp_path / "state"
    _seed_registry(state_dir)

    assert main(["build", "--state-dir", str(state_dir), "--output-dir", str(tmp_path / "site")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["database_path"].endswith("dashboard.db")


def _seed_registry(
    state_dir,
    *,
    with_baseline: bool = False,
    baseline_metrics: dict | None = None,
):
    registry = Registry(state_dir)
    registry.init()
    if with_baseline:
        registry.record_baseline_snapshot(
            ref="main",
            metrics=baseline_metrics
            or {"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
        )
    registry.record_run(
        run_id="run_abc",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.99, "latency_ms": 12.1},
        correlation_id="run_abc",
    )
    registry.record_research_outcome(
        run_id="run_abc",
        outcome={
            "research_outcome": "met_targets",
            "next_action": "continue",
            "reason": "ok",
        },
    )
    registry.record_experiment_manifest(
        run_id="run_abc",
        manifest_path=".hiagentresearch/experiments/model_architecture/run_abc.json",
        manifest=_manifest(),
    )
    return registry


def _write_artifacts(artifact_dir) -> None:
    (artifact_dir / "metrics.json").write_text('{"accuracy": 0.99, "latency_ms": 12.1}', encoding="utf-8")
    (artifact_dir / "failure_class.json").write_text('{"failure_class": "none", "exit_code": 0}', encoding="utf-8")
    (artifact_dir / "research_outcome.json").write_text(
        '{"research_outcome": "met_targets", "next_action": "continue", "reason": "ok"}',
        encoding="utf-8",
    )
    (artifact_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "run_id": "gh_123",
                "group_id": "model_architecture",
                "branch": "research/model-architecture",
                "commit_sha": "abc",
                "workflow_run_id": "123",
                "correlation_id": "run_abc",
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "stdout.txt").write_text("{}", encoding="utf-8")
    (artifact_dir / "stderr.txt").write_text("", encoding="utf-8")
    (artifact_dir / "experiment_manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")


def _manifest() -> dict:
    return {
        "group_id": "model_architecture",
        "branch": "research/model-architecture",
        "loop_index": 1,
        "hypothesis_id": "h1",
        "hypothesis": "Try a dashboard-ready model change.",
        "target_files": ["mnist/src/model.py"],
        "planned_code_changes": ["Edit model.py"],
        "lineage_baseline_snapshot": {
            "ref": "main",
            "metrics": {"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
        },
    }
