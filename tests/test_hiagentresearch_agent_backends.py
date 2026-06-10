import contextlib
import json
import sys
import types
from pathlib import Path

from hiagentresearch.src.agents.agent_backends import (
    _startup_retry_attempts_from_env,
    failure_class_for_cursor_agent_error,
    failure_class_for_cursor_run_status,
    run_cursor_agent_cycle,
)
from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.core.models import IntentPacket


def test_failure_class_for_cursor_run_status() -> None:
    assert failure_class_for_cursor_run_status("finished") == "none"
    assert failure_class_for_cursor_run_status("error") == "invalid_cycle"
    assert failure_class_for_cursor_run_status("cancelled") == "infra_failure"
    assert failure_class_for_cursor_run_status("ERROR") == "invalid_cycle"


def test_failure_class_for_cursor_agent_error() -> None:
    assert failure_class_for_cursor_agent_error() == "infra_failure"


def test_run_cursor_agent_cycle_streams_messages_and_preserves_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test")
    fake_module = types.ModuleType("cursor_sdk")

    class FakeCursorAgentError(Exception):
        pass

    class FakeLocalAgentOptions:
        def __init__(self, *, cwd: str) -> None:
            self.cwd = cwd

    class FakeDumpMessage:
        def model_dump(self):
            return {"type": "assistant", "message": {"content": [{"type": "text", "text": "dump text"}]}}

    class FakeRun:
        id = "sdk_run_123"

        def messages(self):
            block = types.SimpleNamespace(type="text", text="streamed assistant text")
            message = types.SimpleNamespace(content=[block])
            yield types.SimpleNamespace(type="assistant", message=message)
            yield {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "dict assistant text"}]},
            }
            yield FakeDumpMessage()

        def wait(self):
            return types.SimpleNamespace(
                id="result_123",
                agent_id="agent_123",
                status="finished",
                result="done",
                duration_ms=12,
                created_at="now",
                git=None,
            )

    class FakeAgent:
        agent_id = "agent_123"

        @classmethod
        def create(cls, **kwargs):
            cls.kwargs = kwargs
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def send(self, prompt):
            self.prompt = prompt
            return FakeRun()

    fake_module.Agent = FakeAgent
    fake_module.CursorAgentError = FakeCursorAgentError
    fake_module.LocalAgentOptions = FakeLocalAgentOptions
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake_module)

    @contextlib.contextmanager
    def fake_client(workspace, **kwargs):
        yield object()

    monkeypatch.setattr(
        "hiagentresearch.src.agents.agent_backends.cursor_sdk_client",
        fake_client,
    )

    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id,
        active_goal_id="h1",
        goal_text="test goal",
        attempt_count=0,
        last_failure_class="none",
        next_action="continue",
    )
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()

    record = run_cursor_agent_cycle(
        workdir=tmp_path,
        run_dir=run_dir,
        group=group,
        intent_packet=packet,
        run_id="run_test",
        model="composer-2.5",
    )

    assert record.success is True
    assert FakeAgent.kwargs["model"] == "composer-2.5"
    assert "client" in FakeAgent.kwargs
    assert ".hiagentresearch/AGENTS.md" in (run_dir / "agent_prompt.txt").read_text(encoding="utf-8")
    assert "streamed assistant text" in (run_dir / "agent_messages.txt").read_text(encoding="utf-8")
    assert "dict assistant text" in (run_dir / "agent_messages.txt").read_text(encoding="utf-8")
    assert "dump text" in (run_dir / "agent_messages.txt").read_text(encoding="utf-8")
    stream_events = [
        json.loads(line) for line in (run_dir / "agent_stream.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert stream_events[0]["type"] == "agent_created"
    assert stream_events[0]["startup_attempts_config"] == 2
    assert stream_events[1]["sdk_run_id"] == "sdk_run_123"
    backend_record = json.loads((run_dir / "agent_backend_record.json").read_text(encoding="utf-8"))
    assert backend_record["raw_result"]["agent_id"] == "agent_123"
    assert backend_record["raw_result"]["sdk_run_id"] == "sdk_run_123"


def test_retry_off_switch_forces_single_attempt(monkeypatch) -> None:
    monkeypatch.setenv("HIAGENTRESEARCH_CURSOR_STARTUP_RETRY", "0")
    monkeypatch.setenv("HIAGENTRESEARCH_CURSOR_STARTUP_ATTEMPTS", "5")
    assert _startup_retry_attempts_from_env() == 1


def test_thinking_builds_model_selection(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test")
    fake_module = types.ModuleType("cursor_sdk")

    class FakeCursorAgentError(Exception):
        pass

    class FakeLocalAgentOptions:
        def __init__(self, *, cwd: str) -> None:
            self.cwd = cwd

    class FakeModelParameterValue:
        def __init__(self, *, id: str, value: str) -> None:
            self.id, self.value = id, value

    class FakeModelSelection:
        def __init__(self, *, id: str, params=()) -> None:
            self.id, self.params = id, list(params)

    class FakeRun:
        id = "run_x"

        def messages(self):
            return iter(())

        def wait(self):
            return types.SimpleNamespace(id="r", agent_id="a", status="finished", result="ok", duration_ms=1, created_at="now", git=None)

    class FakeAgent:
        agent_id = "a"

        @classmethod
        def create(cls, **kwargs):
            cls.kwargs = kwargs
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def send(self, prompt):
            return FakeRun()

    fake_module.Agent = FakeAgent
    fake_module.CursorAgentError = FakeCursorAgentError
    fake_module.LocalAgentOptions = FakeLocalAgentOptions
    fake_module.ModelSelection = FakeModelSelection
    fake_module.ModelParameterValue = FakeModelParameterValue
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake_module)

    @contextlib.contextmanager
    def fake_client(workspace, **kwargs):
        yield object()

    monkeypatch.setattr("hiagentresearch.src.agents.agent_backends.cursor_sdk_client", fake_client)

    config = load_config(Path("configs/standard.yaml"))
    group = config.research_groups_by_id()["model_architecture"]
    packet = IntentPacket(
        group_id=group.id, active_goal_id="h1", goal_text="t",
        attempt_count=0, last_failure_class="none", next_action="continue",
    )
    run_dir = tmp_path / "run_t"
    run_dir.mkdir()
    run_cursor_agent_cycle(
        workdir=tmp_path, run_dir=run_dir, group=group, intent_packet=packet,
        run_id="run_t", model="composer-2.5", thinking="high",
    )
    selection = FakeAgent.kwargs["model"]
    assert isinstance(selection, FakeModelSelection)
    assert selection.id == "composer-2.5"
    assert [(p.id, p.value) for p in selection.params] == [("thinking", "high")]
