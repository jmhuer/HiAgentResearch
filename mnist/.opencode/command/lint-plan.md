# Lint plan.json (pre-gate)

Run the PlanDefinition linter before the official review committee gate:

```
PYTHONPATH=<repo-root> python -m hiagentcontrol.tools.lint_plan_json --workdir . --num-tasks N
```

Replace `N` with the required task count. Fix all reported issues, re-run lint until `PLAN LINT: PASS`, then proceed to the gate phase.

This is the JSON equivalent of `lsp_diagnostics` for `plan.json` — use **lint**, not LSP, for schema validation.
