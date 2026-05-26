import json
import sqlite3

from hiagentresearch.src.config import load_config
from hiagentresearch.src.dashboard.build import build_from_artifacts, build_from_registry
from hiagentresearch.src.dashboard.cli import main
from hiagentresearch.src.registry import Registry


def test_dashboard_build_outputs_sanitized_bundle(tmp_path) -> None:
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
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    assert snapshot["metric_names"] == ["accuracy", "latency_ms"]
    assert snapshot["experiments"][0]["hypothesis_id"] == "h1"

    conn = sqlite3.connect(result.database_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM metric_series").fetchone()[0] == 2
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'intent_packets'").fetchone() is None
    finally:
        conn.close()


def test_dashboard_build_from_artifacts(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts" / "hiagentresearch-123"
    artifact_dir.mkdir(parents=True)
    _write_artifacts(artifact_dir)

    output_dir = tmp_path / "site"
    result = build_from_artifacts(artifact_root=tmp_path / "artifacts", output_dir=output_dir, config=load_config())

    assert result.database_path.exists()
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    assert snapshot["runs"][0]["run_id"] == "gh_123"
    assert snapshot["experiments"][0]["hypothesis_id"] == "h1"


def test_dashboard_cli_build(tmp_path, capsys) -> None:
    state_dir = tmp_path / "state"
    _seed_registry(state_dir)

    assert main(["build", "--state-dir", str(state_dir), "--output-dir", str(tmp_path / "site")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["database_path"].endswith("dashboard.db")


def _seed_registry(state_dir):
    registry = Registry(state_dir)
    registry.init()
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
            "research_outcome": "improved_baseline",
            "improved_baseline": True,
            "metrics_ok": True,
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
        '{"research_outcome": "improved_baseline", "improved_baseline": true, "metrics_ok": true, "next_action": "continue", "reason": "ok"}',
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
        "target_files": ["mnist/pipeline/model.py"],
        "planned_code_changes": ["Edit model.py"],
    }
