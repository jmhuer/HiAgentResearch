from hiagentresearch.src.core.edit_boundary import (
    PathCategory,
    PathClassifier,
    is_generated_artifact,
)
from hiagentresearch.src.core.models import EvaluationSpec, ResearchGroup


def _group(**overrides) -> ResearchGroup:
    base = dict(
        id="g",
        branch="b",
        objective="o",
        policy_mode="explore",
        evaluation=EvaluationSpec(command="true"),
        workdir="proj/src",
        reference_paths=[".hiagentresearch/eval/"],
        generated_paths=["proj/src/data/"],
        hidden_paths=["proj/src/core/other/"],
        editable_paths=["proj/src/core/layer2/"],
    )
    base.update(overrides)
    return ResearchGroup(**base)


def _classify(path: str, **overrides) -> PathCategory:
    return PathClassifier(_group(**overrides), run_id="r1").classify(path)


def test_classify_precedence_reference_then_hidden_then_ignored() -> None:
    assert _classify(".hiagentresearch/eval/run.py") is PathCategory.REFERENCE
    assert _classify("proj/src/core/other/x.py") is PathCategory.HIDDEN
    assert _classify("proj/src/data/cache.bin") is PathCategory.IGNORED
    assert _classify(".hiagentresearch/runs/r1/cycle_plan.md") is PathCategory.IGNORED
    assert _classify("uv.lock") is PathCategory.IGNORED


def test_classify_workspace_vs_outside() -> None:
    assert _classify("proj/src/core/layer2/analyzer.py") is PathCategory.WORKSPACE
    # In the workspace but outside the editable allowlist.
    assert _classify("proj/src/core/layer1/analyzer.py") is PathCategory.OUTSIDE_EDITABLE
    # A real edit outside the workspace entirely.
    assert _classify("other/thing.py") is PathCategory.OUTSIDE_WORKDIR


def test_no_editable_allowlist_treats_whole_workdir_as_workspace() -> None:
    assert _classify("proj/src/anything.py", editable_paths=[]) is PathCategory.WORKSPACE


def test_is_generated_artifact_covers_lockfiles_and_cycle_manifests() -> None:
    assert is_generated_artifact("poetry.lock", []) is True
    assert is_generated_artifact(".hiagentresearch/cycles/g/run.json", []) is True
    assert is_generated_artifact("proj/src/x.py", []) is False
