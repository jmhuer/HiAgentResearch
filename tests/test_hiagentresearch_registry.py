import sqlite3

from hiagentresearch.src.core.models import IntentPacket
from hiagentresearch.src.registry.store import BASELINE_RUN_GROUP, SCHEMA_VERSION, Registry


def test_baseline_run_is_single_source_but_hidden_from_group_lists(tmp_path) -> None:
    """The frozen baseline is stored as a run (single source of truth) yet must not
    surface as an 'unknown' group card or run-detail entry in the dashboard snapshot."""
    registry = Registry(tmp_path)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.88, "latency_ms": 12.0})
    registry.record_run(
        run_id="gh_m1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.91, "latency_ms": 10.0},
        commit_sha="sha1",
    )
    # baseline_snapshot is derived from the run row (single source of truth).
    snap = registry.baseline_snapshot()
    assert snap is not None and snap["metrics"]["accuracy"] == 0.88
    # ...but the sentinel group is absent from group-facing lists.
    dash = registry.dashboard_snapshot()
    assert all(row["group_id"] for row in dash["summary"])
    assert all(row["group_id"] for row in dash["runs"])
    assert BASELINE_RUN_GROUP not in {row["group_id"] for row in dash["summary"]}


def test_mark_session_complete_stamps_without_losing_start(tmp_path) -> None:
    """Marking the session complete adds completed_at (so the dashboard shows the run
    as finished) while preserving started_at."""
    registry = Registry(tmp_path)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.9})
    started = registry.orchestration_session()["started_at"]
    assert "completed_at" not in registry.orchestration_session()  # live
    registry.mark_session_complete()
    session = registry.orchestration_session()
    assert session["started_at"] == started
    assert session["completed_at"]  # complete


def test_baseline_snapshot_records_commit_sha(tmp_path) -> None:
    """The frozen L0 baseline anchors to a real commit (like every other run), so the
    run row carries the resolved SHA and the accessor exposes it for dashboard links."""
    registry = Registry(tmp_path)
    registry.init()
    registry.record_baseline_snapshot(
        ref="main", metrics={"accuracy": 0.9, "latency_ms": 5.0}, commit_sha="deadbeefcafe"
    )
    snap = registry.baseline_snapshot()
    assert snap is not None
    assert snap["commit_sha"] == "deadbeefcafe"
    # ... and the baseline appears among displayable dashboard metrics.
    dash = registry.dashboard_snapshot()
    assert any(row["run_id"] == "baseline:main" for row in dash["metrics"])


def test_dashboard_snapshot_hides_local_quick_eval_rows(tmp_path) -> None:
    """A research cycle records two rows sharing a correlation_id: an ephemeral
    local quick-eval (no commit) and the authoritative GitHub eval (gh_*, committed).
    Only the committed eval (plus the baseline L0) belongs on the dashboard, so the
    trajectory shows one point per cycle — not a local/CI pair with diverging numbers."""
    registry = Registry(tmp_path)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.81, "latency_ms": 50.0})
    # Ephemeral local quick-eval row: no commit, shares correlation_id with the CI run.
    registry.record_run(
        run_id="run_local1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.931},  # diverges from the authoritative CI number below
        correlation_id="run_local1",
    )
    # Authoritative GitHub Actions eval for the same cycle.
    registry.record_run(
        run_id="gh_900",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.899},
        commit_sha="committed_sha",
        correlation_id="run_local1",
    )

    dash = registry.dashboard_snapshot()
    run_ids = {row["run_id"] for row in dash["runs"]}
    metric_run_ids = {row["run_id"] for row in dash["metrics"]}

    # The local quick-eval is hidden from runs and metrics ...
    assert "run_local1" not in run_ids
    assert "run_local1" not in metric_run_ids
    # ... while the committed CI eval and the baseline L0 remain.
    assert "gh_900" in run_ids
    assert "gh_900" in metric_run_ids
    assert any(row["group_id"] == BASELINE_RUN_GROUP for row in dash["metrics"])
    # Exactly one model_architecture accuracy point (the CI eval), not two.
    model_acc = [
        row
        for row in dash["metrics"]
        if row["group_id"] == "model_architecture" and row["metric_name"] == "accuracy"
    ]
    assert len(model_acc) == 1
    assert model_acc[0]["metric_value"] == 0.899


def test_registry_init_and_intent_packet(tmp_path) -> None:
    registry = Registry(tmp_path)
    registry.init()
    assert registry.schema_version() == SCHEMA_VERSION
    packet = IntentPacket(
        group_id="model_architecture",
        active_goal_id="h1",
        goal_text="test",
        attempt_count=1,
        last_failure_class="none",
        next_action="continue",
    )
    registry.write_intent_packet(packet)
    loaded = registry.read_intent_packet("model_architecture")
    assert loaded is not None
    assert loaded.active_goal_id == "h1"


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
    registry.record_cycle_manifest(
        run_id="run_abc",
        manifest_path=".hiagentresearch/cycles/model_architecture/run_abc.json",
        manifest={
            "run_id": "run_abc",
            "group_id": "model_architecture",
            "branch": "research/model-architecture",
            "loop_index": 1,
            "goal_id": "h1",
            "goal": "Try a model change.",
            "target_files": ["mnist/src/model.py"],
            "planned_code_changes": ["Edit model.py"],
        },
    )

    outcome = registry.outcome_for_run("run_abc")
    cycle = registry.cycle_for_run("run_abc")
    summary = registry.group_summary()

    assert outcome is not None
    assert outcome["research_outcome"] == "met_targets"
    assert cycle is not None
    assert cycle["target_files"] == ["mnist/src/model.py"]
    assert summary[0]["research_outcome"] == "met_targets"
    # Summary carries a generic metrics dict (no hardcoded metric columns).
    assert summary[0]["metrics"]["accuracy"] == 0.99


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
