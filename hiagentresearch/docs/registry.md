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

## Phase 2 Ambition

Phase 2 can publish a static dashboard from GitHub Pages. The likely shape is:

1. Copy a compact, read-only SQLite database or exported snapshot into a Pages artifact.
2. Load that database in the browser with a WebAssembly SQLite reader such as `sql.js-httpvfs`.
3. Render metric trajectories by research group, latest outcomes, branches, workflow IDs, and artifact links.

GitHub Pages can host static database files, but it is read-only from the browser. Publishing should happen from
GitHub Actions after registry export, and large artifact payloads should stay outside the database with links or hashes.
