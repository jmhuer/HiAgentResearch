from __future__ import annotations

from pathlib import Path

from hiagentresearch.src.agents.task_contract import task_contract
from hiagentresearch.src.lineage.resolve import BranchBootstrap
from hiagentresearch.src.core.models import IntentPacket, ResearchGroup, ScoreContext


def build_research_cycle_prompt(
    *,
    group: ResearchGroup,
    intent_packet: IntentPacket,
    run_id: str,
    checkout_root: Path | None = None,
    lineage_bootstrap: BranchBootstrap | None = None,
    score_context: ScoreContext | None = None,
) -> str:
    run_plan_md = f".hiagentresearch/runs/{run_id}/cycle_plan.md"
    run_intent_json = f".hiagentresearch/runs/{run_id}/cycle_intent.json"
    workdir = group.workdir.rstrip("/") or "."
    active_checkout = (checkout_root or Path.cwd()).resolve()

    guidance_files = list(group.guidance_files)
    if group.workspace_agents_path and group.workspace_agents_path not in guidance_files:
        guidance_files = [group.workspace_agents_path, *guidance_files]
    guidance_text = _bullets(guidance_files)
    reference_text = _bullets(group.reference_paths)
    generated_paths_text = _bullets(group.generated_paths)
    workspace_agents = group.workspace_agents_path or f"{workdir}/AGENTS.md"
    lineage_text = _lineage_stanza(lineage_bootstrap)
    contract = task_contract(group.task_kind)
    grounding_text = _grounding_stanza(score_context, preserve_metrics=contract.preserve_metrics)
    policy_line = f"Policy mode: {group.policy_mode}"
    if group.policy_mode_description:
        policy_line += f" — {group.policy_mode_description}"
    note = intent_packet.last_note or ""
    feedback_ref = intent_packet.last_feedback_ref or ""
    feedback_lines = []
    if note:
        feedback_lines.append(f"Feedback from last CI eval: {note}")
    if feedback_ref:
        feedback_lines.append(
            f"CI artifact bundle: `{feedback_ref}` — if execution was blocked, read diagnostics.json "
            "then follow attachments for detail; parsed_eval.json is metrics only."
        )
    note_line = "\n".join(feedback_lines)
    if note_line:
        note_line += "\n"
    # A group's optional `change_scope` REPLACES the task kind's default scope outright, so
    # there is only ever one scope statement (no competing rules to contradict).
    scope_text = (group.change_scope or "").strip() or contract.default_scope

    return (
        f"You are the {contract.agent_role} for group '{group.id}'.\n"
        f"Run ID: {run_id}\n"
        f"Objective: {group.objective}\n"
        f"{policy_line}\n"
        f"Current {contract.intent_noun} id: {intent_packet.active_goal_id}\n"
        f"Current {contract.intent_noun}: {intent_packet.goal_text}\n"
        f"Previous failure class: {intent_packet.last_failure_class}\n"
        f"Next action: {intent_packet.next_action}\n"
        f"{note_line}\n"
        f"{grounding_text}"
        f"{lineage_text}"
        "Read and follow these guides before editing (most specific first):\n"
        f"{guidance_text}\n\n"
        f"Your workspace is `{workdir}/`; you own its editable paths. {contract.cycle_instruction}\n"
        f"{contract.metric_directive}\n\n"
        f"Scope this cycle: {scope_text}\n\n"
        f"Authoritative goals and expectations are in `{workspace_agents}` "
        "(## Goals and expectations) — read that section before editing.\n\n"
        "Write these planning artifacts in the current checkout before editing code:\n"
        f"- {run_intent_json}: run_id, group_id, objective, goal_id, goal,\n"
        "  evidence_refs, planned_code_changes,\n"
        f"  target_files (all under `{workdir}/`), success_criteria, rollback_plan.\n"
        f"- {run_plan_md}: headings ## Evidence, ## Planned Edit, ## Risk and Rollback, {contract.plan_heading}.\n"
        f"  In {contract.plan_heading}, {contract.plan_expectation}\n\n"
        "Read-only scoring references:\n"
        f"{reference_text}\n\n"
        "Boundaries:\n"
        f"- Your current checkout root is `{active_checkout}`. All relative paths in this prompt and "
        "the guide files resolve inside this checkout; do not use a parent checkout's "
        "`.hiagentresearch/` directory for this run.\n"
        "- Git and edit boundaries are defined in `.hiagentresearch/AGENTS.md` (read it first): leave your "
        "edit UNCOMMITTED and do not move HEAD — the orchestrator commits after the cycle; read-only git "
        "(`git diff`, `git show`, `git log`) is fine.\n"
        "- The scoring references above are read-only: read them to understand how you are scored, "
        "but never edit or run them; the GitHub eval node owns metric-producing evaluation.\n"
        "- Do not create branch-memory source files; the runtime records your intent.\n"
        f"- Edit only files under `{workdir}/`, excluding protected paths named in the workspace guide, "
        "and don't run package/dependency managers (e.g. `uv`, "
        "`pip install`) — the environment is already set up; tools that write lockfiles outside the "
        "workspace are ignored, so keep all changes inside your workspace.\n"
        "- These generated paths may appear while testing but are never committed as source changes:\n"
        f"{generated_paths_text}\n\n"
        "When your intent JSON, plan, and edit are in place, re-read your own diff and run a quick "
        "smoke check, then stop and return a short summary of what you changed. Don't keep exploring "
        "once the change is reviewed and complete.\n"
    )


def _grounding_stanza(score: ScoreContext | None, *, preserve_metrics: bool) -> str:
    """Numeric gradient for the agent: where the metric stands now, where it has been,
    the floor, and which attempt this is.

    Frames progress as *relative* movement, never an absolute target — the configured
    target is unreachable in the quick-eval regime, so handing it to the agent only
    manufactures phantom failures and panic moves. The metric's ROLE is task-kind aware:
    for cycles it is the objective to beat; for engineering/merge (``preserve_metrics``)
    it is a guardrail to hold, not a number to chase."""
    if score is None or not score.metric_name:
        return ""
    direction = "lower is better" if score.minimize else "higher is better"
    lines = [f"Scoreboard for `{score.metric_name}` ({direction}):"]
    if score.baseline_value is not None:
        lines.append(f"- Baseline (L0): {score.baseline_value:.4g}")
    if score.inherited_floor is not None:
        floor_note = (
            "hold the metric at or above this — it is a guardrail, not a number to chase."
            if preserve_metrics
            else "do NOT regress below this; it is the protected best-so-far."
        )
        lines.append(f"- Inherited floor (your starting commit): {score.inherited_floor:.4g} — {floor_note}")
    if score.trajectory:
        points = ", ".join(f"loop {loop}: {value:.4g}" for loop, value in score.trajectory)
        best = (min if score.minimize else max)(value for _, value in score.trajectory)
        lines.append(f"- This group's committed scores so far: {points}")
        if preserve_metrics:
            lines.append(f"- Best held so far: {best:.4g} — keep at or above it while you improve the code.")
        else:
            lines.append(f"- Best so far in this group: {best:.4g} — your job is to beat it.")
        # Persist-but-vary: only meaningful once there is committed history not to repeat.
        lines.append(
            "- These attempts are already on the board: stay with the direction, but don't "
            "resubmit one that already scored — build on what they taught you and vary the next move."
        )
    else:
        lines.append("- No committed scores yet in this group; establish the first data point.")
    if score.total_attempts > 1:
        lines.append(f"- This is attempt {score.attempt_index} of {score.total_attempts}.")
        if score.attempt_index >= score.total_attempts:
            lines.append(
                "  Final attempt — consolidate the best change you have evidence for rather "
                "than opening a speculative new direction."
            )
    lines.append(
        "Treat this number as a guardrail: improve quality/integration and keep the metric from "
        "dropping."
        if preserve_metrics
        else "Moving this number is the whole point: optimize for relative movement — beat your own "
        "best and stay above the floor, not an absolute target."
    )
    return "\n".join(lines) + "\n\n"


def _lineage_stanza(bootstrap: BranchBootstrap | None) -> str:
    if not bootstrap or bootstrap.mode == "baseline":
        return ""
    if getattr(bootstrap, "merge_sources", ()):
        return _merge_stanza(bootstrap)
    parent = bootstrap.parent_group_id or "unknown"
    short_sha = bootstrap.start_ref[:7]
    policy = bootstrap.anchor_policy or "best_commit"
    return (
        f"Lineage: this group continues from '{parent}' at commit {short_sha} ({policy}). "
        "Build on that state; do not reset unrelated files unless the change requires it.\n\n"
    )


def _merge_stanza(bootstrap: BranchBootstrap) -> str:
    base = bootstrap.parent_group_id or "the strongest lineage"
    metric = bootstrap.anchor_metric or "metric"
    lines = []
    for source in bootstrap.merge_sources:
        sha = str(source.get("commit_sha", ""))[:7]
        gid = source.get("source_group_id") or source.get("group_id", "")
        branch = source.get("source_branch") or source.get("branch", "")
        value = source.get("metric_value")
        suffix = f", {metric}={value:.4g}" if isinstance(value, (int, float)) else ""
        lines.append(f"- {gid} (branch {branch}) at {sha}{suffix}")
    sources = "\n".join(lines) or "- (no other lineage had a winning commit)"
    return (
        f"Merge: this branch starts from the strongest lineage '{base}' at commit "
        f"{bootstrap.start_ref[:7]}. Integrate the following lineages' improvements, in this "
        f"priority order (strongest first):\n{sources}\n"
        "For each, inspect what it adds with `git diff HEAD..<commit>` (and `git show <commit>`), "
        "then fold in the compatible improvements — one bounded integration step per cycle, "
        "building on what is already merged. Do not reset unrelated files.\n"
        "Once every source above is integrated, do NOT return empty: keep improving the merged "
        "result — reconcile rough edges between the combined changes and strengthen the integrated "
        "behavior. The strongest result is always preserved, so continued refinement is safe.\n\n"
    )


def _bullets(items: list[str]) -> str:
    if not items:
        return "- (none configured)"
    return "\n".join(f"- {item}" for item in items)
