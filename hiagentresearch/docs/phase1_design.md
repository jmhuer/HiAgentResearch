# Phase 1 Design Contract

## Primary objective

Deliver a production-grade minimum runtime for one research-group cycle on MNIST with transparent evidence and deterministic artifacts.

## Runtime flow

1. Load group charter from `.hiagentresearch/state/research_groups.json`.
2. Load current intent packet for group (or seed first packet).
3. Record agent actions to `agent_actions.jsonl` (traceability).
4. Trigger evaluation command (`.hiagentresearch/eval/run_phase1_eval.py` for phase 1).
5. Normalize outputs into canonical artifacts.
6. Write:
   - run metadata,
   - normalized metrics,
   - failure classification,
   - updated intent packet,
   - append-only event log entry.

## Evidence requirement

Each cycle must include evidence references in `evidence.json`:

- at least one `code` evidence item, and
- optional `web` evidence items for external backing.

The orchestrator does not invent evidence; it only validates/persists it.

## No-shortcuts policy

- Do not mark runs successful if eval artifacts are missing.
- Do not bypass failed evals with manual pass flags.
- Do not hide failures; classify as `infra_failure`, `code_failure`, or `eval_failure`.
- If the runtime cannot execute the intended path, surface the blocker as explicit run output.
