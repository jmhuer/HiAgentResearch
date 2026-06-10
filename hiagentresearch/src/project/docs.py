"""Generate the workspace-facing AGENTS.md from config.

The workspace AGENTS.md is the single, honest description of the agent's
contract for a project: the workspace is theirs, the eval zone is read-only, and
they are told the exact command and targets that will judge them. It is derived
from the active config file so it stays correct when targets or the eval command change.
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
    # Show each scored metric and its DIRECTION only — deliberately not the absolute
    # pass/fail bound. In the quick-eval regime the absolute target is unreachable, and
    # handing it to the agent invites panic moves; the per-cycle scoreboard already drives
    # relative progress (beat your best / hold the floor).
    lines: list[str] = []
    for name in config.evaluation.targets:
        direction = "lower is better" if config.evaluation.metric_minimizes(name) else "higher is better"
        lines.append(f"- `{name}` — {direction}")
    return lines or ["- (no metrics configured)"]


def render_workspace_agents(config: HiAgentResearchConfig) -> str:
    workdir = config.workdir.rstrip("/") or "."
    metric_fields = ", ".join(f"`{name}`" for name in config.evaluation.targets) or "the configured metrics"
    reference_lines = "\n".join(f"- `{path}`" for path in config.all_reference_paths())
    hidden_lines = "\n".join(f"- `{path}`" for path in config.hidden_paths) or "- (none configured)"
    expectation_lines = (
        "\n".join(f"- {item}" for item in config.agent_contract.research_output_expectations)
        or "- Follow the objective and keep changes inside the workspace."
    )
    targets_block = "\n".join(_targets_lines(config))
    command = _display_eval_command(config)
    return f"""# Workspace contract ({workdir})

<!-- Generated from the active config by `hiagentresearch render-workspace-docs`. Do not edit by hand. -->

This workspace (`{workdir}/`) is yours. You may add, modify, restructure, and
delete files anywhere under it: add modules, add tests under `{workdir}/src/tests/`
or `{workdir}/tests/`, add dependencies to the requirements file, and reorganize
code to support your change.

## How you are evaluated

After your cycle, the orchestrator (and GitHub eval node) runs this exact command:

```bash
{command}
```

It prints a canonical JSON report to stdout and you are scored on these metrics
({metric_fields}):

{targets_block}

Optimize for relative progress (improve over the current best; for engineering and
merge work, hold the metric where it is) — there is no absolute bar to hit per cycle.
The eval reads `passed` / `execution_passed` health flags plus those metric keys from
the JSON report. You do not need to call the parser yourself.

## The eval zone is read-only

Scoring, model loading, preprocessing, and deployment code live in:

{reference_lines}

Read these files to understand exactly how your model is loaded, what
preprocessing is applied at inference, and how each metric is computed. Never
edit or run them: the orchestrator runs the eval after your cycle and that result
is authoritative. Editing the eval zone is rejected as an invalid cycle.

## Other protected paths

These paths are available for context when they exist, but are not part of your
editable workspace for this research phase:

{hidden_lines}

## Research expectations

{expectation_lines}

For how to work a cycle (planning, self-review, smoke tests, what counts as a
regression, git boundaries), follow the framework contract in `hiagentresearch/AGENTS.md`.
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
