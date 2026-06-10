# Future Ambitions

Tracked ideas to pursue *after* the current system is verified and cleaned up. None of
these are started yet — this file is just the backlog so we don't lose them.

## 1. One-command repo scaffolding

Add a `hiagentresearch scaffold` (a.k.a. `onboard`) command that generates the skeleton
for a new project in one step:

- a `configs/standard.yaml` template (workdir, evaluation entrypoint/command/targets, research
  groups, orchestration, github, agent, dashboard),
- a stub frozen eval adapter under `.hiagentresearch/eval/` that prints canonical JSON,
- the GitHub workflows, and the workspace `AGENTS.md`.

**Why:** onboarding a new repo is currently a manual replication of the skeleton (see
the 15-minute path in `hiagentresearch/docs/new-repo-onboarding.md`). A scaffold command
makes the tool genuinely turnkey for a new use case.

## 2. Package distribution polish

`hiagentresearch init` now materializes the framework guidance contract from the
installed runtime into `.hiagentresearch/AGENTS.md`, so prompts use a stable
project-facing path regardless of whether the runtime is vendored or installed.

The remaining distribution work is packaging polish: make the CLI installable as
`hiagentresearch`, document the config/eval skeleton expected in a target repo, and
ensure the source `hiagentresearch/AGENTS.md` is always included as package data.

**Why:** clean separation of "the tool" from "the repo being researched"; the tool moves
repo to repo as a dependency, not a copy.

## 3. Refresh the onboarding doc to the full config contract

`hiagentresearch/docs/new-repo-onboarding.md` predates several knobs that are now
config-driven. Document them all so a new user has a single accurate contract:

- `orchestration.baseline_ref` (which branch/ref the baseline + L0 lineage builds from),
- `github.remote` (drives both `git push` and the derived `gh --repo` target),
- the `agent` section (`model`, `thinking`, retries, timeouts),
- `dashboard` (metrics default to `evaluation.targets`; `discrete_metrics`),
- the lineage policy split (`inherit_policy` vs `top_commit_policy`).

**Why:** the runtime is now fully metric- and project-agnostic; the docs should make that
self-service.

## 4. Cross-lineage merge run — IMPLEMENTED (2026-06)

Shipped as the `merge` task kind: a build task that combines the strongest commit of
every lineage into one branch. It is **near-zero config** — `objective`, base
(`inherit_from`), and `draw_from` are auto-resolved at run time: the orchestrator ranks
each lineage's winner by the anchor metric, starts the branch from the strongest, and
integrates the rest best→worst via git (`git diff HEAD..<sha>`). Reuses the inherit
model (single parent → linear chain, no DAG), the engineering preserve-metrics
regression→repair, and `best_commit` (so a failed merge keeps the strongest source as
the star). Enable via the commented `merge_best` group + final wave in `configs/standard.yaml`.

Remaining follow-ups (not blocking):
- **Merge loop budget vs. integration steps:** a merge collapses N lineages through N-1
  sequential integration steps (base + fold in the rest), but the loop count is the
  uniform `--loops` (default 3), not `N-1`. So ≤4 lineages integrate within the default,
  but 5+ lineages (4+ steps) won't finish at 3 loops. Consider auto-sizing a merge group's
  loops to `max(default, N-1)` (or a per-group `loops` override) so every source is folded
  in regardless of lineage count.
- **Multi-metric merge floor:** today regression is judged on the anchor metric vs the
  inherited base; a "hold every source on its own best metric" guarantee would confirm
  gains are truly combined across metrics.
- **Multi-generation collapse (north star):** promote a merge's top commit to the next
  generation's `baseline_ref` (or a `promote` step) so the merged best becomes the new
  L0 and research continues from a collapsed baseline.
- **Sequential/recursive merges as an auto-discovered generation:** chaining merges
  (each inheriting the previous) is already expressible; auto-orchestrating successive
  generations is the natural extension.

## 5. GHES-compatible dashboard publishing

The dashboard publish path uses GitHub Pages via Actions
(`actions/configure-pages`, `actions/upload-pages-artifact`, `actions/deploy-pages`),
which — like the v4 artifact backend — is generally **not supported on GitHub Enterprise
Server**. On GHES (e.g. github.disney.com) the workflow is run with `dashboard.enabled:
false` and the dashboard is reviewed locally (`dashboard build --prefer-json` +
`scripts/preview_dashboard.sh`), so this is not blocking — but published dashboards on
GHES need a different mechanism.

Options to design later:
- publish the static bundle to a GHES Pages site if the instance supports it,
- or push it to an internal static host / object store / a `gh-pages`-style branch,
- gate the publish mechanism by config so github.com keeps using Actions→Pages and GHES
  uses the alternative.

**Context:** the standard GHES gaps the tool already handles are no hosted runners
(use self-hosted via `HIAGENTRESEARCH_RUNNER`), the `setup-python` `/Users/runner` path
(created by `scripts/setup_self_hosted_runner.sh`), and no v4 artifacts (pinned `@v3`).
Pages publishing is the remaining GHES gap.

## 6. Heartbeat / health pings for true run liveness

The dashboard's run-status chip (green "Live" / red "Complete") and the elapsed
**Duration** are derived from the orchestration session at **build time**: `loops-all`
stamps `completed_at` on exit, and the static page reflects whatever was true when it
was last built. That cleanly distinguishes *finished* from *in-progress*, but it cannot
tell **healthy-and-running** from **stalled or dead** (e.g. the machine slept, the loop
hung, the process was killed without stamping `completed_at` — the page keeps showing a
stale green "Live").

Add a periodic heartbeat the running loop emits — e.g. `loops-all` writes a
`last_heartbeat_at` into the orchestration session every N seconds (and on each cycle
boundary). Then:

- the status chip can show three states — **Live** (recent heartbeat), **Stalled**
  (heartbeat older than a threshold), **Complete** (`completed_at` set) — instead of
  two,
- the **Live** Duration can tick in real time in the browser (count from baseline start
  to *now* while live, freeze at `completed_at` when done) without misrepresenting a
  dead run as live,
- a publish/refresh loop could rebuild the dashboard on a cadence so a viewer sees
  progress without manual rebuilds.

**Why:** makes "is this actually running right now?" trustworthy rather than a snapshot
artifact. Keep it config-driven (heartbeat interval, stalled threshold) and host-agnostic;
no OS-specific liveness probing in the framework.

## 7. Metric-preserving top commit for engineering (last-cycle regression)

Engineering tasks must preserve the metrics they inherited: the loop detects a regression
below the inherited floor and steers the *next* cycle to repair it (see TaskContract
`preserve_metrics`). But a `last_commit` engineering group (e.g. `polish_code`) whose
**final** cycle regresses has no next cycle to repair it — and `last_commit` makes that
regressed commit the trajectory top.

Options to design later (not a priority — there are workarounds today, e.g. run an extra
loop, or treat the inherited anchor as the fallback top):

- **Top commit = latest metric-preserving commit** for engineering: skip trailing commits
  that regressed below the floor when selecting the `last_commit` top.
- **Acceptance threshold:** a config tolerance band so small/noise-level metric moves are
  accepted, and only commits within the band are eligible to be the top commit.
- Gate by `preserve_metrics` so metric_experiment groups are unaffected (their latest is
  always a valid top, regressions are findings).

**Why:** guarantees an engineering trajectory's published top commit never shows a metric
regression, even when the last cycle slipped. Keep it config-driven and direction-aware
(reuse `EvaluationConfig.metric_minimizes`).
