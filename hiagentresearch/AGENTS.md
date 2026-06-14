# HiAgentResearch Agent Contract

This project uses a Cursor-first research loop with a thin Python control-plane.

## Core cycle contract

1. Research and planning happen before code edits.
2. Each run writes planning artifacts under `.hiagentresearch/runs/<run_id>/`
   in the checkout where the agent is running:
   - `cycle_intent.json`
   - `cycle_plan.md`
3. Each run applies real, bounded code edits to the workspace; keep them reversible and
   syntactically valid, and make the change exactly as broad as your cycle prompt's scope
   says — no more, no less. No marker-only or no-op runs.
4. Each run includes a concise `.hiagentresearch/cycles/<group_id>/<run_id>.json`
   manifest, which the **orchestrator** commits for you — you do not commit anything yourself.
5. Do not create files to carry state between cycles; the runtime records your intent.
6. Every run leaves an auditable trail. The orchestrator runs the eval *after* your edit
   and that result is authoritative; you do not produce metric/eval artifacts yourself, and
   you do not claim an outcome you have not measured.

## Editing boundaries

- **The active checkout is the root.** You do not choose or move the git position. Treat the current checkout/cwd as "here"; all relative paths in prompts and guides resolve there, including `.hiagentresearch/runs/<run_id>/`.
- **The workspace (`workdir`) is yours.** Edit, add, restructure, and add tests or dependencies freely within it (its generated `AGENTS.md` lists the exact eval command and targets). Keep changes inside the workspace plus run-local observability artifacts; add a needed dependency to the workspace requirements file, not to core runtime dependencies.
- **The evaluation zone is read-only, and its contract is binding.** Read it to understand how you are scored, preserve the loaded entry points/signatures/output shapes, and never edit or run it. The orchestrator and GitHub eval nodes own metric-producing evaluation.
- **The eval runs your code as-is with fixed inputs — make changes take effect in code, not args/env.** The frozen eval command does not pass environment variables or CLI arguments you introduce; behavior gated on an unset env var or flag silently falls back to its default and your change is ignored. Land tunable behavior in workspace code (defaults and the wiring the eval already executes), not in runtime env/flags the eval never sets. Anything you build must be turned ON via code defaults in the same cycle so the frozen eval exercises it — leaving new functionality built-but-disabled is an incomplete cycle, not a safe one. When present, the eval report's effective-configuration block is the ground truth of what actually ran — confirm your change appears there rather than assuming it was honored.
- **Review and smoke-check before you finish (required).** Read back your own diff (`git diff`) and confirm it is internally consistent — symbols, shapes, signatures, and unpacking are correct, and nothing you introduced is unused. Then run a quick, cheap (CPU-bounded) smoke check — import or construct what you changed, or a fast unit test — to confirm it executes. This is your feedback loop; do **not** run the full evaluation or any long/expensive job (that is the eval node's). A crash a diff re-read or a 10-second construct would have caught is a wasted cycle.
- **Read-only git is encouraged; never change git state yourself.** Inspect freely — `git diff`, `git show`, `git log`, `git blame`, `git diff HEAD..<commit>`. What you must NOT do is *mutate* git or move HEAD: no `git add`, `git commit`, `git merge`, `git rebase`, `git reset`, `git stash`, `git checkout <ref>`, or `git push`. Make your edit and leave it **uncommitted** in the working tree; the orchestrator owns commits and branch history and commits it (with its manifest) after your cycle. Moving HEAD during a cycle fails the cycle with an `agent_moved_head` error. If a prior state is a worse basis to build on, fix it forward with a new edit — never with git history operations.
- **You don't pick a mode — you propose the next change.** The orchestrator carries each cycle's outcome forward and sets the next action; how a result counts (a finding to build on vs. a regression to repair) is defined per task kind in your cycle prompt, not here.

## Evidence expectations

- Ground each cycle in evidence: cite concrete code or metric-target references in the planning artifacts, and include measurable success criteria tied to the evaluation metrics.
- Research freely. Consult the web — documentation, papers, prior art — to ground a hypothesis or change in real evidence and current best practice; cite what you drew on in the planning artifacts.
