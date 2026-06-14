"""Generate the workspace-facing AGENTS.md from config.

Goals and expectations come solely from ``research_output_expectations`` in the
active config. Everything else is workspace skeleton (paths, eval command,
protected zones). Framework cycle mechanics stay in the framework AGENTS.md.
"""

from __future__ import annotations

from pathlib import Path

from hiagentresearch.src.core.config import HiAgentResearchConfig, load_config
from hiagentresearch.src.core.guidance import default_guidance_files
from hiagentresearch.src.paths import REPO_ROOT


def _display_eval_command(config: HiAgentResearchConfig) -> str:
    return (
        config.evaluation.command_template
        .replace("{entrypoint}", config.evaluation.entrypoint)
        .replace("{workdir}", config.workdir)
        .replace("{project_id}", config.project_id)
        .replace("{group_id}", "<group_id>")
        .replace("{branch}", "<branch>")
    )


def _targets_lines(config: HiAgentResearchConfig) -> list[str]:
    # Show each scored metric and its DIRECTION only — deliberately not the absolute
    # pass/fail bound. In the quick-eval regime the absolute target is unreachable, and
    # handing it to the agent invites panic moves; the per-cycle scoreboard already drives
    # relative progress (beat your best / hold the floor).
    lines: list[str] = []
    for name in config.evaluation.targets:
        direction = "lower is better" if config.evaluation.metric_minimizes(name) else "higher is better"
        lines.append(f"- `{name}` — {direction}")
    return lines or ["- (no metrics configured)"]


def _dependency_lines(config: HiAgentResearchConfig) -> str:
    if not config.dependency_files:
        return (
            "No project dependency file is configured. If your code imports packages "
            "beyond the stdlib, ask operators to add a dependency file before relying on them."
        )
    paths = "\n".join(f"- `{path}`" for path in config.dependency_files)
    return f"""The GitHub eval node and agent loop install packages from:

{paths}

You may add libraries when your approach needs them. List every package your code imports—
including dependencies imported at module load time—in the dependency file in the same cycle.
Missing entries fail CI during pytest collection."""


def render_workspace_agents(config: HiAgentResearchConfig) -> str:
    workdir = config.workdir.rstrip("/") or "."
    metric_fields = ", ".join(f"`{name}`" for name in config.evaluation.targets) or "the configured metrics"
    reference_lines = "\n".join(f"- `{path}`" for path in config.all_reference_paths())
    hidden_lines = "\n".join(f"- `{path}`" for path in config.hidden_paths) or "- (none configured)"
    editable_lines = "\n".join(f"- `{path}`" for path in config.editable_paths) or f"- `{workdir}/`"
    expectation_lines = (
        "\n".join(f"- {item}" for item in config.agent_contract.research_output_expectations)
        or "- Follow the objective and keep changes inside the workspace."
    )
    targets_block = "\n".join(_targets_lines(config))
    dependency_lines = _dependency_lines(config)
    framework_guidance = default_guidance_files()[0]
    command = _display_eval_command(config)
    return f"""# Workspace contract ({workdir})

<!-- Generated from the active config by `hiagentresearch render-workspace-docs`. Do not edit by hand. -->

Two parts: **Goals and expectations** (authoritative — from `research_output_expectations`
in the active config) and **Workspace skeleton** (paths, eval wiring, protected zones).
Cycle mechanics live in `{framework_guidance}`.

## Goals and expectations

{expectation_lines}

## Workspace skeleton

This workspace (`{workdir}/`) is yours except for protected paths below.

### Editable paths

{editable_lines}

### Dependency files

{dependency_lines}

### How you are evaluated

After your cycle, the orchestrator (and GitHub eval node) runs this exact command:

```bash
{command}
```

It prints a canonical JSON report to stdout. Scored metrics ({metric_fields}):

{targets_block}

The eval reads `passed` / `execution_passed` health flags plus those metric keys.

### The eval zone is read-only

{reference_lines}

Read these to see how inference is loaded and scored. Never edit or run them.

### Other protected paths

{hidden_lines}

For cycle mechanics, planning artifacts, self-review, and git boundaries,
follow the framework contract in `{framework_guidance}`.
"""


def write_workspace_agents(
    config: HiAgentResearchConfig | None = None,
    *,
    root: Path = REPO_ROOT,
) -> Path:
    loaded = config or load_config()
    target = root / loaded.workspace_agents_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_workspace_agents(loaded), encoding="utf-8")
    return target
