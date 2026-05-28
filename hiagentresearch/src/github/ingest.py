from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.core.outcomes import baseline_metrics_complete
from hiagentresearch.src.registry.store import Registry


from hiagentresearch.src.paths import resolve_state_dir

STATE_DIR = resolve_state_dir()


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
    if str(meta.get("node_kind", "")) == "baseline":
        record_baseline_snapshot_from_metrics(
            registry,
            ref=str(meta.get("baseline_ref") or meta.get("branch") or "main"),
            metrics=metrics,
        )
    manifest_path = artifact_dir / "experiment_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"malformed experiment manifest: {exc}"}, indent=2))
            return 1
        registry.record_experiment_manifest(
            run_id=run_id,
            manifest_path="experiment_manifest.json",
            manifest=manifest,
        )
        record_baseline_snapshot_from_manifest(registry, manifest)
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
            },
            indent=2,
        )
    )
    return 0


def record_baseline_snapshot_from_manifest(registry: Registry, manifest: dict) -> None:
    snapshot = manifest.get("lineage_baseline_snapshot")
    if not isinstance(snapshot, dict):
        return
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        return
    normalized: dict[str, float] = {}
    for name, value in metrics.items():
        try:
            normalized[str(name)] = float(value)
        except (TypeError, ValueError):
            return
    if not baseline_metrics_complete(normalized):
        return
    existing = registry.baseline_snapshot()
    if existing and baseline_metrics_complete((existing.get("metrics") or {})):
        return
    record_baseline_snapshot_from_metrics(registry, ref=str(snapshot.get("ref") or "main"), metrics=normalized)


def record_baseline_snapshot_from_metrics(registry: Registry, *, ref: str, metrics: dict) -> None:
    normalized: dict[str, float] = {}
    for name, value in metrics.items():
        try:
            normalized[str(name)] = float(value)
        except (TypeError, ValueError):
            return
    if not baseline_metrics_complete(normalized):
        return
    existing = registry.baseline_snapshot()
    if existing and baseline_metrics_complete((existing.get("metrics") or {})):
        return
    registry.record_baseline_snapshot(ref=ref, metrics=normalized)


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
