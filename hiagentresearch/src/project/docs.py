"""Generate the workspace-facing AGENTS.md from config.

The workspace AGENTS.md is the single, honest description of the agent's
contract for a project: the workspace is theirs, the eval zone is read-only, and
they are told the exact command and targets that will judge them. It is derived
from `config.yaml` so it stays correct when targets or the eval command change.
"""

from __future__ import annotations

from pathlib import Path

from hiagentresearch.src.core.config import HiAgentResearchConfig, load_config
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
    lines: list[str] = []
    for name, expectation in config.evaluation.targets.items():
        bounds: list[str] = []
        if expectation.min is not None:
            bounds.append(f">= {expectation.min}")
        if expectation.max is not None:
            bounds.append(f"<= {expectation.max}")
        suffix = " and ".join(bounds) if bounds else "(no bound configured)"
        lines.append(f"- `{name}` {suffix}")
    return lines or ["- (no targets configured)"]


def render_workspace_agents(config: HiAgentResearchConfig) -> str:
    workdir = config.workdir.rstrip("/") or "."
    metric_fields = ", ".join(f"`{name}`" for name in config.evaluation.targets) or "the configured metrics"
    reference_lines = "\n".join(f"- `{path}`" for path in config.all_reference_paths())
    targets_block = "\n".join(_targets_lines(config))
    command = _display_eval_command(config)
    return f"""# Workspace contract ({workdir})

<!-- Generated from config.yaml by `hiagentresearch render-workspace-docs`. Do not edit by hand. -->

This workspace (`{workdir}/`) is yours. You may add, modify, restructure, and
delete files anywhere under it: add modules, add tests under `{workdir}/src/tests/`
or `{workdir}/tests/`, add dependencies to the requirements file, and reorganize
code to support your hypothesis.

## How you are evaluated

After your cycle, the orchestrator (and GitHub eval node) runs this exact command:

```bash
{command}
```

It prints a canonical JSON report to stdout and you are scored on these target
fields ({metric_fields}):

{targets_block}

The eval reads `passed` / `execution_passed` health flags plus those metric keys
from the JSON report. You do not need to call the parser yourself.

## The eval zone is read-only

Scoring, model loading, preprocessing, and deployment code live in:

{reference_lines}

Read these files to understand exactly how your model is loaded, what
preprocessing is applied at inference, and how each metric is computed. Never
edit or run them: the orchestrator runs the eval after your cycle and that result
is authoritative. Editing the eval zone is rejected as an invalid cycle.

## Feedback loop

- Write and run your own quick unit/smoke tests for fast feedback before the
  authoritative eval.
- Keep your own feedback cheap and CPU-bounded; do not launch long training runs.
- Treat metric regressions as research evidence, not execution failures.
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
