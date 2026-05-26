# Phase 1 Design Contract

## Primary objective

Deliver a production-grade minimum runtime for one research-group cycle on MNIST with transparent evidence and deterministic artifacts.

## Runtime flow

1. Load the canonical project contract from root `config.yaml`.
2. Load current intent packet for group (or seed first packet).
3. Record agent actions to `agent_actions.jsonl` (traceability).
4. Trigger evaluation command (`.hiagentresearch/eval/run_phase1_eval.py` for phase 1).
5. Normalize outputs into canonical artifacts.
6. Write:
   - run metadata,
   - normalized metrics,
   - execution failure classification,
   - research outcome,
   - branch-local experiment manifest,
   - updated intent packet,
   - append-only event log entry.

## Config contract

`config.yaml` is the stitch point between a project and the generic runtime. It owns:

- project id and workdir,
- editable paths,
- project-owned dependency files,
- frozen eval entrypoint,
- eval command template and parser,
- research groups,
- artifact contract,
- policy modes,
- agent context and quality expectations.

Core runtime code should stay project-agnostic. If a project needs different files, prompts, or eval behavior, change config or the frozen eval adapter rather than hardcoding a new path in Python.
Project experiments may add dependencies through their configured requirements file; do not add experiment-only packages to the core runtime dependencies.

## Experiment Memory

Each run has exactly one canonical hypothesis in `.hiagentresearch/runs/<run_id>/experiment_intent.json`.
The loop controller copies the concise branch record to `.hiagentresearch/experiments/<group_id>/<run_id>.json`
before committing the experiment branch. Do not maintain accumulating Python lists for hypotheses or markers.

## Evidence requirement

Each cycle must include evidence references in `evidence.json`:

- at least one `code` evidence item, and
- optional `web` evidence items for external backing.

The orchestrator does not invent evidence; it only validates/persists it.

## Outcome language

`failure_class` is reserved for execution health: `none`, `infra_failure`,
`code_failure`, `eval_failure`, or `invalid_cycle`. A valid experiment that
does not improve baseline is not a failure; it records
`research_outcome=did_not_improve_baseline` and continues adding evidence to
the research branch unless the agent explicitly chooses a revert.

## No-shortcuts policy

- Do not mark runs successful if eval artifacts are missing.
- Do not bypass failed evals with manual pass flags.
- Do not hide execution failures; classify as `infra_failure`, `code_failure`, or `eval_failure`.
- Do not call metric regressions execution failures; record them as research outcomes.
- If the runtime cannot execute the intended path, surface the blocker as explicit run output.
- Do not fix contract failures with ad-hoc guardrails. Strengthen the canonical config, eval adapter, registry invariant, or operator command.
