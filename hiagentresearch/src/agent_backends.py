from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from hiagentresearch.src.models import IntentPacket, ResearchGroup, utc_now_iso
from hiagentresearch.src.prompts import build_phase1_prompt


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
    try:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ModuleNotFoundError as exc:
        raise AgentBackendError(
            "cursor-sdk is not installed. Install the project dependencies before running real agent loops."
        ) from exc

    prompt = build_phase1_prompt(group=group, intent_packet=intent_packet, run_id=run_id)
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


