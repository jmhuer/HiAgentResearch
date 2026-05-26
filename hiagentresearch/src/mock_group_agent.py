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


def _prepend_hypothesis(target_file: Path, entry: dict) -> None:
    if not target_file.exists():
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(
            '"""Phase-1 research hypothesis log."""\n\nfrom __future__ import annotations\n\nRESEARCH_HYPOTHESES: list[dict] = []\n',
            encoding="utf-8",
        )
    content = target_file.read_text(encoding="utf-8")
    rendered = json.dumps(entry, indent=8)
    needle = "RESEARCH_HYPOTHESES: list[dict] = ["
    if needle in content:
        content = content.replace(needle, f"{needle}\n    {rendered},", 1)
    else:
        content = content.rstrip() + f"\n\nRESEARCH_HYPOTHESES: list[dict] = [\n    {rendered},\n]\n"
    target_file.write_text(content, encoding="utf-8")


def _append_core_smoke_change(core_file: Path, run_id: str) -> None:
    marker = f"# hiagentresearch loop-controller smoke: {run_id}"
    content = core_file.read_text(encoding="utf-8")
    if marker not in content:
        core_file.write_text(content.rstrip() + f"\n{marker}\n", encoding="utf-8")


def _write_planning_artifacts(run_id: str, group_id: str, core_file: Path) -> None:
    run_dir = Path(".hiagentresearch") / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    intent = {
        "run_id": run_id,
        "group_id": group_id,
        "objective": "Mock command-backend smoke cycle for loop-controller validation.",
        "hypothesis_id": f"{group_id}-mock-{run_id}",
        "hypothesis": (
            "A backend-owned loop can validate planning artifacts, commit a bounded core-file "
            "change, push to GitHub Actions, and ingest canonical artifacts without shell glue."
        ),
        "evidence_refs": [str(core_file)],
        "planned_code_changes": ["Append one no-op smoke marker comment to the configured core file."],
        "target_files": [str(core_file)],
        "success_criteria": ["Phase-1 eval artifacts are produced and pass configured quality metrics."],
        "rollback_plan": "Revert the smoke marker commit.",
    }
    (run_dir / "experiment_intent.json").write_text(json.dumps(intent, indent=2), encoding="utf-8")
    (run_dir / "experiment_plan.md").write_text(
        "\n".join(
            [
                "# Mock Loop Controller Smoke Plan",
                "",
                "## Evidence",
                f"The command backend is validating the configured core file `{core_file}` and the loop controller path.",
                "",
                "## Planned Edit",
                "Append one no-op marker comment so the backend can enforce a real configured core-file change.",
                "",
                "## Risk and Rollback",
                "The edit is intentionally inert and can be reverted by removing the smoke marker comment or reverting the commit.",
                "",
                "## Eval Expectations",
                "The frozen phase-1 eval should emit metrics, failure classification, stdout, stderr, and run metadata.",
                "",
                "This plan is intentionally verbose enough to satisfy the pre-code planning contract while keeping the smoke cycle simple.",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock research-group agent for phase-1 loop testing.")
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--core-file", type=Path, default=Path("mnist/pipeline/model.py"))
    parser.add_argument("--marker-file", type=Path, default=Path("mnist/pipeline/research_markers.py"))
    parser.add_argument("--hypothesis-file", type=Path, default=Path("mnist/pipeline/research_hypotheses.py"))
    args = parser.parse_args()

    run_id = os.environ.get("HIAGENTRESEARCH_RUN_ID", "run_unknown")
    state_dir = Path(os.environ.get("HIAGENTRESEARCH_STATE_DIR", ".hiagentresearch/state"))
    packet_path = state_dir / "intent_packets" / f"{args.group_id}.json"
    packet = {}
    if packet_path.exists():
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    attempt = int(packet.get("attempt_count", 0)) + 1
    marker = f"{args.group_id}:{run_id}:attempt{attempt}:{_utc_now()}"
    _write_planning_artifacts(run_id, args.group_id, args.core_file)
    _append_core_smoke_change(args.core_file, run_id)
    _append_marker(args.marker_file, marker)
    _prepend_hypothesis(
        args.hypothesis_file,
        {
            "hypothesis_id": f"{args.group_id}-mock-{run_id}",
            "theme": "loop_controller_smoke",
            "hypothesis": "Backend-owned loop orchestration can replace shell glue cleanly.",
            "planned_change": f"Append one no-op smoke marker to {args.core_file}.",
            "run_id": run_id,
            "timestamp": _utc_now(),
        },
    )

    activity_dir = state_dir / "agent_activity" / args.group_id
    activity_dir.mkdir(parents=True, exist_ok=True)
    activity = {
        "run_id": run_id,
        "group_id": args.group_id,
        "marker": marker,
        "target_file": str(args.core_file),
        "attempt_from_packet": attempt,
        "timestamp": _utc_now(),
    }
    (activity_dir / f"{run_id}.json").write_text(json.dumps(activity, indent=2), encoding="utf-8")
    print(json.dumps(activity, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
