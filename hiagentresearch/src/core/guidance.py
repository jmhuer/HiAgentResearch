"""Framework guidance documents for research-cycle agents."""

from __future__ import annotations

from pathlib import Path

from hiagentresearch.src.paths import REPO_ROOT

FRAMEWORK_GUIDANCE_PATH = ".hiagentresearch/AGENTS.md"
DEFAULT_GUIDANCE_FILES = (FRAMEWORK_GUIDANCE_PATH,)
_SOURCE_GUIDANCE_PATH = Path(__file__).resolve().parents[2] / "AGENTS.md"


def default_guidance_files() -> tuple[str, ...]:
    """Return the invariant project-facing framework guidance path."""
    return DEFAULT_GUIDANCE_FILES


def materialize_framework_guidance(*, root: Path | None = None) -> Path:
    """Expose the framework cycle contract at the canonical project path.

    The source contract is owned by the HiAgentResearch runtime. Projects read the
    materialized copy from `.hiagentresearch/AGENTS.md`, so agent prompts do not
    depend on whether the runtime is vendored, installed, or checked out nearby.
    """
    if not _SOURCE_GUIDANCE_PATH.is_file():
        raise FileNotFoundError(f"missing framework guidance source: {_SOURCE_GUIDANCE_PATH}")
    target = (root or REPO_ROOT) / FRAMEWORK_GUIDANCE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    source = _SOURCE_GUIDANCE_PATH.read_text(encoding="utf-8")
    target.write_text(
        "<!-- Generated from HiAgentResearch runtime. Do not edit by hand. -->\n\n" + source,
        encoding="utf-8",
    )
    return target
