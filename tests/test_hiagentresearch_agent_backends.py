from hiagentresearch.src.agents.agent_backends import (
    failure_class_for_cursor_agent_error,
    failure_class_for_cursor_run_status,
)


def test_failure_class_for_cursor_run_status() -> None:
    assert failure_class_for_cursor_run_status("finished") == "none"
    assert failure_class_for_cursor_run_status("error") == "invalid_cycle"
    assert failure_class_for_cursor_run_status("cancelled") == "infra_failure"
    assert failure_class_for_cursor_run_status("ERROR") == "invalid_cycle"


def test_failure_class_for_cursor_agent_error() -> None:
    assert failure_class_for_cursor_agent_error() == "infra_failure"
