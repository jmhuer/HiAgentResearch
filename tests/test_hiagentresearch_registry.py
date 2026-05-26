import json

from hiagentresearch.src.models import IntentPacket
from hiagentresearch.src.registry import Registry


def test_registry_init_and_intent_packet(tmp_path) -> None:
    registry = Registry(tmp_path)
    registry.init()
    packet = IntentPacket(
        group_id="model_architecture",
        active_hypothesis_id="h1",
        hypothesis_text="test",
        attempt_count=1,
        last_failure_class="none",
        next_action="continue",
        rollback_anchor_sha="",
        key_evidence_refs=["run_1"],
    )
    path = registry.write_intent_packet(packet)
    loaded = registry.read_intent_packet("model_architecture")
    assert path.exists()
    assert loaded is not None
    assert loaded.active_hypothesis_id == "h1"


def test_registry_record_run(tmp_path) -> None:
    registry = Registry(tmp_path)
    registry.init()
    registry.record_run(
        run_id="run_abc",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.99, "latency_ms": 12.1},
    )
    # events.jsonl should be writable for external callers
    registry.append_event({"event_type": "smoke", "ok": True})
    payloads = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert payloads[-1]["event_type"] == "smoke"
