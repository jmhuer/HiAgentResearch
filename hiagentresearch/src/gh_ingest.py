from __future__ import annotations

import argparse
import os
import json
import sys
from pathlib import Path

from hiagentresearch.src.config import load_config
from hiagentresearch.src.registry import Registry


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DIR = REPO_ROOT / ".hiagentresearch" / "state"
STATE_DIR = Path(os.environ.get("HIAGENTRESEARCH_STATE_DIR", str(DEFAULT_STATE_DIR))).resolve()


def ingest(run_id: str, group_id: str, branch: str, artifact_dir: Path) -> int:
    registry = Registry(STATE_DIR)
    registry.init()
    config = load_config()
    metrics_path = artifact_dir / "metrics.json"
    failure_path = artifact_dir / "failure_class.json"
    outcome_path = artifact_dir / "research_outcome.json"
    meta_path = artifact_dir / "run_meta.json"

    validation_error = _validate_artifact_contract(artifact_dir, config.artifact_contract.required)
    if validation_error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": validation_error,
                    "required": config.artifact_contract.required,
                },
                indent=2,
            )
        )
        return 1

    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"malformed artifact json: {exc}"}, indent=2))
        return 1

    failure_class = failure.get("failure_class", "infra_failure")
    status = "finished" if failure_class == "none" else "error"
    correlation_id = str(meta.get("correlation_id") or meta.get("run_id") or run_id)
    registry.record_run(
        run_id=run_id,
        group_id=group_id,
        branch=branch,
        status=status,
        failure_class=failure_class,
        metrics={k: float(v) for k, v in metrics.items()},
        commit_sha=str(meta.get("commit_sha", "")),
        workflow_run_id=str(meta.get("workflow_run_id", "")),
        correlation_id=correlation_id,
    )
    registry.record_research_outcome(run_id=run_id, outcome=outcome)
    registry.record_artifacts(
        run_id=run_id,
        artifact_paths=[
            artifact_dir / name for name in config.artifact_contract.required + config.artifact_contract.optional
        ],
        artifact_type="github_eval",
        base_dir=artifact_dir,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "run_id": run_id,
                "failure_class": failure_class,
                "research_outcome": outcome.get("research_outcome", "unknown"),
                "improved_baseline": bool(outcome.get("improved_baseline", False)),
            },
            indent=2,
        )
    )
    return 0


def _validate_artifact_contract(artifact_dir: Path, required: list[str]) -> str:
    missing = [name for name in required if not (artifact_dir / name).exists()]
    if missing:
        return f"missing required artifacts: {missing}"
    malformed: list[str] = []
    for name in ("metrics.json", "failure_class.json", "research_outcome.json", "run_meta.json"):
        path = artifact_dir / name
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                malformed.append(name)
                continue
            if not isinstance(payload, dict):
                malformed.append(name)
    if malformed:
        return f"malformed required artifacts: {malformed}"
    return ""


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
