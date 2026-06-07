import json

import hiagentresearch.src.github.ingest as gh_ingest
from hiagentresearch.src.core.artifacts import eval_node_index_names


def _write_required_artifacts(artifact_dir, *, metrics: str = '{"tests_passed": 1}') -> None:
    (artifact_dir / "metrics.json").write_text(metrics, encoding="utf-8")
    (artifact_dir / "failure_class.json").write_text(
        '{"failure_class": "none", "exit_code": 0}', encoding="utf-8"
    )
    (artifact_dir / "research_outcome.json").write_text(
        '{"research_outcome": "met_targets", "next_action": "continue", "reason": "ok"}',
        encoding="utf-8",
    )
    (artifact_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "run_id": "run_local",
                "correlation_id": "corr-1",
                "commit_sha": "abc",
                "workflow_run_id": "123",
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "stdout.txt").write_text("{}", encoding="utf-8")
    (artifact_dir / "stderr.txt").write_text("", encoding="utf-8")
    (artifact_dir / "parsed_eval.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "cycle_manifest.json").write_text(
        json.dumps(
            {
                "group_id": "model_architecture",
                "branch": "research/model-architecture",
                "loop_index": 1,
                "goal_id": "h1",
                "goal": "Try a clean dashboard-ready cycle.",
                "target_files": ["mnist/src/model.py"],
                "planned_code_changes": ["Edit model.py"],
                "lineage_baseline_snapshot": {
                    "ref": "main",
                    "metrics": {"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
                },
            }
        ),
        encoding="utf-8",
    )


def test_ingest_rejects_missing_required_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gh_ingest, "STATE_DIR", tmp_path / "state")

    assert gh_ingest.ingest("gh_1", "model_architecture", "research/model-architecture", tmp_path) == 1


def test_ingest_records_artifacts_and_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gh_ingest, "STATE_DIR", tmp_path / "state")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    _write_required_artifacts(artifact_dir)

    assert gh_ingest.ingest("gh_1", "model_architecture", "research/model-architecture", artifact_dir) == 0
    assert gh_ingest.ingest("gh_1", "model_architecture", "research/model-architecture", artifact_dir) == 0

    registry = gh_ingest.Registry(tmp_path / "state")
    registry.init()
    assert registry.metrics_for_run("gh_1") == {"tests_passed": 1.0}
    assert registry.outcome_for_run("gh_1")["research_outcome"] == "met_targets"
    assert registry.cycle_for_run("gh_1")["goal_id"] == "h1"
    assert registry.baseline_snapshot()["metrics"]["accuracy"] == 0.81
    indexed = {artifact["artifact_path"] for artifact in registry.artifacts_for_run("gh_1")}
    assert indexed >= set(eval_node_index_names())


def test_ingest_rejects_malformed_metrics(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gh_ingest, "STATE_DIR", tmp_path / "state")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    _write_required_artifacts(artifact_dir, metrics="{not json")

    assert gh_ingest.ingest("gh_1", "model_architecture", "research/model-architecture", artifact_dir) == 1


def test_ingest_records_baseline_node_metrics(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gh_ingest, "STATE_DIR", tmp_path / "state")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    _write_required_artifacts(artifact_dir, metrics='{"accuracy": 0.82, "latency_ms": 12.0}')
    meta_path = artifact_dir / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({"node_kind": "baseline", "baseline_ref": "main"})
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert gh_ingest.ingest("gh_1", "model_architecture", "main", artifact_dir) == 0

    registry = gh_ingest.Registry(tmp_path / "state")
    registry.init()
    assert registry.baseline_snapshot()["ref"] == "main"
    assert registry.baseline_snapshot()["metrics"]["accuracy"] == 0.82


def test_ingest_synthesizes_experiment_when_manifest_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gh_ingest, "STATE_DIR", tmp_path / "state")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    _write_required_artifacts(artifact_dir)
    (artifact_dir / "cycle_manifest.json").unlink()

    assert gh_ingest.ingest("gh_1", "model_architecture", "research/model-architecture", artifact_dir) == 0

    registry = gh_ingest.Registry(tmp_path / "state")
    registry.init()
    cycle = registry.cycle_for_run("gh_1")
    assert cycle is not None
    assert cycle["goal_id"] == "model_architecture-direct-eval"
    assert "missing" in (cycle["goal"] or "").lower()
