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
    inherit_policy: str = "best_commit",
    top_commit_policy: str = "best_commit",
) -> ResearchGroupConfig:
    return ResearchGroupConfig(
        id=group_id,
        branch=f"research/{group_id.replace('_', '-')}",
        objective="test",
        policy_mode="explore",
        lineage=LineageConfig(
            mode=mode,
            inherit_from=inherit_from,
            inherit_policy=inherit_policy,
            top_commit_policy=top_commit_policy,
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


def test_last_commit_prefers_latest_github_commit_even_if_metric_regresses(tmp_path) -> None:
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_old_better",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.95},
        commit_sha="oldsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_old_better",
        manifest_path=".hiagentresearch/cycles/model_architecture/gh_old_better.json",
        manifest={"group_id": "model_architecture", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_newer_worse",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.70},
        commit_sha="newersha",
    )
    registry.record_cycle_manifest(
        run_id="gh_newer_worse",
        manifest_path=".hiagentresearch/cycles/model_architecture/gh_newer_worse.json",
        manifest={"group_id": "model_architecture", "loop_index": 2},
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
            _group(
                "optimization_strategy",
                mode="inherit",
                inherit_from="model_architecture",
                inherit_policy="last_commit",
            ),
        ],
    )
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("optimization_strategy"),
        config,
        registry=registry,
        git=GitService(tmp_path),
    )
    assert bootstrap.start_ref == "newersha"


def test_record_cycle_manifest_persists_parent_anchor_step(tmp_path) -> None:
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_cycle_manifest(
        run_id="run_child",
        manifest_path=".hiagentresearch/cycles/optimization_strategy/run_child.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_parent_anchor_step": 0,
            "lineage_anchor_sha": "mainsha",
            "lineage_anchor_source_group": "model_architecture",
        },
    )
    cycle = registry.cycle_for_run("run_child")
    assert cycle is not None
    assert cycle["lineage_parent_anchor_step"] == 0
    assert cycle["lineage_anchor_source_group"] == "model_architecture"


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
    registry.record_cycle_manifest(
        run_id="gh_loop1",
        manifest_path=".hiagentresearch/cycles/model_architecture/gh_loop1.json",
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
    registry.record_cycle_manifest(
        run_id="gh_opt_1",
        manifest_path=".hiagentresearch/cycles/optimization_strategy/gh_opt_1.json",
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


def test_inherit_policy_and_top_commit_policy_are_independent(tmp_path) -> None:
    """A group may branch from the BEST parent commit while its OWN top commit is
    the latest run (the Polish case). The branch-from must follow inherit_policy,
    never top_commit_policy."""
    registry = Registry(tmp_path / "state")
    registry.init()
    # Parent (hyperparameter_optimization) loop 1 is its peak; loop 2 regresses.
    registry.record_run(
        run_id="gh_hp_1",
        group_id="hyperparameter_optimization",
        branch="research/hyperparameter-optimization",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.98},
        commit_sha="hpbestsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_hp_1",
        manifest_path=".hiagentresearch/cycles/hyperparameter_optimization/gh_hp_1.json",
        manifest={"group_id": "hyperparameter_optimization", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_hp_2",
        group_id="hyperparameter_optimization",
        branch="research/hyperparameter-optimization",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.90},
        commit_sha="hplatestsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_hp_2",
        manifest_path=".hiagentresearch/cycles/hyperparameter_optimization/gh_hp_2.json",
        manifest={"group_id": "hyperparameter_optimization", "loop_index": 2},
    )
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true"},
        policy_modes={"explore": "Explore.", "exploit": "Exploit."},
        orchestration=OrchestrationConfig(
            execution_waves=[["hyperparameter_optimization"], ["polish_code"]],
        ),
        research_groups=[
            _group("hyperparameter_optimization"),
            _group(
                "polish_code",
                mode="inherit",
                inherit_from="hyperparameter_optimization",
                inherit_policy="best_commit",
                top_commit_policy="last_commit",
            ),
        ],
    )
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("polish_code"),
        config,
        registry=registry,
        git=GitService(tmp_path),
    )
    # Branches from the BEST hyperparam commit (loop 1), not the latest (loop 2).
    assert bootstrap.start_ref == "hpbestsha"


def test_latest_loop_github_run_orders_by_loop_index_not_created_at(tmp_path) -> None:
    """The higher loop_index wins even when it was recorded earlier in wall-clock
    time (retries / parallel waves can invert created_at order)."""
    registry = Registry(tmp_path / "state")
    registry.init()
    # Record the HIGHER loop_index first so created_at order disagrees with loop order.
    registry.record_run(
        run_id="gh_loop_3",
        group_id="polish_code",
        branch="research/polish-code",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.5},
        commit_sha="loop3sha",
    )
    registry.record_cycle_manifest(
        run_id="gh_loop_3",
        manifest_path=".hiagentresearch/cycles/polish_code/gh_loop_3.json",
        manifest={"group_id": "polish_code", "loop_index": 3},
    )
    registry.record_run(
        run_id="gh_loop_2",
        group_id="polish_code",
        branch="research/polish-code",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.6},
        commit_sha="loop2sha",
    )
    registry.record_cycle_manifest(
        run_id="gh_loop_2",
        manifest_path=".hiagentresearch/cycles/polish_code/gh_loop_2.json",
        manifest={"group_id": "polish_code", "loop_index": 2},
    )
    row = registry.latest_loop_github_run("polish_code")
    assert row is not None
    assert row["commit_sha"] == "loop3sha"


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


def test_merge_resolves_base_to_strongest_and_orders_sources(tmp_path) -> None:
    """A merge group auto-resolves: branch starts from the strongest lineage winner,
    the rest become merge_sources ranked best->worst."""
    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_model", group_id="model_architecture", branch="research/model-architecture",
        status="completed", failure_class="none", metrics={"accuracy": 0.95}, commit_sha="modelsha",
    )
    registry.record_run(
        run_id="gh_data", group_id="data_augmentation", branch="research/data-augmentation",
        status="completed", failure_class="none", metrics={"accuracy": 0.90}, commit_sha="datasha",
    )
    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "Explore.", "exploit": "Exploit."},
        orchestration=OrchestrationConfig(
            execution_waves=[["model_architecture", "data_augmentation"], ["merge_best"]],
        ),
        research_groups=[
            _group("model_architecture"),
            _group("data_augmentation"),
            ResearchGroupConfig(
                id="merge_best", branch="research/merge-best", policy_mode="exploit",
                task_kind="merge", lineage=LineageConfig(mode="inherit", anchor_metric="accuracy"),
            ),
        ],
    )
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("merge_best"), config, registry=registry, git=GitService(tmp_path),
    )
    assert bootstrap.start_ref == "modelsha"  # strongest lineage is the base
    assert bootstrap.parent_group_id == "model_architecture"
    assert [s["group_id"] for s in bootstrap.merge_sources] == ["data_augmentation"]
    assert bootstrap.merge_sources[0]["commit_sha"] == "datasha"


def test_merge_honors_source_top_commit_policy_for_engineering(tmp_path) -> None:
    """A merge picks each source's representative commit by THAT source's top_commit_policy.
    An engineering source (last_commit) that only preserved the metric still contributes its
    OWN latest commit — best_commit would fall back past it to the ancestor that owns the peak,
    silently dropping the engineering work from the merge base."""
    registry = Registry(tmp_path / "state")
    registry.init()
    # model_architecture peak @ 0.95.
    registry.record_run(
        run_id="gh_model", group_id="model_architecture", branch="research/model-architecture",
        status="completed", failure_class="none", metrics={"accuracy": 0.95}, commit_sha="modelsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_model",
        manifest_path=".hiagentresearch/cycles/model_architecture/gh_model.json",
        manifest={"group_id": "model_architecture", "loop_index": 1},
    )
    # polish inherits model_architecture and PRESERVES the metric (0.95 == inherited): its own
    # commit `polishsha` is metric-neutral, so best_commit would revert to modelsha.
    registry.record_run(
        run_id="gh_polish", group_id="polish_code", branch="research/polish-code",
        status="completed", failure_class="none", metrics={"accuracy": 0.95}, commit_sha="polishsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_polish",
        manifest_path=".hiagentresearch/cycles/polish_code/gh_polish.json",
        manifest={
            "group_id": "polish_code", "loop_index": 1, "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture", "lineage_anchor_sha": "modelsha",
            "lineage_parent_anchor_step": 1,
        },
    )
    # A weaker independent lineage so the merge has a clear base.
    registry.record_run(
        run_id="gh_data", group_id="data_augmentation", branch="research/data-augmentation",
        status="completed", failure_class="none", metrics={"accuracy": 0.88}, commit_sha="datasha",
    )
    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "Explore.", "exploit": "Exploit."},
        orchestration=OrchestrationConfig(
            execution_waves=[["model_architecture", "data_augmentation"], ["polish_code"], ["merge_best"]],
        ),
        research_groups=[
            _group("model_architecture"),
            _group("data_augmentation"),
            ResearchGroupConfig(
                id="polish_code", branch="research/polish-code", objective="t", policy_mode="exploit",
                task_kind="engineering",
                lineage=LineageConfig(mode="inherit", inherit_from="model_architecture"),
            ),
            ResearchGroupConfig(
                id="merge_best", branch="research/merge-best", policy_mode="exploit",
                task_kind="merge",
                lineage=LineageConfig(mode="inherit", anchor_metric="accuracy", draw_from=["polish_code", "data_augmentation"]),
            ),
        ],
    )
    # Engineering default kicks in: polish_code resolves its own latest commit.
    assert config.group_by_id("polish_code").lineage.top_commit_policy == "last_commit"
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("merge_best"), config, registry=registry, git=GitService(tmp_path),
    )
    # The merge base is polish's OWN commit, attributed to polish — not the ancestor's modelsha.
    assert bootstrap.start_ref == "polishsha"
    assert bootstrap.parent_group_id == "polish_code"
    assert [s["group_id"] for s in bootstrap.merge_sources] == ["data_augmentation"]


def test_merge_drops_source_that_never_beat_its_inherited_floor(tmp_path) -> None:
    """A fold-in must add something the base does not already contain. A source whose best
    commit falls back to an ANCESTOR of the base (it regressed below the floor it inherited,
    so best_commit fell through to a commit already on the base's lineage path) integrates
    nothing and is dropped. This is the optimization__a2 corner case: a leaf that scored under
    its inherited architecture commit would otherwise re-offer that architecture commit — which
    the base already descends from — as a no-op fold-in."""
    registry = Registry(tmp_path / "state")
    registry.init()
    # Shared root both experiments inherit from.
    registry.record_run(
        run_id="gh_root", group_id="architecture", branch="research/architecture",
        status="completed", failure_class="none", metrics={"accuracy": 0.95}, commit_sha="rootsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_root",
        manifest_path=".hiagentresearch/cycles/architecture/gh_root.json",
        manifest={"group_id": "architecture", "loop_index": 1},
    )
    # strong_exp IMPROVES on the inherited floor (0.97 > 0.95) → its own commit wins.
    registry.record_run(
        run_id="gh_strong", group_id="strong_exp", branch="research/strong-exp",
        status="completed", failure_class="none", metrics={"accuracy": 0.97}, commit_sha="strongsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_strong",
        manifest_path=".hiagentresearch/cycles/strong_exp/gh_strong.json",
        manifest={
            "group_id": "strong_exp", "loop_index": 1, "lineage_mode": "inherit",
            "lineage_parent_group_id": "architecture", "lineage_anchor_sha": "rootsha",
            "lineage_anchor_source_group": "architecture", "lineage_parent_anchor_step": 1,
        },
    )
    # weak_exp REGRESSES (0.90 < 0.95) → best_commit falls back to the inherited rootsha,
    # owned by `architecture` (the shared ancestor), not weak_exp itself.
    registry.record_run(
        run_id="gh_weak", group_id="weak_exp", branch="research/weak-exp",
        status="completed", failure_class="none", metrics={"accuracy": 0.90}, commit_sha="weaksha",
    )
    registry.record_cycle_manifest(
        run_id="gh_weak",
        manifest_path=".hiagentresearch/cycles/weak_exp/gh_weak.json",
        manifest={
            "group_id": "weak_exp", "loop_index": 1, "lineage_mode": "inherit",
            "lineage_parent_group_id": "architecture", "lineage_anchor_sha": "rootsha",
            "lineage_anchor_source_group": "architecture", "lineage_parent_anchor_step": 1,
        },
    )
    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "Explore.", "exploit": "Exploit."},
        orchestration=OrchestrationConfig(
            execution_waves=[["architecture"], ["strong_exp", "weak_exp"], ["merge_best"]],
        ),
        research_groups=[
            _group("architecture"),
            _group("strong_exp", mode="inherit", inherit_from="architecture"),
            _group("weak_exp", mode="inherit", inherit_from="architecture"),
            ResearchGroupConfig(
                id="merge_best", branch="research/merge-best", policy_mode="exploit",
                task_kind="merge",
                lineage=LineageConfig(
                    mode="inherit", anchor_metric="accuracy", draw_from=["strong_exp", "weak_exp"]
                ),
            ),
        ],
    )
    bootstrap = resolve_branch_bootstrap(
        config.group_by_id("merge_best"), config, registry=registry, git=GitService(tmp_path),
    )
    # Base is the genuine winner; the regressed source (resolved to the shared ancestor) is
    # dropped rather than offered as a no-op fold-in.
    assert bootstrap.start_ref == "strongsha"
    assert bootstrap.parent_group_id == "strong_exp"
    assert bootstrap.merge_sources == ()


# --- select collapse carries its adopted result into a downstream inherit (no ghost node) ---

def _resolve_select_collapse_then_inherit(monkeypatch, tmp_path, *, h1_acc, h2_acc, h2_sha="h2sha"):
    """Seed two architecture leaves, resolve the select collapse, persist its adopted anchor
    exactly as the loops=0 orchestrator path does, then resolve optimization inheriting from it."""
    from pathlib import Path
    from hiagentresearch.src.core.config import load_config

    def fake_run(args, **kwargs):
        if args[1:2] == ["rev-parse"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = load_config(Path("configs/test_select_edge.yaml"))
    reg = Registry(tmp_path / "state"); reg.init()
    reg.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.90})
    for gid, branch, sha, acc in (
        ("architecture__a1", "research/architecture-a1", "h1sha", h1_acc),
        ("architecture__a2", "research/architecture-a2", h2_sha, h2_acc),
    ):
        reg.record_run(run_id=f"gh_{gid}", group_id=gid, branch=branch, status="finished",
                       failure_class="none", metrics={"accuracy": acc}, commit_sha=sha)
        reg.record_cycle_manifest(run_id=f"gh_{gid}", manifest_path="",
                                  manifest={"group_id": gid, "branch": branch, "loop_index": 1})
    git = GitService(tmp_path)

    collapse = resolve_branch_bootstrap(cfg.group_by_id("architecture__collapse"), cfg, registry=reg, git=git)
    # Persist the collapse's adopted anchor (mirrors loop_controller's loops==0 path).
    reg.record_cycle_manifest(
        run_id="collapse_architecture__collapse", manifest_path="",
        manifest={
            "group_id": "architecture__collapse", "branch": "research/architecture-collapse",
            "loop_index": 0, "lineage_mode": "inherit",
            "lineage_parent_group_id": collapse.parent_group_id,
            "lineage_anchor_sha": collapse.start_ref,
            "lineage_anchor_policy": collapse.anchor_policy,
            "lineage_parent_anchor_step": collapse.parent_anchor_step,
            "lineage_anchor_source_group": collapse.anchor_source_group_id,
        },
    )
    optimization = resolve_branch_bootstrap(cfg.group_by_id("optimization"), cfg, registry=reg, git=git)
    return collapse, optimization


def test_select_collapse_winning_leaf_carries_downstream_to_l2(monkeypatch, tmp_path) -> None:
    """A leaf that BEATS baseline is adopted, and a group inheriting from the select collapse
    continues FROM that leaf's commit at L1 — so its own loops land at L2 (not a baseline reset)."""
    collapse, optimization = _resolve_select_collapse_then_inherit(
        monkeypatch, tmp_path, h1_acc=0.88, h2_acc=0.95, h2_sha="h2sha"
    )
    assert collapse.start_ref == "h2sha"
    assert collapse.anchor_source_group_id == "architecture__a2"
    # The whole point: optimization resolves THROUGH the (zero-loop) collapse to h2's commit at
    # step 1 — its loops therefore land at L2, the edge that was silently collapsing to baseline.
    assert optimization.start_ref == "h2sha"
    assert optimization.parent_anchor_step == 1


def test_select_collapse_all_regress_inherits_baseline_at_l1(monkeypatch, tmp_path) -> None:
    """When no leaf beats baseline, best_commit keeps the baseline (L0) — adopted honestly (no
    leaf mislabel). A downstream inherit continues from baseline at step 0 (its loops are L1)."""
    collapse, optimization = _resolve_select_collapse_then_inherit(
        monkeypatch, tmp_path, h1_acc=0.88, h2_acc=0.85
    )
    assert collapse.start_ref == "mainsha"            # baseline, not a regressed leaf
    assert collapse.parent_group_id is None           # honest: no leaf is credited
    assert collapse.anchor_source_group_id is None
    assert optimization.start_ref == "mainsha"
    assert optimization.parent_anchor_step == 0
