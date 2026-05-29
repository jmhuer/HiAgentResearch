import subprocess

import pytest

from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, OrchestrationConfig, ResearchGroupConfig
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.lineage.resolve import LineageError, resolve_branch_bootstrap
from hiagentresearch.src.registry.store import Registry


def _group(
    group_id: str,
    *,
    mode: str = "baseline",
    inherit_from: str | None = None,
    anchor_policy: str = "best_commit",
) -> ResearchGroupConfig:
    return ResearchGroupConfig(
        id=group_id,
        branch=f"research/{group_id.replace('_', '-')}",
        objective="test",
        policy_mode="explore",
        lineage=LineageConfig(
            mode=mode,
            inherit_from=inherit_from,
            anchor_policy=anchor_policy,
        ),
    )


def test_resolve_baseline_uses_main_sha(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "deadbeef\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true"},
        policy_modes={"explore": "Explore."},
        orchestration=OrchestrationConfig(),
        research_groups=[_group("model_architecture")],
    )
    registry = Registry(tmp_path / "state")
    registry.init()
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("model_architecture"),
        config,
        registry=registry,
        git=GitService(tmp_path),
    )
    assert bootstrap.mode == "baseline"
    assert bootstrap.start_ref == "deadbeef"


def test_resolve_inherit_best_commit_from_registry(tmp_path) -> None:
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_100",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.99},
        commit_sha="abc123",
    )
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true"},
        policy_modes={"explore": "Explore."},
        orchestration=OrchestrationConfig(
            execution_waves=[["model_architecture"], ["optimization_strategy"]],
        ),
        research_groups=[
            _group("model_architecture"),
            _group("optimization_strategy", mode="inherit", inherit_from="model_architecture"),
        ],
    )
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("optimization_strategy"),
        config,
        registry=registry,
        git=GitService(tmp_path),
    )
    assert bootstrap.parent_group_id == "model_architecture"
    assert bootstrap.start_ref == "abc123"


def test_best_commit_prefers_highest_github_metric_not_latest(tmp_path) -> None:
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_old_best",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.95},
        commit_sha="bestsha",
    )
    registry.record_run(
        run_id="gh_new_worse",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.80},
        commit_sha="latestsha",
    )
    registry.record_run(
        run_id="run_local_high",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.99},
        commit_sha="",
    )
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true"},
        policy_modes={"explore": "Explore."},
        orchestration=OrchestrationConfig(
            execution_waves=[["model_architecture"], ["optimization_strategy"]],
        ),
        research_groups=[
            _group("model_architecture"),
            _group("optimization_strategy", mode="inherit", inherit_from="model_architecture"),
        ],
    )
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("optimization_strategy"),
        config,
        registry=registry,
        git=GitService(tmp_path),
    )
    assert bootstrap.start_ref == "bestsha"


def test_record_experiment_manifest_persists_parent_anchor_step(tmp_path) -> None:
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_experiment_manifest(
        run_id="run_child",
        manifest_path=".hiagentresearch/experiments/optimization_strategy/run_child.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_parent_anchor_step": 0,
            "lineage_anchor_sha": "mainsha",
            "lineage_anchor_source_group": "model_architecture",
        },
    )
    experiment = registry.experiment_for_run("run_child")
    assert experiment is not None
    assert experiment["lineage_parent_anchor_step"] == 0
    assert experiment["lineage_anchor_source_group"] == "model_architecture"


def test_best_commit_prefers_parent_l0_when_baseline_is_higher(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.949, "latency_ms": 6.0})
    registry.record_run(
        run_id="gh_loop1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.935, "latency_ms": 6.1},
        commit_sha="loop1sha",
    )
    registry.record_experiment_manifest(
        run_id="gh_loop1",
        manifest_path=".hiagentresearch/experiments/model_architecture/gh_loop1.json",
        manifest={"group_id": "model_architecture", "loop_index": 1},
    )
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true"},
        policy_modes={"explore": "Explore."},
        orchestration=OrchestrationConfig(baseline_ref="main"),
        research_groups=[
            _group("model_architecture"),
            _group("optimization_strategy", mode="inherit", inherit_from="model_architecture"),
        ],
    )
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("optimization_strategy"),
        config,
        registry=registry,
        git=GitService(tmp_path),
    )
    assert bootstrap.start_ref == "mainsha"
    assert bootstrap.parent_anchor_step == 0
    assert bootstrap.anchor_source_group_id is None


def test_hyperparameter_can_inherit_parent_origin_commit_from_model(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.80, "latency_ms": 6.0})
    registry.record_run(
        run_id="gh_model_2",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.95, "latency_ms": 6.3},
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
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.91, "latency_ms": 6.1},
        commit_sha="optloop1sha",
    )
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true"},
        policy_modes={"explore": "Explore."},
        orchestration=OrchestrationConfig(baseline_ref="main"),
        research_groups=[
            _group("model_architecture"),
            _group("optimization_strategy", mode="inherit", inherit_from="model_architecture"),
            _group("hyperparameter_optimization", mode="inherit", inherit_from="optimization_strategy"),
        ],
    )
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("hyperparameter_optimization"),
        config,
        registry=registry,
        git=GitService(tmp_path),
    )
    assert bootstrap.start_ref == "modelbestsha"
    assert bootstrap.parent_anchor_step == 2
    # The winning commit is owned by the grandparent (model_architecture), not the
    # immediate parent (optimization_strategy) which never beat that baseline.
    assert bootstrap.anchor_source_group_id == "model_architecture"


def test_force_mode_fails_fast(tmp_path) -> None:
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true"},
        policy_modes={"explore": "Explore."},
        research_groups=[
            ResearchGroupConfig(
                id="demo",
                branch="research/demo",
                objective="test",
                policy_mode="explore",
                lineage=LineageConfig(mode="force"),
            )
        ],
    )
    registry = Registry(tmp_path / "state")
    registry.init()
    with pytest.raises(LineageError, match="force"):
        resolve_branch_bootstrap(
            config.group_by_id("demo"),
            config,
            registry=registry,
            git=GitService(tmp_path),
        )
