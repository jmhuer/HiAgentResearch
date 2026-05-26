from __future__ import annotations

from hiagentresearch.src.models import IntentPacket, ResearchGroup


def build_phase1_prompt(*, group: ResearchGroup, intent_packet: IntentPacket, run_id: str) -> str:
    run_plan_md = f".hiagentresearch/runs/{run_id}/experiment_plan.md"
    run_intent_json = f".hiagentresearch/runs/{run_id}/experiment_intent.json"
    core_paths = _core_allowed_paths(group)

    guidance_text = _bullets(["hiagentresearch/AGENTS.md", "hiagentresearch/skills/phase1-experiment-cycle/SKILL.md"])
    context_text = _bullets(group.context_paths)
    core_paths_text = _bullets(core_paths)
    generated_paths_text = _bullets(group.generated_paths)
    supporting_text = _supporting_artifacts_text(group)
    supporting_step = (
        "5) Update configured supporting artifacts when applicable, following their instructions.\n"
        if group.supporting_artifacts
        else "5) Do not create branch-memory source files; experiment intent is recorded by the runtime.\n"
    )
    expectations_text = _bullets(group.research_output_expectations)

    return (
        f"You are the phase-1 research agent for group '{group.id}'.\n"
        f"Run ID: {run_id}\n"
        f"Objective: {group.objective}\n"
        f"Policy mode: {group.policy_mode}\n"
        f"Current hypothesis id: {intent_packet.active_hypothesis_id}\n"
        f"Current hypothesis text: {intent_packet.hypothesis_text}\n"
        f"Previous failure class: {intent_packet.last_failure_class}\n"
        f"Next action: {intent_packet.next_action}\n\n"
        "Design north star:\n"
        "- Keep the runtime simple; power comes from trusted modular services.\n"
        "- Use configured contracts instead of project-specific assumptions.\n"
        "- Fix issues canonically by improving contracts, eval adapters, registry invariants, or docs.\n\n"
        "Before changes, read and follow:\n"
        f"{guidance_text}\n\n"
        "Inspect configured context before editing:\n"
        f"{context_text}\n\n"
        "Execution order (must follow in this order):\n"
        "1) Inspect the configured context and previous evidence.\n"
        "2) Write an experiment intent JSON before editing code:\n"
        f"   - path: {run_intent_json}\n"
        "   - required keys: run_id, group_id, objective, hypothesis_id, hypothesis,\n"
        "     evidence_refs (list[str]), planned_code_changes (list[str]),\n"
        "     target_files (list[str]), success_criteria (list[str]), rollback_plan.\n"
        "3) Write an experiment plan markdown before editing code:\n"
        f"   - path: {run_plan_md}\n"
        "   - include headings: ## Evidence, ## Planned Edit, ## Risk and Rollback,\n"
        "     ## Eval Expectations.\n"
        "4) Implement one real bounded code experiment in at least one configured core file.\n"
        f"{supporting_step}"
        "6) Return a short JSON summary with keys: hypothesis_id, theme, changed_files,\n"
        "   intent_json_path, plan_md_path.\n\n"
        "Configured core experiment files:\n"
        f"{core_paths_text}\n\n"
        "Configured supporting artifacts:\n"
        f"{supporting_text}\n\n"
        "Configured generated paths (may be created while testing, never commit as source changes):\n"
        f"{generated_paths_text}\n\n"
        "Research output expectations:\n"
        f"{expectations_text}\n\n"
        "Constraints:\n"
        "- Keep edits inside configured allowed paths plus run-local observability files.\n"
        "- Do not edit frozen eval entrypoints or runtime config unless explicitly configured.\n"
        "- Do not delete previous research entries.\n"
        "- Keep edits minimal, reversible, and syntactically valid.\n"
        "- If you add or change project dependencies, install the configured requirements file before validation.\n"
        "- If previous output did not improve baseline, treat that as evidence and continue or pivot using the intent packet.\n"
        "- Only revert when the current branch state is a worse basis for future research.\n"
    )


def _core_allowed_paths(group: ResearchGroup) -> list[str]:
    supporting = set(group.supporting_artifacts)
    return [path for path in group.allowed_paths if path not in supporting]


def _bullets(items: list[str]) -> str:
    if not items:
        return "- (none configured)"
    return "\n".join(f"- {item}" for item in items)


def _supporting_artifacts_text(group: ResearchGroup) -> str:
    if not group.supporting_artifacts:
        return "- (none configured)"
    lines = []
    for path in group.supporting_artifacts:
        instruction = group.supporting_artifact_instructions.get(path, "")
        suffix = f": {instruction}" if instruction else ""
        lines.append(f"- {path}{suffix}")
    return "\n".join(lines)
