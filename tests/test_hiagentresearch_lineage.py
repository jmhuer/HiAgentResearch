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
        allowed_paths=["src/app.py"],
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
        editable_paths=["src/app.py"],
        frozen_eval_entrypoint=".hiagentresearch/eval/run.py",
        evaluation={"command_template": "true", "parser": "canonical_json_stdout"},
        artifact_contract={"required": ["metrics.json"]},
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
        editable_paths=["src/app.py"],
        frozen_eval_entrypoint=".hiagentresearch/eval/run.py",
        evaluation={"command_template": "true", "parser": "canonical_json_stdout"},
        artifact_contract={"required": ["metrics.json"]},
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
        editable_paths=["src/app.py"],
        frozen_eval_entrypoint=".hiagentresearch/eval/run.py",
        evaluation={"command_template": "true", "parser": "canonical_json_stdout"},
        artifact_contract={"required": ["metrics.json"]},
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


def test_force_mode_fails_fast(tmp_path) -> None:
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        editable_paths=["src/app.py"],
        frozen_eval_entrypoint=".hiagentresearch/eval/run.py",
        evaluation={"command_template": "true", "parser": "canonical_json_stdout"},
        artifact_contract={"required": ["metrics.json"]},
        policy_modes={"explore": "Explore."},
        research_groups=[
            ResearchGroupConfig(
                id="demo",
                branch="research/demo",
                objective="test",
                policy_mode="explore",
                allowed_paths=["src/app.py"],
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
