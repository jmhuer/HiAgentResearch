"""Framework guidance documents for research-cycle agents.

Projects do not list these in config.yaml. The workspace ``AGENTS.md`` (derived
from ``workdir`` and ``evaluation``) is prepended at prompt build time — see
``agents.prompts.build_research_cycle_prompt``.
"""

from __future__ import annotations

from hiagentresearch.src.paths import REPO_ROOT

GUIDANCE_FILE_CANDIDATES = (
    # Standalone HiAgentResearch checkout.
    "hiagentresearch/AGENTS.md",
    # Embedded runtime in a project-owned .hiagentresearch control plane.
    ".hiagentresearch/runtime/hiagentresearch/AGENTS.md",
    # Historical embedded runtime layout.
    "tools/hiagentresearch-runtime/hiagentresearch/AGENTS.md",
)
DEFAULT_GUIDANCE_FILES = (GUIDANCE_FILE_CANDIDATES[0],)


def default_guidance_files() -> tuple[str, ...]:
    for path in GUIDANCE_FILE_CANDIDATES:
        if (REPO_ROOT / path).exists():
            return (path,)
    return (GUIDANCE_FILE_CANDIDATES[0],)
