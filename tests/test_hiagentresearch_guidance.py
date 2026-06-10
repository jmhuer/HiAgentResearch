from hiagentresearch.src.core.guidance import DEFAULT_GUIDANCE_FILES, default_guidance_files
from hiagentresearch.src.core import guidance


def test_default_guidance_files_matches_constants() -> None:
    assert default_guidance_files() == DEFAULT_GUIDANCE_FILES
    assert len(DEFAULT_GUIDANCE_FILES) == 1


def test_default_guidance_files_supports_embedded_runtime(monkeypatch, tmp_path) -> None:
    embedded = tmp_path / ".hiagentresearch" / "runtime" / "hiagentresearch"
    embedded.mkdir(parents=True)
    (embedded / "AGENTS.md").write_text("# Contract\n", encoding="utf-8")
    monkeypatch.setattr(guidance, "REPO_ROOT", tmp_path)

    assert default_guidance_files() == (".hiagentresearch/runtime/hiagentresearch/AGENTS.md",)
