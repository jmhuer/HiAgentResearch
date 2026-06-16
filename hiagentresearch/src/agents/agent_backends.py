from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from hiagentresearch.src.agents.credentials import ensure_cursor_api_key
from hiagentresearch.src.agents.cursor_client import cursor_sdk_client
from hiagentresearch.src.agents.prompts import build_research_cycle_prompt
from hiagentresearch.src.core.models import FailureClass, IntentPacket, ResearchGroup, ScoreContext, utc_now_iso
from hiagentresearch.src.lineage.resolve import BranchBootstrap


class AgentBackendError(RuntimeError):
    """Raised when agent backend execution fails."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: FailureClass = "invalid_cycle",
        record: AgentExecutionRecord | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.record = record


@dataclass(slots=True)
class AgentExecutionRecord:
    backend: str
    success: bool
    status: str
    failure_class: FailureClass
    summary: str
    raw_result: dict[str, Any]
    timestamp: str


def failure_class_for_cursor_run_status(run_status: str) -> FailureClass:
    """Map terminal Cursor run status to execution failure classes."""
    normalized = run_status.strip().lower()
    if normalized == "finished":
        return "none"
    if normalized == "cancelled":
        return "infra_failure"
    if normalized == "error":
        return "invalid_cycle"
    return "invalid_cycle"


def failure_class_for_cursor_agent_error() -> FailureClass:
    """Cursor SDK errors mean the run did not start (auth, rate limit, network, config)."""
    return "infra_failure"


# Markers for Cursor bridge startup failures that are transient even though the SDK
# reports them with is_retryable=False. The bridge mints a fresh tool-callback auth
# token on every launch (secrets.token_urlsafe), and the vendored bridge arg parser
# rejects any value starting with "-" as "Missing value" — so ~1.5% of launches die
# before discovery purely on an unlucky token. A relaunch regenerates the token and
# clears it, so we retry these regardless of the SDK's is_retryable flag.
_BRIDGE_STARTUP_RETRY_MARKERS = (
    "before discovery",
    "bridge discovery",
    "tool-callback",
    "tool callback server",
)


def _is_retryable_startup_error(exc: Any) -> bool:
    """A Cursor startup error is retryable if the SDK says so OR it is a transient
    pre-discovery bridge launch failure (see ``_BRIDGE_STARTUP_RETRY_MARKERS``)."""
    if bool(getattr(exc, "is_retryable", False)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _BRIDGE_STARTUP_RETRY_MARKERS)


def _startup_retry_attempts_from_env(default: int = 3) -> int:
    raw = os.environ.get("HIAGENTRESEARCH_CURSOR_STARTUP_RETRY", "").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return 1
    attempts_raw = os.environ.get("HIAGENTRESEARCH_CURSOR_STARTUP_ATTEMPTS", "").strip()
    if not attempts_raw:
        return default
    try:
        attempts = int(attempts_raw)
    except ValueError as exc:
        raise ValueError(
            "HIAGENTRESEARCH_CURSOR_STARTUP_ATTEMPTS must be an integer >= 1"
        ) from exc
    if attempts < 1:
        raise ValueError("HIAGENTRESEARCH_CURSOR_STARTUP_ATTEMPTS must be >= 1")
    return attempts


def _startup_retry_backoff_sec_from_env(default: float = 2.0) -> float:
    raw = os.environ.get("HIAGENTRESEARCH_CURSOR_STARTUP_RETRY_BACKOFF_SEC", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            "HIAGENTRESEARCH_CURSOR_STARTUP_RETRY_BACKOFF_SEC must be a non-negative number"
        ) from exc
    if value < 0:
        raise ValueError("HIAGENTRESEARCH_CURSOR_STARTUP_RETRY_BACKOFF_SEC must be >= 0")
    return value


def run_cursor_agent_cycle(
    *,
    workdir: Path,
    run_dir: Path,
    group: ResearchGroup,
    intent_packet: IntentPacket,
    run_id: str,
    model: str = "composer-2.5",
    thinking: str = "",
    startup_attempts: int = 3,
    startup_retry_backoff_sec: float = 2.0,
    unary_timeout_sec: float = 1800.0,
    stream_timeout_sec: float = 1800.0,
    lineage_bootstrap: BranchBootstrap | None = None,
    score_context: ScoreContext | None = None,
) -> AgentExecutionRecord:
    ensure_cursor_api_key()
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise AgentBackendError(
            "CURSOR_API_KEY is missing. Export CURSOR_API_KEY or add credentials/cursor_secret.txt.",
            failure_class="infra_failure",
        )
    try:
        from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions
    except ModuleNotFoundError as exc:
        raise AgentBackendError(
            "cursor-sdk is not installed. Install the project dependencies before running real agent loops.",
            failure_class="infra_failure",
        ) from exc

    # Reasoning effort is config-driven and applied to every group; build a typed
    # ModelSelection only when a thinking level is requested.
    if thinking:
        from cursor_sdk import ModelParameterValue, ModelSelection

        model_selection: Any = ModelSelection(
            id=model, params=[ModelParameterValue(id="thinking", value=thinking)]
        )
    else:
        model_selection = model

    prompt = build_research_cycle_prompt(
        group=group,
        intent_packet=intent_packet,
        run_id=run_id,
        checkout_root=workdir.resolve(),
        lineage_bootstrap=lineage_bootstrap,
        score_context=score_context,
    )
    (run_dir / "agent_prompt.txt").write_text(prompt, encoding="utf-8")
    stream_path = run_dir / "agent_stream.jsonl"
    messages_path = run_dir / "agent_messages.txt"
    stream_error = ""
    result: Any
    agent: Any = None
    sdk_run: Any = None
    # Config provides the defaults; matching env vars still override (ops escape hatch).
    resolved_attempts = _startup_retry_attempts_from_env(startup_attempts)
    retry_backoff_sec = _startup_retry_backoff_sec_from_env(startup_retry_backoff_sec)
    for attempt in range(1, resolved_attempts + 1):
        try:
            with cursor_sdk_client(
                str(workdir),
                unary_timeout_default=unary_timeout_sec,
                stream_timeout_default=stream_timeout_sec,
            ) as client, Agent.create(
                api_key=api_key,
                model=model_selection,
                local=LocalAgentOptions(cwd=str(workdir)),
                client=client,
            ) as agent:
                _append_stream_event(
                    stream_path,
                    {
                        "type": "agent_created",
                        "agent_id": getattr(agent, "agent_id", "") or getattr(agent, "id", ""),
                        "model": model,
                        "thinking": thinking,
                        "cwd": str(workdir),
                        "startup_attempt": attempt,
                        "startup_attempts_config": resolved_attempts,
                    },
                )
                sdk_run = agent.send(prompt)
                _append_stream_event(
                    stream_path,
                    {
                        "type": "run_started",
                        "agent_id": getattr(agent, "agent_id", "") or getattr(agent, "id", ""),
                        "sdk_run_id": getattr(sdk_run, "id", ""),
                    },
                )
                try:
                    for message in sdk_run.messages():
                        payload = _sdk_message_payload(message)
                        _append_stream_event(stream_path, payload)
                        text = _message_text(message)
                        if text:
                            with messages_path.open("a", encoding="utf-8") as handle:
                                handle.write(text)
                                if not text.endswith("\n"):
                                    handle.write("\n")
                except Exception as exc:  # pragma: no cover - defensive; wait() still gives terminal status.
                    stream_error = f"{type(exc).__name__}: {exc}"
                    _append_stream_event(stream_path, {"type": "stream_error", "error": stream_error})
                result = sdk_run.wait()
                break
        except CursorAgentError as exc:
            can_retry = attempt < resolved_attempts and _is_retryable_startup_error(exc)
            _append_stream_event(
                stream_path,
                {
                    "type": "startup_error",
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "is_retryable": bool(getattr(exc, "is_retryable", False)),
                    "will_retry": can_retry,
                },
            )
            if can_retry:
                if retry_backoff_sec > 0:
                    time.sleep(retry_backoff_sec)
                continue
            record = _record_from_cursor_error(exc)
            _write_record(run_dir=run_dir, record=record, prompt=prompt)
            raise AgentBackendError(
                f"Cursor agent failed to start ({type(exc).__name__}): {exc}",
                failure_class=failure_class_for_cursor_agent_error(),
                record=record,
            ) from exc

    status = str(result.status)
    failure_class = failure_class_for_cursor_run_status(status)
    success = failure_class == "none"
    record = AgentExecutionRecord(
        backend="cursor_sdk",
        success=success,
        status=status,
        failure_class=failure_class,
        summary=str(result.result)[:2000],
        raw_result=_run_result_payload(
            result,
            cursor_run_status=status,
            agent=agent,
            sdk_run=sdk_run,
            stream_error=stream_error,
        ),
        timestamp=utc_now_iso(),
    )
    _write_record(run_dir=run_dir, record=record, prompt=prompt)
    if not success:
        raise AgentBackendError(
            f"Cursor agent run did not finish successfully (status={status}).",
            failure_class=failure_class,
            record=record,
        )
    return record


def _record_from_cursor_error(exc: Any) -> AgentExecutionRecord:
    return AgentExecutionRecord(
        backend="cursor_sdk",
        success=False,
        status="startup_error",
        failure_class=failure_class_for_cursor_agent_error(),
        summary=str(exc)[:2000],
        raw_result=_cursor_error_payload(exc),
        timestamp=utc_now_iso(),
    )


def _cursor_error_payload(exc: Any) -> dict[str, Any]:
    return {
        "status": "startup_error",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "request_id": getattr(exc, "request_id", None),
        "is_retryable": bool(getattr(exc, "is_retryable", False)),
        "retry_after": getattr(exc, "retry_after", None),
        "code": getattr(exc, "code", None),
        "status_code": getattr(exc, "status_code", None),
    }


def _run_result_payload(
    result: Any,
    *,
    cursor_run_status: str,
    agent: Any | None = None,
    sdk_run: Any | None = None,
    stream_error: str = "",
) -> dict[str, Any]:
    return {
        "id": getattr(result, "id", ""),
        "agent_id": getattr(result, "agent_id", "") or _agent_id(agent),
        "sdk_run_id": getattr(sdk_run, "id", "") if sdk_run is not None else "",
        "status": cursor_run_status,
        "cursor_run_status": cursor_run_status,
        "result": str(getattr(result, "result", "")),
        "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
        "created_at": getattr(result, "created_at", None),
        "git": _serialize_git(getattr(result, "git", None)),
        "stream_error": stream_error,
        "request_id": None,
        "is_retryable": False,
        "retry_after": None,
    }


def _agent_id(agent: Any | None) -> str:
    if agent is None:
        return ""
    return str(getattr(agent, "agent_id", "") or getattr(agent, "id", ""))


def _append_stream_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


def _sdk_message_payload(message: Any) -> dict[str, Any]:
    return {
        "type": "sdk_message",
        "message_type": str(getattr(message, "type", "")),
        "text": _message_text(message),
        "raw": _jsonable(message),
    }


def _message_text(message: Any) -> str:
    texts: list[str] = []
    sdk_message = getattr(message, "message", None)
    if isinstance(message, dict):
        direct = message.get("text", "")
        if direct:
            texts.append(str(direct))
        sdk_message = message.get("message", sdk_message)
    content = sdk_message.get("content") if isinstance(sdk_message, dict) else getattr(sdk_message, "content", None)
    if isinstance(content, list):
        for block in content:
            if getattr(block, "type", "") == "text":
                texts.append(str(getattr(block, "text", "")))
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
    direct = getattr(message, "text", "")
    if direct:
        texts.append(str(direct))
    if texts:
        return "".join(texts)
    return _message_text_from_jsonable(_jsonable(message))


def _message_text_from_jsonable(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("text")
    if direct:
        return str(direct)
    message = payload.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    return "".join(texts)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump())
        except Exception:
            pass
    payload: dict[str, Any] = {}
    for key in ("type", "id", "status", "created_at", "message"):
        if hasattr(value, key):
            payload[key] = _jsonable(getattr(value, key))
    return payload or str(value)


def _serialize_git(git: Any) -> dict[str, Any] | None:
    if git is None:
        return None
    if is_dataclass(git):
        return asdict(git)
    if isinstance(git, dict):
        return git
    payload: dict[str, Any] = {}
    for key in ("branch", "commit", "repo", "url"):
        if hasattr(git, key):
            payload[key] = getattr(git, key)
    return payload or None


def _write_record(run_dir: Path, record: AgentExecutionRecord, prompt: str) -> None:
    payload = {
        "backend": record.backend,
        "success": record.success,
        "status": record.status,
        "failure_class": record.failure_class,
        "summary": record.summary,
        "raw_result": record.raw_result,
        "timestamp": record.timestamp,
        "prompt": prompt,
    }
    (run_dir / "agent_backend_record.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
