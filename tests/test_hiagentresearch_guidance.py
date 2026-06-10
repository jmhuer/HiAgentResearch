from hiagentresearch.src.core.guidance import (
    DEFAULT_GUIDANCE_FILES,
    FRAMEWORK_GUIDANCE_PATH,
    default_guidance_files,
    materialize_framework_guidance,
)


def test_default_guidance_files_matches_constants() -> None:
    assert default_guidance_files() == DEFAULT_GUIDANCE_FILES
    assert len(DEFAULT_GUIDANCE_FILES) == 1


def test_default_guidance_files_are_project_facing() -> None:
    assert default_guidance_files() == (".hiagentresearch/AGENTS.md",)


def test_materialize_framework_guidance_exposes_runtime_contract(tmp_path) -> None:
    target = materialize_framework_guidance(root=tmp_path)

    assert target == tmp_path / FRAMEWORK_GUIDANCE_PATH
    text = target.read_text(encoding="utf-8")
    assert "Generated from HiAgentResearch runtime" in text
    assert "HiAgentResearch Agent Contract" in text
