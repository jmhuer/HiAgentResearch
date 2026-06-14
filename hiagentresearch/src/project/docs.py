"""Generate the workspace-facing AGENTS.md from config.

Goals and expectations come solely from ``research_output_expectations`` in the
active config. Everything else is workspace skeleton (paths, eval command,
protected zones). Framework cycle mechanics stay in the framework AGENTS.md.
"""

from __future__ import annotations

import re
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


def _frozen_gate_note(config: HiAgentResearchConfig) -> str:
    match = re.search(r"layer(\d+)", config.project_id)
    if not match:
        return ""
    gate_dir = f".hiagentresearch/eval/gate/layer{match.group(1)}/"
    gate_path = REPO_ROOT / gate_dir
    if not gate_path.exists():
        return ""
    editable_tests = f"{config.workdir.rstrip('/')}/core/layer{match.group(1)}/tests/"
    editable_gate_files = [
        f"{editable_tests}{path.name}"
        for path in sorted(gate_path.glob("test_*.py"))
        if (REPO_ROOT / editable_tests / path.name).exists()
    ]
    optional_check = ""
    if editable_gate_files:
        optional_check = (
            " If this repo provides editable copies of the gates, run "
            f"`PYTHONPATH={config.workdir.rstrip('/') or '.'} python -m pytest {' '.join(editable_gate_files)}` "
            "as an optional local "
            "repair check before returning your cycle."
        )
    return (
        f"Before metric scoring, the eval adapter runs operator-owned pytest gates from "
        f"`{gate_dir}`. These gates are read-only; failures count as `code_failure`, "
        f"not as a scored experiment. Agent-editable tests live under `{editable_tests}`."
        f"{optional_check}"
    )


def render_workspace_agents(config: HiAgentResearchConfig) -> str:
    workdir = config.workdir.rstrip("/") or "."
    metric_fields = ", ".join(f"`{name}`" for name in config.evaluation.targets) or "the configured metrics"
    reference_lines = "\n".join(f"- `{path}`" for path in config.all_reference_paths())
    editable_lines = "\n".join(f"- `{path}`" for path in config.editable_paths) or f"- `{workdir}/`"
    expectation_lines = (
        "\n".join(f"- {item}" for item in config.agent_contract.research_output_expectations)
        or "- Follow the objective and keep changes inside the workspace."
    )
    targets_block = "\n".join(_targets_lines(config))
    dependency_lines = _dependency_lines(config)
    framework_guidance = default_guidance_files()[0]
    command = _display_eval_command(config)
    frozen_gate_note = _frozen_gate_note(config)
    return f"""# Workspace contract ({workdir})

<!-- Generated from the active config by `hiagentresearch render-workspace-docs`. Do not edit by hand. -->

Two parts: **Goals and expectations** (authoritative — from `research_output_expectations`
in the active config) and **Workspace skeleton** (paths, eval wiring, protected zones).
Cycle mechanics live in `{framework_guidance}`.

## Goals and expectations

{expectation_lines}

## Workspace skeleton

Only the configured editable paths are agent-owned. The rest of `{workdir}/` is
read-only context for understanding imports, integration, and scoring behavior.

### Editable paths

{editable_lines}

### Dependency files

{dependency_lines}

### How you are evaluated

After your cycle, the orchestrator (and GitHub eval node) runs this exact command:

```bash
{command}
```

{frozen_gate_note}

It prints a canonical JSON report to stdout. Scored metrics ({metric_fields}):

{targets_block}

The eval reads `passed` / `execution_passed` health flags plus those metric keys.

### Read-only authority and context

{reference_lines}

Read these to see how inference is loaded and scored. Never edit or run them.
The broader read-only context includes held-out eval assets under `eval/`,
operator-curated reference packs under `{workdir}/ref/`, shared layers,
providers, shell/worker wiring, and secrets.

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
