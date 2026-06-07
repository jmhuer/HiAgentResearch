# Repo Migration Checklist

Use this checklist when you want to run the HiAgentResearch runtime against a different repository.

## 1) Prerequisites

- Python virtualenv with runtime installed (`pip install -e .[dev]`)
- `gh` CLI authenticated to the target GitHub host/org
- Cursor API key available to the runtime (`CURSOR_API_KEY`)
- Target repository has Actions enabled and allows workflow runs

## 2) Create the project config

Start from `configs/standard.yaml` and adjust:

- `project_id`
- `workdir` (agent-owned code area)
- `dependency_files`
- `evaluation.entrypoint`
- `evaluation.command_template`
- `evaluation.targets`
- `research_groups`
- `orchestration.baseline_ref`
- `github.workflow_name`
- `github.remote` (must match an actual `git remote` name in that repo, usually `origin`)
- `dashboard.enabled` / `dashboard.metrics` / `dashboard.output_dir`

Validate before running:

```bash
HIAGENTRESEARCH_CONFIG=configs/<your-config>.yaml python -m hiagentresearch.src.core.config validate
```

## 3) Verify remotes and credentials

- `git remote -v` includes the remote named in config (`github.remote`)
- `gh auth status` succeeds
- `gh variable list --repo <owner/repo>` is accessible

If using a non-default config in CI, set:

```bash
gh variable set HIAGENTRESEARCH_CONFIG --body "configs/<your-config>.yaml" --repo <owner/repo>
```

## 4) Install workflow files

Required:

- `.github/workflows/hiagentresearch-mnist-phase1.yml` (or equivalent eval workflow with `name` matching `github.workflow_name`)

Optional:

- `.github/workflows/hiagentresearch-dashboard.yml` for GitHub Pages publish
- `*-selfhosted.yml` backups for manual self-hosted execution

On github.com, use `actions/upload-artifact@v4`.

## 5) Prepare runtime state

```bash
HIAGENTRESEARCH_CONFIG=configs/<your-config>.yaml bash scripts/clean_slate.sh
.venv/bin/hiagentresearch init
```

## 6) Run the pipeline

```bash
CONFIG=configs/<your-config>.yaml bash scripts/start_loops_all_parallel.sh
```

Monitor:

```bash
pgrep -af "hiagentresearch loops-all"
gh run list --workflow hiagentresearch-research-eval --limit 10
```

## 7) Dashboard checks

Local smoke build:

```bash
.venv/bin/hiagentresearch dashboard --config configs/<your-config>.yaml build \
  --state-dir .hiagentresearch/state \
  --output-dir .hiagentresearch/dashboard-preview \
  --prefer-json
```

If publishing via Actions, confirm:

- `dashboard.enabled: true` in active config
- `HIAGENTRESEARCH_CONFIG` Actions variable points to that config
- Pages permissions are enabled for the dashboard workflow

## 8) Common failure modes

- `unknown group_id` in CI: workflow loaded the wrong config (fix `HIAGENTRESEARCH_CONFIG`)
- baseline bootstrap fails immediately: eval workflow failed on `main`; inspect with `gh run view <id> --log-failed`
- push/lookup failures: `github.remote` does not exist or points to wrong repo
- no dashboard updates: dashboard disabled in config or workflow cannot find artifacts for current session

