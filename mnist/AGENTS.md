# Workspace contract (mnist)

<!-- Generated from the active config by `hiagentresearch render-workspace-docs`. Do not edit by hand. -->

This workspace (`mnist/`) is yours. You may add, modify, restructure, and
delete files anywhere under it: add modules, add tests under `mnist/src/tests/`
or `mnist/tests/`, add dependencies to the requirements file, and reorganize
code to support your change.

## How you are evaluated

After your cycle, the orchestrator (and GitHub eval node) runs this exact command:

```bash
python .hiagentresearch/eval/run_phase1_eval.py --workdir mnist --group-id <group_id> --quick
```

It prints a canonical JSON report to stdout and you are scored on these metrics
(`accuracy`, `latency_ms`):

- `accuracy` — higher is better
- `latency_ms` — lower is better

Optimize for relative progress (improve over the current best; for engineering and
merge work, hold the metric where it is) — there is no absolute bar to hit per cycle.
The eval reads `passed` / `execution_passed` health flags plus those metric keys from
the JSON report. You do not need to call the parser yourself.

## The eval zone is read-only

Scoring, model loading, preprocessing, and deployment code live in:

- `.hiagentresearch/eval/`
- `.hiagentresearch/eval/run_phase1_eval.py`

Read these files to understand exactly how your model is loaded, what
preprocessing is applied at inference, and how each metric is computed. Never
edit or run them: the orchestrator runs the eval after your cycle and that result
is authoritative. Editing the eval zone is rejected as an invalid cycle.

For how to work a cycle (planning, self-review, smoke tests, what counts as a
regression, git boundaries), follow the framework contract in `hiagentresearch/AGENTS.md`.
