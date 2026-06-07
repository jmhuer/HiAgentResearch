# Phase 1 Design Contract

## Primary objective

Deliver a production-grade minimum runtime for one research-group cycle on MNIST with transparent evidence and deterministic artifacts.

## Runtime flow

1. Load the canonical project contract from `configs/standard.yaml` (or another selected config file).
2. Load current intent packet for group from SQLite (or seed first packet).
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
      - SQLite registry rows.

## Config contract

The active config file is the stitch point between a project and the generic runtime. It owns:

- project id and workdir,
- editable paths,
- project-owned dependency files,
- frozen eval entrypoint,
- agent validation commands,
- eval command template and parser,
- research groups,
- policy modes,
- optional dashboard publishing,
- agent context and quality expectations.

Core runtime code should stay project-agnostic. Framework artifact filenames live in `hiagentresearch/src/core/artifacts.py`; if a project needs different files, prompts, or eval behavior, change config or the frozen eval adapter rather than hardcoding a new path in Python.
Project experiments may add dependencies through their configured requirements file; do not add experiment-only packages to the core runtime dependencies.

## Eval Abstraction

Agent validation commands are optional feedback tools. They can run unit tests,
smoke training, quick evals, or import checks during an agent cycle, and they may
call editable project eval code. They are not the final authority.

The frozen eval command is the final authority. It lives outside the workspace
(in the read-only eval zone), runs locally during the control loop, and runs
again in GitHub Actions on the committed research branch. It emits canonical JSON
to stdout: a top-level object with `passed` / `execution_passed` flags plus the
metric keys named in `evaluation.targets`. The core deterministically reads those
fields from the JSON; project-specific report quirks belong in the frozen
adapter, not in core orchestration. There is no `parser` field — canonical JSON
is the single eval contract.

Project metric thresholds live in the active config file. Project eval scripts may emit
raw metrics, but the frozen adapter is responsible for passing configured
thresholds and writing run-local train metrics under the active run directory.

## Experiment Memory

Each run has exactly one canonical goal in `.hiagentresearch/runs/<run_id>/cycle_intent.json`.
The loop controller copies the concise branch record to `.hiagentresearch/cycles/<group_id>/<run_id>.json`
before committing the experiment branch. Do not maintain accumulating Python lists for hypotheses or markers.

## Registry Inspection

The registry is the operator-facing read model. It stores runs, metrics, research
outcomes, experiment manifests, and artifact metadata in SQLite while keeping full
run payloads on disk. Use `hiagentresearch registry summary` for a
quick health check, or see `hiagentresearch/docs/registry.md` for the full command set.

The static dashboard is an optional read layer over that registry. It must remain
isolated from agent prompts and loop execution; explicit dashboard commands or the
separate Pages workflow build the static bundle.

## Evidence requirement

Each cycle must include evidence references in `cycle_intent.json`:

- at least one `code` evidence item, and
- optional `web` evidence items for external backing.

The orchestrator does not invent evidence; it validates the planning artifact and
persists the durable experiment manifest on the research branch.

## Outcome language

`failure_class` is reserved for execution health: `none`, `infra_failure`,
`code_failure`, `eval_failure`, or `invalid_cycle`. A valid experiment that
does not improve the configured baseline is not a failure; it records
`research_outcome=did_not_improve_baseline` and continues adding evidence to
the research branch unless the agent explicitly chooses a revert.

## No-shortcuts policy

- Do not mark runs successful if eval artifacts are missing.
- Do not bypass failed evals with manual pass flags.
- Do not hide execution failures; classify as `infra_failure`, `code_failure`, or `eval_failure`.
- Do not call metric regressions execution failures; record them as research outcomes.
- If the runtime cannot execute the intended path, surface the blocker as explicit run output.
- Do not fix contract failures with ad-hoc guardrails. Strengthen the canonical config, eval adapter, registry invariant, or operator command.
