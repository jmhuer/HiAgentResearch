# Registry Inspection

Phase 1 keeps the registry local, simple, and queryable. The SQLite database lives at
`.hiagentresearch/state/evals.db`; full local run artifacts remain under `.hiagentresearch/runs/`.
Agents should use concise intent packets and branch manifests for context, not the full registry.

## Read Commands

```bash
hiagentresearch registry summary
hiagentresearch registry runs --group-id model_architecture
hiagentresearch registry show --run-id gh_123456789
hiagentresearch registry metrics --group-id model_architecture --metric accuracy
hiagentresearch registry artifacts --run-id gh_123456789
hiagentresearch registry export --json
hiagentresearch dashboard build
hiagentresearch dashboard build-from-artifacts --artifact-root dashboard-artifacts
```

Add `--json` to any command for machine-readable output.

## Schema Direction

The registry stores execution health and research outcome separately in `.hiagentresearch/state/evals.db`.
Runtime state is intentionally SQLite-only; durable experiment intent belongs in tracked branch manifests.

- `runs` records orchestration state, branch, commit, workflow run, and `failure_class`.
- `metrics` records numeric metric values by run.
- `research_outcomes` records whether the experiment improved the configured baseline and what action should happen next.
- `experiments` records the concise branch manifest: hypothesis, planned changes, target files, and manifest path.
- `artifacts` records artifact paths, types, hashes, and sizes without embedding large payloads in the database.

This keeps the database useful for humans and dashboards while keeping agent context small.
Metric rows come from deterministic parser output. Prefer frozen eval adapters
that emit canonical JSON and `evaluation.parser: canonical_json_stdout`.

## Static Dashboard

Phase 2 publishes an optional static dashboard from the registry. The dashboard
does not run during normal loops; it is controlled by the top-level `dashboard`
config block and explicit `hiagentresearch dashboard ...` commands.

The dashboard build writes:

- `dashboard.db` — sanitized read-only SQLite tables and dashboard views.
- `dashboard.json` — JSON fallback snapshot for the browser.
- `summary.json` — small first-paint payload.
- `manifest.json` — build metadata and cache-bust token.
- static `index.html`, `app.js`, and `styles.css`.

GitHub Pages publishing lives in a separate optional workflow. It collects recent
research artifacts, rebuilds a temporary registry, builds the dashboard bundle,
and deploys only when `dashboard.enabled: true`. Large artifact payloads stay out
of the published database; only metadata, hashes, metrics, outcomes, and
experiment intent are included.
