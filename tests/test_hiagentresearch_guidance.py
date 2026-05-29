from hiagentresearch.src.core.guidance import DEFAULT_GUIDANCE_FILES, default_guidance_files


def test_default_guidance_files_matches_constants() -> None:
    assert default_guidance_files() == DEFAULT_GUIDANCE_FILES
    assert len(DEFAULT_GUIDANCE_FILES) == 2
