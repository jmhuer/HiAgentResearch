from __future__ import annotations

from hiagentresearch.src.lineage.resolve import BranchBootstrap
from hiagentresearch.src.core.models import IntentPacket, ResearchGroup


def build_phase1_prompt(
    *,
    group: ResearchGroup,
    intent_packet: IntentPacket,
    run_id: str,
    lineage_bootstrap: BranchBootstrap | None = None,
) -> str:
    run_plan_md = f".hiagentresearch/runs/{run_id}/experiment_plan.md"
    run_intent_json = f".hiagentresearch/runs/{run_id}/experiment_intent.json"
    workdir = group.workdir.rstrip("/") or "."

    guidance_files = list(group.guidance_files)
    if group.workspace_agents_path and group.workspace_agents_path not in guidance_files:
        guidance_files = [group.workspace_agents_path, *guidance_files]
    guidance_text = _bullets(guidance_files)
    reference_text = _bullets(group.reference_paths)
    generated_paths_text = _bullets(group.generated_paths)
    expectations_text = _bullets(group.research_output_expectations)
    lineage_text = _lineage_stanza(lineage_bootstrap)

    return (
        f"You are the phase-1 research agent for group '{group.id}'.\n"
        f"Run ID: {run_id}\n"
        f"Objective: {group.objective}\n"
        f"Policy mode: {group.policy_mode}\n"
        f"Current hypothesis id: {intent_packet.active_hypothesis_id}\n"
        f"Current hypothesis text: {intent_packet.hypothesis_text}\n"
        f"Previous failure class: {intent_packet.last_failure_class}\n"
        f"Next action: {intent_packet.next_action}\n\n"
        f"{lineage_text}"
        "Research north star:\n"
        "- Start from evidence and a written plan before making edits.\n"
        f"- Your workspace is `{workdir}/`; you own it fully and may add, modify, or restructure any file under it.\n"
        "- Make one bounded, hypothesis-driven change per cycle.\n"
        "- Treat metric regressions as learning, not execution failure.\n\n"
        "Before changes, read and follow:\n"
        f"{guidance_text}\n\n"
        "Read (do not edit or run) the read-only evaluation zone to understand exactly how you are scored:\n"
        f"{reference_text}\n\n"
        "Execution order (must follow in this order):\n"
        "1) Inspect the workspace and the read-only eval zone to ground your hypothesis in evidence.\n"
        "2) Write an experiment intent JSON before editing code:\n"
        f"   - path: {run_intent_json}\n"
        "   - required keys: run_id, group_id, objective, hypothesis_id, hypothesis,\n"
        "     evidence_refs (list[str]), planned_code_changes (list[str]),\n"
        f"     target_files (list[str], all under `{workdir}/`), success_criteria (list[str]), rollback_plan.\n"
        "3) Write an experiment plan markdown before editing code:\n"
        f"   - path: {run_plan_md}\n"
        "   - include headings: ## Evidence, ## Planned Edit, ## Risk and Rollback,\n"
        "     ## Eval Expectations.\n"
        f"4) Implement one real bounded code experiment in the `{workdir}/` workspace.\n"
        "5) Do not create branch-memory source files; experiment intent is recorded by the runtime.\n"
        "6) Return a short JSON summary with keys: hypothesis_id, theme, changed_files,\n"
        "   intent_json_path, plan_md_path.\n\n"
        "Generated paths (may be created while testing, never commit as source changes):\n"
        f"{generated_paths_text}\n\n"
        "Research output expectations:\n"
        f"{expectations_text}\n\n"
        "Constraints:\n"
        f"- Keep edits inside the `{workdir}/` workspace plus run-local observability files.\n"
        "- Do not edit or run the read-only evaluation zone; read it only to understand scoring.\n"
        "- Metric-producing training and full eval are owned by the orchestrator and GitHub eval nodes.\n"
        "- For your own feedback, write and run quick CPU-bounded unit/smoke tests; do not launch long training.\n"
        "- Do not delete previous research entries.\n"
        "- Keep edits minimal, reversible, and syntactically valid.\n"
        "- If you add or change dependencies, install the requirements file before validation.\n"
        "- If previous output did not improve the baseline, treat that as evidence and continue or pivot using the intent packet.\n"
        "- Only revert when the current branch state is a worse basis for future research.\n"
    )


def _lineage_stanza(bootstrap: BranchBootstrap | None) -> str:
    if not bootstrap or bootstrap.mode == "baseline":
        return ""
    parent = bootstrap.parent_group_id or "unknown"
    short_sha = bootstrap.start_ref[:7]
    policy = bootstrap.anchor_policy or "last_commit"
    return (
        f"Lineage: this group continues from '{parent}' at commit {short_sha} ({policy}). "
        "Build on that state; do not reset unrelated files unless the hypothesis requires it.\n\n"
    )


def _bullets(items: list[str]) -> str:
    if not items:
        return "- (none configured)"
    return "\n".join(f"- {item}" for item in items)
