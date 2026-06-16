from pathlib import Path

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.core.outcomes import (
    baseline_metrics_complete,
    required_baseline_metrics,
)
from hiagentresearch.src.project.docs import render_workspace_agents
from hiagentresearch.src.runtime import orchestrator


def _group():
    return load_config(Path("configs/standard.yaml")).research_groups_by_id()["model_architecture"]


def test_render_workspace_agents_includes_command_and_targets() -> None:
    config = load_config(Path("configs/standard.yaml"))
    doc = render_workspace_agents(config)

    assert "Workspace contract (mnist)" in doc
    assert "--workdir mnist" in doc
    # Metrics are shown by name + direction, NOT as an absolute bar (unreachable in
    # quick-eval; would invite panic moves). The per-cycle scoreboard drives relative progress.
    assert "`accuracy` — higher is better" in doc
    assert "`latency_ms` — lower is better" in doc
    assert "0.985" not in doc
    assert "13.0" not in doc
    assert ".hiagentresearch/eval/" in doc
    assert "read-only" in doc.lower()
    assert "canonical JSON" in doc
    assert "Only the configured editable paths are agent-owned" in doc
    assert "### Read-only authority and context" in doc
    assert "### Dependency files" in doc
    assert "`mnist/requirements.txt`" in doc
    assert "mnist/src/tests/" not in doc
    assert "For cycle mechanics, planning artifacts, self-review, and git boundaries" in doc
    assert "what counts as a regression" not in doc
    assert "## Goals and expectations" in doc
    assert "## Workspace skeleton" in doc
    assert doc.index("## Goals and expectations") < doc.index("## Workspace skeleton")
    assert "### Editable paths" in doc
    assert "Optimize for relative progress" not in doc
    assert "research_output_expectations" in doc


def test_required_baseline_metrics_derived_from_targets() -> None:
    config = load_config(Path("configs/standard.yaml"))
    required = required_baseline_metrics(config.evaluation.targets)
    assert set(required) == {"accuracy", "latency_ms"}
    assert baseline_metrics_complete({"accuracy": 0.99, "latency_ms": 5.0}, required) is True
    assert baseline_metrics_complete({"accuracy": 0.99}, required) is False


def test_required_baseline_metrics_for_other_project() -> None:
    required = required_baseline_metrics(["f1"])
    assert required == ("f1",)
    assert baseline_metrics_complete({"f1": 0.9}, required) is True
    assert baseline_metrics_complete({"accuracy": 0.9}, required) is False


def test_workspace_docs_blocks_are_config_driven_not_inferred_from_project_id() -> None:
    """The smoke-test and frozen-gate blocks come ONLY from workspace_docs config —
    the framework must not infer them from a `layerN` naming convention in project_id."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, ResearchGroupConfig

    base = dict(
        workdir=".",
        evaluation={
            "entrypoint": ".hiagentresearch/eval/run.py",
            "command_template": "true",
            "targets": {"accuracy": {"min": 0.9}},
        },
        dependency_files=["requirements.txt"],
        policy_modes={"explore": "Explore."},
        research_groups=[
            ResearchGroupConfig(id="g", objective="t", policy_mode="explore"),
        ],
    )

    # A project_id that literally contains "layer2" but no workspace_docs → no blocks.
    inferred = HiAgentResearchConfig(project_id="grapple_layer2", **base)
    doc = render_workspace_agents(inferred)
    assert "infra smoke test" not in doc
    assert "frozen pytest gates" not in doc.lower() and "operator-owned pytest gates" not in doc

    # Unset paths stay absent even when the files would exist; only config opts in.
    configured = HiAgentResearchConfig(
        project_id="anything",
        workspace_docs={"gate_tests_dir": "does/not/exist/", "infra_smoke_test": "nope.py"},
        **base,
    )
    # Non-existent paths render nothing (stale config never documents a missing file).
    assert "infra smoke test" not in render_workspace_agents(configured)


def test_area_desugar_uses_configured_branch_prefix() -> None:
    from hiagentresearch.src.core.config import HiAgentResearchConfig, ResearchGroupConfig

    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={
            "entrypoint": ".hiagentresearch/eval/run.py",
            "command_template": "true",
            "targets": {"accuracy": {"min": 0.9}},
        },
        policy_modes={"explore": "Explore."},
        orchestration={"baseline_ref": "main", "branch_prefix": "hiagentresearch"},
        research_groups=[
            ResearchGroupConfig(
                id="layer2_model_gate",
                objective="t",
                policy_mode="explore",
                approaches=["review", "retry"],
            ),
        ],
    )

    branches = {group.id: group.branch for group in config.research_groups}
    assert branches["layer2_model_gate__a1"] == "hiagentresearch/layer2_model_gate-a1"
    assert branches["layer2_model_gate__a2"] == "hiagentresearch/layer2_model_gate-a2"
    assert branches["layer2_model_gate__collapse"] == "hiagentresearch/layer2_model_gate-collapse"


def test_edit_boundary_accepts_workspace_changes(monkeypatch) -> None:
    group = _group()
    monkeypatch.setattr(
        orchestrator,
        "_git_changed_files",
        lambda workdir: {"mnist/src/model.py", "mnist/src/tests/test_new.py", "mnist/data/MNIST/raw/x"},
    )
    valid, error, changes = orchestrator._validate_edit_boundary(
        workdir=Path("."), group=group, run_id="run_x", before_changes=set()
    )
    assert valid is True, error
    assert "mnist/src/model.py" in changes


def test_edit_boundary_rejects_eval_zone_edit(monkeypatch) -> None:
    group = _group()
    monkeypatch.setattr(
        orchestrator,
        "_git_changed_files",
        lambda workdir: {"mnist/src/model.py", ".hiagentresearch/eval/score.py"},
    )
    valid, error, _ = orchestrator._validate_edit_boundary(
        workdir=Path("."), group=group, run_id="run_x", before_changes=set()
    )
    assert valid is False
    assert "read-only reference/eval paths" in error


def test_edit_boundary_rejects_changes_outside_workspace(monkeypatch) -> None:
    group = _group()
    monkeypatch.setattr(
        orchestrator,
        "_git_changed_files",
        lambda workdir: {"hiagentresearch/src/core/config.py"},
    )
    valid, error, _ = orchestrator._validate_edit_boundary(
        workdir=Path("."), group=group, run_id="run_x", before_changes=set()
    )
    assert valid is False
    assert "outside workspace" in error


def test_edit_boundary_rejects_changes_outside_configured_editable_paths(monkeypatch) -> None:
    from hiagentresearch.src.core.models import EvaluationSpec, ResearchGroup

    group = ResearchGroup(
        id="layer2",
        branch="hiagentresearch/layer2",
        objective="x",
        policy_mode="explore",
        evaluation=EvaluationSpec(command="true"),
        workdir="grapple-multilayer-detection/src",
        editable_paths=[
            "grapple-multilayer-detection/src/core/layer2/",
            "grapple-multilayer-detection/src/core/layer2/tests/",
        ],
    )
    monkeypatch.setattr(
        orchestrator,
        "_git_changed_files",
        lambda workdir: {
            "grapple-multilayer-detection/src/core/layer2/runtime.py",
            "grapple-multilayer-detection/src/core/providers/dashscope.py",
        },
    )
    valid, error, _ = orchestrator._validate_edit_boundary(
        workdir=Path("."), group=group, run_id="run_x", before_changes=set()
    )
    assert valid is False
    assert "outside configured editable paths" in error


def test_edit_boundary_requires_a_workspace_source_change(monkeypatch) -> None:
    group = _group()
    monkeypatch.setattr(
        orchestrator,
        "_git_changed_files",
        lambda workdir: {"mnist/data/MNIST/raw/x", ".hiagentresearch/runs/run_x/cycle_plan.md"},
    )
    valid, error, _ = orchestrator._validate_edit_boundary(
        workdir=Path("."), group=group, run_id="run_x", before_changes=set()
    )
    assert valid is False
    assert "no workspace source change" in error


def test_edit_boundary_allows_framework_experiment_artifacts(monkeypatch) -> None:
    group = _group()
    monkeypatch.setattr(
        orchestrator,
        "_git_changed_files",
        lambda workdir: {"mnist/src/model.py", ".hiagentresearch/cycles/model_architecture/run_x.json"},
    )
    valid, error, changes = orchestrator._validate_edit_boundary(
        workdir=Path("."), group=group, run_id="run_x", before_changes=set()
    )
    assert valid is True, error
    assert "mnist/src/model.py" in changes


def test_edit_boundary_tolerates_agent_created_root_lockfile(monkeypatch) -> None:
    # A package manager (e.g. `uv`) the agent runs may drop uv.lock at the repo root — tool output,
    # never committed (staging is workspace-scoped). It must not invalidate an otherwise-valid cycle.
    group = _group()
    monkeypatch.setattr(
        orchestrator,
        "_git_changed_files",
        lambda workdir: {"mnist/src/model.py", "uv.lock"},
    )
    valid, error, changes = orchestrator._validate_edit_boundary(
        workdir=Path("."), group=group, run_id="run_x", before_changes=set()
    )
    assert valid is True, error
    assert "mnist/src/model.py" in changes
    # A real out-of-workspace SOURCE edit alongside the lockfile is still rejected.
    monkeypatch.setattr(
        orchestrator,
        "_git_changed_files",
        lambda workdir: {"mnist/src/model.py", "uv.lock", "hiagentresearch/src/core/config.py"},
    )
    valid, error, _ = orchestrator._validate_edit_boundary(
        workdir=Path("."), group=group, run_id="run_x", before_changes=set()
    )
    assert valid is False and "outside workspace" in error and "config.py" in error
