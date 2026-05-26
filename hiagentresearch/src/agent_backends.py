from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

from hiagentresearch.src.models import IntentPacket, ResearchGroup, utc_now_iso


class AgentBackendError(RuntimeError):
    """Raised when agent backend execution fails."""


@dataclass(slots=True)
class AgentExecutionRecord:
    backend: str
    success: bool
    status: str
    summary: str
    raw_result: dict
    timestamp: str


def run_cursor_agent_cycle(
    *,
    workdir: Path,
    run_dir: Path,
    group: ResearchGroup,
    intent_packet: IntentPacket,
    run_id: str,
    model: str = "composer-2.5",
) -> AgentExecutionRecord:
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise AgentBackendError(
            "CURSOR_API_KEY is missing. Export CURSOR_API_KEY before running real cursor-agent loops."
        )

    prompt = _build_prompt(group=group, intent_packet=intent_packet, run_id=run_id)
    result = Agent.prompt(
        prompt,
        AgentOptions(
            api_key=api_key,
            model=model,
            local=LocalAgentOptions(cwd=str(workdir)),
        ),
    )
    status = str(result.status)
    success = status == "finished"
    record = AgentExecutionRecord(
        backend="cursor_sdk",
        success=success,
        status=status,
        summary=str(result.result)[:2000],
        raw_result={
            "id": getattr(result, "id", ""),
            "agent_id": getattr(result, "agent_id", ""),
            "status": status,
            "result": str(getattr(result, "result", "")),
            "duration_ms": int(getattr(result, "duration_ms", 0)),
            "created_at": getattr(result, "created_at", None),
        },
        timestamp=utc_now_iso(),
    )
    _write_record(run_dir=run_dir, record=record, prompt=prompt)
    if not success:
        raise AgentBackendError(f"Cursor agent run did not finish successfully (status={status}).")
    return record


def _write_record(run_dir: Path, record: AgentExecutionRecord, prompt: str) -> None:
    payload = {
        "backend": record.backend,
        "success": record.success,
        "status": record.status,
        "summary": record.summary,
        "raw_result": record.raw_result,
        "timestamp": record.timestamp,
        "prompt": prompt,
    }
    (run_dir / "agent_backend_record.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_prompt(*, group: ResearchGroup, intent_packet: IntentPacket, run_id: str) -> str:
    run_plan_md = f".hiagentresearch/runs/{run_id}/experiment_plan.md"
    run_intent_json = f".hiagentresearch/runs/{run_id}/experiment_intent.json"
    core_paths = [
        path
        for path in group.allowed_paths
        if not path.endswith("research_markers.py") and not path.endswith("research_hypotheses.py")
    ]
    core_paths_text = "\n".join(f"- {path}" for path in core_paths) if core_paths else "- (none configured)"
    return (
        f"You are the phase-1 research agent for group '{group.id}'.\n"
        f"Run ID: {run_id}\n"
        f"Objective: {group.objective}\n"
        f"Policy mode: {group.policy_mode}\n"
        f"Current hypothesis id: {intent_packet.active_hypothesis_id}\n"
        f"Current hypothesis text: {intent_packet.hypothesis_text}\n\n"
        "Before changes, read and follow:\n"
        "- hiagentresearch/AGENTS.md\n"
        "- hiagentresearch/skills/phase1-experiment-cycle/SKILL.md\n\n"
        "Execution order (must follow in this order):\n"
        "1) Read current code + evidence first: mnist/pipeline/model.py, mnist/pipeline/train.py,\n"
        "   mnist/baseline.json, and mnist/pipeline/research_hypotheses.py.\n"
        "2) Write an experiment intent JSON before editing code:\n"
        f"   - path: {run_intent_json}\n"
        "   - required keys: run_id, group_id, objective, hypothesis_id, hypothesis,\n"
        "     evidence_refs (list[str]), planned_code_changes (list[str]),\n"
        "     target_files (list[str]), success_criteria (list[str]), rollback_plan.\n"
        "3) Write an experiment plan markdown before editing code:\n"
        f"   - path: {run_plan_md}\n"
        "   - include headings: ## Evidence, ## Planned Edit, ## Risk and Rollback,\n"
        "     ## Eval Expectations.\n"
        "4) Implement a real code experiment in at least one core MNIST file (not marker-only).\n"
        "5) Update mnist/pipeline/research_hypotheses.py by prepending exactly one new hypothesis entry\n"
        "   with keys: hypothesis_id, theme, hypothesis, planned_change, run_id, timestamp.\n"
        "6) Update mnist/pipeline/research_markers.py by prepending exactly one marker string.\n\n"
        "Allowed core experiment files:\n"
        f"{core_paths_text}\n\n"
        "Constraints:\n"
        "- Keep edits inside group allowed paths plus run-local observability files in .hiagentresearch/runs.\n"
        "- Do not edit files outside this contract.\n"
        "- Do not delete previous entries.\n"
        "- Write evidence-backed, concrete hypothesis text and a measurable expected effect.\n"
        "- Keep edits minimal and syntactically valid Python.\n"
        "- At the end, output a short JSON summary with keys: hypothesis_id, theme, changed_files,\n"
        "  intent_json_path, plan_md_path.\n"
    )

