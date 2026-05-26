---
name: hac-format-plan-json
description: Format draft.md into plan.json; summarize structured sections; require goal_type.
---

# Input / output
- Read: `state/current/draft.md` (entire file)
- Write: `state/current/plan.json` (one **`write`** of the full JSON file — do not use repeated `edit`)

# How to read draft.md
1. **Narrative / thinking pad** (top): background, strategy, long-form task writeups (`### Task N:`).
2. **Structured candidates** (bottom): `## Candidate Improvement Tasks` or sections with **TRY / FILES / CHANGE / VERIFY** — **prefer these** for `scope` text when present.
3. Produce **exactly N tasks** as requested in the session prompt. If draft has more candidates, pick the best N; if fewer, derive additional tasks from narrative sections without inventing new science.

# PlanDefinition root (required)
- `plan_id` — e.g. `mnist-improvement-v1`
- `metadata` — `title`, `objective`, `workdir` (`mnist`), `source_draft_path`, `loop_attempt`
- `tasks` — non-empty array

# Each task (all fields required)
- `task` — short research area title (from draft task heading)
- `goal_type` — **required** on every task. Choose one:
  - `survey` — literature / benchmarks / web research only
  - `codebase_recon` — read/grep existing code, no training change yet
  - `experiment` — run train/eval change in `pipeline/`
  - `architecture` — model structure change in `pipeline/model.py`
  - `feature` — new training capability (augmentation, scheduler, etc.)
  - `hygiene` — tests, cleanup, regularization wiring
  - `ablation_study` — compare variants with same eval gate
- `scope` — single string with all four labels (compress draft prose; do not drop detail):
  - `TRY:` one concrete attempt
  - `FILES:` only paths that exist or will be created under `pipeline/` or `eval/` (no `mnist_cnn.py`; model is `pipeline/model.py`)
  - `CHANGE:` specific code/hyperparameter edits
  - `VERIFY:` `python eval/run_eval.py --quick` with numeric thresholds from `baseline.json`
- `context` — list of `{path, note}` for files cited
- `gate.script_checks` — MUST be objects, never strings. Use:

```json
"gate": {
  "script_checks": [
    {
      "kind": "script",
      "name": "eval-gate",
      "path": "python",
      "args": ["eval/run_eval.py", "--quick"],
      "threshold": "accuracy >= baseline + 0.001 and latency_ms <= 13.0"
    }
  ],
  "ai_eval": null
}
```
- `required_skills`, `required_tools`, `must_not_do` — use `[]` or minimal lists if draft omits them

# Rules
- **Summarize** long draft sections into tight scopes; preserve facts and thresholds from draft.
- Do NOT change research substance or invent phantom files.
- You **may** use subagents if helpful — deliverable is still only `plan.json`.
- Do **NOT** rely on `lsp_diagnostics` for `plan.json` (LSP is for code). Use **plan lint** instead.
- Do **NOT** run `run_plan_gate.py` (official gate / loop exit). Run **lint** repeatedly until pass:

```bash
PYTHONPATH=<repo-root> python -m hiagentcontrol.tools.lint_plan_json --workdir . --num-tasks N
```

- Copy shape from `hiagentcontrol/schemas/json/plan_example.json` when unsure.
