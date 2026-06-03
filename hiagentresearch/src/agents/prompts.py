from __future__ import annotations

from hiagentresearch.src.agents.task_contract import task_contract
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
    contract = task_contract(group.task_kind)
    policy_line = f"Policy mode: {group.policy_mode}"
    if group.policy_mode_description:
        policy_line += f" — {group.policy_mode_description}"

    return (
        f"You are the phase-1 research agent for group '{group.id}'.\n"
        f"Run ID: {run_id}\n"
        f"Objective: {group.objective}\n"
        f"{policy_line}\n"
        f"Current hypothesis id: {intent_packet.active_hypothesis_id}\n"
        f"Current hypothesis text: {intent_packet.hypothesis_text}\n"
        f"Previous failure class: {intent_packet.last_failure_class}\n"
        f"Next action: {intent_packet.next_action}\n\n"
        f"{lineage_text}"
        "Read and follow these guides before editing (most specific first):\n"
        f"{guidance_text}\n\n"
        f"Your workspace is `{workdir}/`; you own it fully. {contract.cycle_instruction}\n\n"
        "System expectations for this cycle:\n"
        f"{expectations_text}\n\n"
        "Write these planning artifacts before editing code:\n"
        f"- {run_intent_json}: run_id, group_id, objective, hypothesis_id, hypothesis,\n"
        "  evidence_refs, planned_code_changes,\n"
        f"  target_files (all under `{workdir}/`), success_criteria, rollback_plan.\n"
        f"- {run_plan_md}: headings ## Evidence, ## Planned Edit, ## Risk and Rollback, {contract.plan_heading}.\n"
        f"  In {contract.plan_heading}, {contract.plan_expectation}\n\n"
        "Read (never edit or run) the read-only evaluation zone to see exactly how you are scored:\n"
        f"{reference_text}\n\n"
        "Boundaries:\n"
        "- Do not edit or run the read-only evaluation zone; metric-producing training and full eval "
        "are owned by the orchestrator and GitHub eval nodes.\n"
        "- Do not create branch-memory source files; the runtime records your intent.\n"
        "- These generated paths may appear while testing but are never committed as source changes:\n"
        f"{generated_paths_text}\n\n"
        "When the intent JSON, the plan, and one bounded workspace edit are in place, stop and "
        "return a short summary of what you changed. Do not keep exploring after the edit is done.\n"
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
