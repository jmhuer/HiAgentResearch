from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _append_marker(target_file: Path, marker: str) -> None:
    if not target_file.exists():
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(
            '"""Phase-1 research marker file."""\n\nfrom __future__ import annotations\n\nRESEARCH_MARKERS: list[str] = []\n',
            encoding="utf-8",
        )

    content = target_file.read_text(encoding="utf-8")
    needle = "RESEARCH_MARKERS: list[str] = ["
    if needle in content:
        content = content.replace(needle, f'{needle}\n    "{marker}",', 1)
    elif "RESEARCH_MARKERS: list[str] = []" in content:
        content = content.replace(
            "RESEARCH_MARKERS: list[str] = []",
            f'RESEARCH_MARKERS: list[str] = [\n    "{marker}",\n]',
            1,
        )
    else:
        content = content.rstrip() + f'\n\nRESEARCH_MARKERS: list[str] = [\n    "{marker}",\n]\n'
    target_file.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock research-group agent for phase-1 loop testing.")
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--target-file", type=Path, default=Path("mnist/pipeline/research_markers.py"))
    args = parser.parse_args()

    run_id = os.environ.get("HIAGENTRESEARCH_RUN_ID", "run_unknown")
    state_dir = Path(os.environ.get("HIAGENTRESEARCH_STATE_DIR", ".hiagentresearch/state"))
    packet_path = state_dir / "intent_packets" / f"{args.group_id}.json"
    packet = {}
    if packet_path.exists():
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    attempt = int(packet.get("attempt_count", 0)) + 1
    marker = f"{args.group_id}:{run_id}:attempt{attempt}:{_utc_now()}"
    _append_marker(args.target_file, marker)

    activity_dir = state_dir / "agent_activity" / args.group_id
    activity_dir.mkdir(parents=True, exist_ok=True)
    activity = {
        "run_id": run_id,
        "group_id": args.group_id,
        "marker": marker,
        "target_file": str(args.target_file),
        "attempt_from_packet": attempt,
        "timestamp": _utc_now(),
    }
    (activity_dir / f"{run_id}.json").write_text(json.dumps(activity, indent=2), encoding="utf-8")
    print(json.dumps(activity, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
