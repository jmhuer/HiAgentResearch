import sqlite3

from hiagentresearch.src.core.models import IntentPacket
from hiagentresearch.src.registry.store import SCHEMA_VERSION, Registry


def test_registry_init_and_intent_packet(tmp_path) -> None:
    registry = Registry(tmp_path)
    registry.init()
    assert registry.schema_version() == SCHEMA_VERSION
    packet = IntentPacket(
        group_id="model_architecture",
        active_hypothesis_id="h1",
        hypothesis_text="test",
        attempt_count=1,
        last_failure_class="none",
        next_action="continue",
    )
    registry.write_intent_packet(packet)
    loaded = registry.read_intent_packet("model_architecture")
    assert loaded is not None
    assert loaded.active_hypothesis_id == "h1"


def test_registry_record_run(tmp_path) -> None:
    registry = Registry(tmp_path)
    registry.init()
    registry.record_run(
        run_id="run_abc",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.99, "latency_ms": 12.1},
        correlation_id="corr-1",
    )
    registry.record_run(
        run_id="run_abc",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.991, "latency_ms": 12.0},
        correlation_id="corr-1",
    )
    latest = registry.latest_run("model_architecture")
    assert latest is not None
    assert latest["correlation_id"] == "corr-1"
    assert registry.metrics_for_run("run_abc") == {"accuracy": 0.991, "latency_ms": 12.0}


def test_registry_lineage_winners_round_trip(tmp_path) -> None:
    registry = Registry(tmp_path)
    registry.init()
    payload = {
        "updated_at": "2026-06-02T00:00:00+00:00",
        "lineage_winners": {
            "model_architecture": {
                "lineage_id": "model_architecture",
                "winner_commit_sha": "abc123",
            }
        },
    }
    registry.write_lineage_winners(payload)
    assert registry.lineage_winners() == payload


def test_registry_records_research_outcome_and_experiment(tmp_path) -> None:
    registry = Registry(tmp_path)
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
            "research_outcome": "met_targets",
            "next_action": "continue",
            "reason": "ok",
        },
    )
    registry.record_experiment_manifest(
        run_id="run_abc",
        manifest_path=".hiagentresearch/experiments/model_architecture/run_abc.json",
        manifest={
            "run_id": "run_abc",
            "group_id": "model_architecture",
            "branch": "research/model-architecture",
            "loop_index": 1,
            "hypothesis_id": "h1",
            "hypothesis": "Try a model change.",
            "target_files": ["mnist/src/model.py"],
            "planned_code_changes": ["Edit model.py"],
        },
    )

    outcome = registry.outcome_for_run("run_abc")
    experiment = registry.experiment_for_run("run_abc")
    summary = registry.group_summary()

    assert outcome is not None
    assert outcome["research_outcome"] == "met_targets"
    assert experiment is not None
    assert experiment["target_files"] == ["mnist/src/model.py"]
    assert summary[0]["research_outcome"] == "met_targets"
    assert summary[0]["accuracy"] == 0.99


def test_registry_v4_migrates_existing_database(tmp_path) -> None:
    db_path = tmp_path / "evals.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            commit_sha TEXT,
            workflow_run_id TEXT,
            correlation_id TEXT DEFAULT '',
            status TEXT NOT NULL,
            failure_class TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE metrics (
            run_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE TABLE transitions (run_id TEXT, group_id TEXT, from_state TEXT, to_state TEXT, reason TEXT, actor TEXT, timestamp TEXT)"
    )
    conn.execute(
        "CREATE TABLE artifacts (run_id TEXT, artifact_path TEXT, artifact_type TEXT, sha256 TEXT, size_bytes INTEGER, created_at TEXT, PRIMARY KEY (run_id, artifact_path))"
    )
    conn.execute("CREATE TABLE intent_packets (group_id TEXT PRIMARY KEY, payload_json TEXT, updated_at TEXT)")
    conn.execute(
        """
        INSERT INTO runs
        (run_id, group_id, branch, status, failure_class, created_at)
        VALUES ('gh_1', 'model_architecture', 'research/model-architecture', 'finished', 'none', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()

    registry = Registry(tmp_path)
    registry.init()

    assert registry.schema_version() == SCHEMA_VERSION


def test_registry_records_artifact(tmp_path) -> None:
    registry = Registry(tmp_path)
    registry.init()
    artifact = tmp_path / "metrics.json"
    artifact.write_text('{"tests_passed": 1}', encoding="utf-8")

    registry.record_artifact(
        run_id="run_abc",
        artifact_path=artifact,
        artifact_type="local_eval",
        base_dir=tmp_path,
    )

    artifacts = registry.artifacts_for_run("run_abc")
    assert artifacts[0]["artifact_path"] == "metrics.json"
    assert artifacts[0]["size_bytes"] > 0
