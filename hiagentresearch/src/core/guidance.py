"""Framework guidance documents for research-cycle agents.

Paths are fixed in code; projects do not list them in config.yaml. The workspace
``AGENTS.md`` (derived from ``workdir`` and ``evaluation``) is prepended at prompt
build time — see ``agents.prompts.build_research_cycle_prompt``.
"""

from __future__ import annotations

DEFAULT_GUIDANCE_FILES = (
    "hiagentresearch/AGENTS.md",
)


def default_guidance_files() -> tuple[str, ...]:
    return DEFAULT_GUIDANCE_FILES
