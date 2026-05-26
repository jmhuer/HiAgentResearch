# Phase 1 Design Contract

## Primary objective

Deliver a production-grade minimum runtime for one research-group cycle on MNIST with transparent evidence and deterministic artifacts.

## Runtime flow

1. Load group charter from `.hiagentresearch/state/research_groups.json`.
   New runs load the canonical project contract from root `config.yaml`; the
   legacy JSON file is retained only as historical state.
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

## Config contract

`config.yaml` is the stitch point between a project and the generic runtime. It owns:

- project id and workdir,
- editable paths,
- frozen eval entrypoint,
- eval command template and parser,
- research groups,
- artifact contract,
- policy modes,
- agent context and quality expectations.

Core runtime code should stay project-agnostic. If a project needs different files, prompts, or eval behavior, change config or the frozen eval adapter rather than hardcoding a new path in Python.

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
- Do not fix contract failures with ad-hoc guardrails. Strengthen the canonical config, eval adapter, registry invariant, or operator command.
