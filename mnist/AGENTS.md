# Workspace contract (mnist)

<!-- Generated from config.yaml by `hiagentresearch render-workspace-docs`. Do not edit by hand. -->

This workspace (`mnist/`) is yours. You may add, modify, restructure, and
delete files anywhere under it: add modules, add tests under `mnist/src/tests/`
or `mnist/tests/`, add dependencies to the requirements file, and reorganize
code to support your hypothesis.

## How you are evaluated

After your cycle, the orchestrator (and GitHub eval node) runs this exact command:

```bash
python .hiagentresearch/eval/run_phase1_eval.py --workdir mnist --group-id <group_id> --quick
```

It prints a canonical JSON report to stdout and you are scored on these target
fields (`accuracy`, `latency_ms`):

- `accuracy` >= 0.985
- `latency_ms` <= 13.0

The eval reads `passed` / `execution_passed` health flags plus those metric keys
from the JSON report. You do not need to call the parser yourself.

## The eval zone is read-only

Scoring, model loading, preprocessing, and deployment code live in:

- `.hiagentresearch/eval/`
- `.hiagentresearch/eval/run_phase1_eval.py`

Read these files to understand exactly how your model is loaded, what
preprocessing is applied at inference, and how each metric is computed. Never
edit or run them: the orchestrator runs the eval after your cycle and that result
is authoritative. Editing the eval zone is rejected as an invalid cycle.

## Feedback loop

- Write and run your own quick unit/smoke tests for fast feedback before the
  authoritative eval.
- Keep your own feedback cheap and CPU-bounded; do not launch long training runs.
- Treat metric regressions as research evidence, not execution failures.
