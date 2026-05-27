import json

from hiagentresearch.src.registry.store import Registry
from hiagentresearch.src.registry.view import main


def test_registry_view_summary_and_show_json(tmp_path, capsys) -> None:
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
            "group_id": "model_architecture",
            "branch": "research/model-architecture",
            "loop_index": 1,
            "hypothesis_id": "h1",
            "hypothesis": "Try a model change.",
            "target_files": ["mnist/pipeline/model.py"],
            "planned_code_changes": ["Edit model.py"],
        },
    )

    assert main(["--state-dir", str(tmp_path), "--json", "summary"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary[0]["research_outcome"] == "met_targets"

    assert main(["--state-dir", str(tmp_path), "show", "--run-id", "run_abc", "--json"]) == 0
    detail = json.loads(capsys.readouterr().out)
    assert detail["metrics"]["accuracy"] == 0.99
    assert detail["experiment"]["hypothesis_id"] == "h1"

    assert main(["--state-dir", str(tmp_path), "export"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["export_schema_version"] == 1
    assert exported["metrics"][0]["metric_name"] == "accuracy"
    assert exported["experiments"][0]["hypothesis_id"] == "h1"


def test_registry_view_metrics_text(tmp_path, capsys) -> None:
    registry = Registry(tmp_path)
    registry.init()
    registry.record_run(
        run_id="run_abc",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.99},
    )

    assert main(["--state-dir", str(tmp_path), "metrics", "--group-id", "model_architecture"]) == 0
    assert "accuracy=0.99" in capsys.readouterr().out
