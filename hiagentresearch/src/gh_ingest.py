from __future__ import annotations

import argparse
import os
import json
import sys
from pathlib import Path

from hiagentresearch.src.registry import Registry


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DIR = REPO_ROOT / ".hiagentresearch" / "state"
STATE_DIR = Path(os.environ.get("HIAGENTRESEARCH_STATE_DIR", str(DEFAULT_STATE_DIR))).resolve()


def ingest(run_id: str, group_id: str, branch: str, artifact_dir: Path) -> int:
    registry = Registry(STATE_DIR)
    registry.init()
    metrics_path = artifact_dir / "metrics.json"
    failure_path = artifact_dir / "failure_class.json"
    meta_path = artifact_dir / "run_meta.json"

    if not metrics_path.exists() or not failure_path.exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "missing required artifacts",
                    "required": ["metrics.json", "failure_class.json"],
                },
                indent=2,
            )
        )
        return 1

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    failure_class = failure.get("failure_class", "infra_failure")
    status = "finished" if failure_class == "none" else "error"
    registry.record_run(
        run_id=run_id,
        group_id=group_id,
        branch=branch,
        status=status,
        failure_class=failure_class,
        metrics={k: float(v) for k, v in metrics.items()},
        commit_sha=str(meta.get("commit_sha", "")),
        workflow_run_id=str(meta.get("workflow_run_id", "")),
    )
    registry.append_event(
        {
            "event_type": "github_ingest",
            "run_id": run_id,
            "group_id": group_id,
            "branch": branch,
            "failure_class": failure_class,
            "artifact_dir": str(artifact_dir),
        }
    )
    print(json.dumps({"ok": True, "run_id": run_id, "failure_class": failure_class}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest GitHub workflow artifacts into hiagentresearch registry.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return ingest(
        run_id=args.run_id,
        group_id=args.group_id,
        branch=args.branch,
        artifact_dir=args.artifact_dir.resolve(),
    )


if __name__ == "__main__":
    sys.exit(main())
