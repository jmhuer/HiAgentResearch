from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from hiagentresearch.src.runtime.orchestrator import DEFAULT_STATE_DIR
from hiagentresearch.src.registry.store import Registry


def summary(*, registry: Registry, as_json: bool) -> int:
    rows = registry.group_summary()
    _emit(rows, as_json=as_json, text_formatter=_format_summary)
    return 0


def runs(*, registry: Registry, group_id: str | None, limit: int, as_json: bool) -> int:
    rows = registry.runs_for_group(group_id, limit=limit)
    _emit(rows, as_json=as_json, text_formatter=_format_runs)
    return 0


def show(*, registry: Registry, run_id: str, as_json: bool) -> int:
    run = next((row for row in registry.runs_for_group(None, limit=10_000) if row["run_id"] == run_id), None)
    payload = {
        "run": run,
        "metrics": registry.metrics_for_run(run_id),
        "outcome": registry.outcome_for_run(run_id),
        "experiment": registry.experiment_for_run(run_id),
        "artifacts": registry.artifacts_for_run(run_id),
    }
    _emit(payload, as_json=as_json, text_formatter=_format_show)
    return 0 if run else 1


def metrics(*, registry: Registry, group_id: str, metric_name: str | None, as_json: bool) -> int:
    rows = registry.metrics_for_group(group_id, metric_name)
    _emit(rows, as_json=as_json, text_formatter=_format_metrics)
    return 0


def artifacts(*, registry: Registry, run_id: str, as_json: bool) -> int:
    rows = registry.artifacts_for_run(run_id)
    _emit(rows, as_json=as_json, text_formatter=_format_artifacts)
    return 0


def export(*, registry: Registry, as_json: bool) -> int:
    payload = registry.dashboard_snapshot()
    _emit(payload, as_json=as_json, text_formatter=lambda value: json.dumps(value, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect HiAgentResearch registry state.")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    summary_parser = sub.add_parser("summary", help="Show latest run summary by research group.")
    _add_json_flag(summary_parser)

    runs_parser = sub.add_parser("runs", help="List runs.")
    runs_parser.add_argument("--group-id", default=None)
    runs_parser.add_argument("--limit", type=int, default=20)
    _add_json_flag(runs_parser)

    show_parser = sub.add_parser("show", help="Show one run with metrics, outcome, experiment, and artifacts.")
    show_parser.add_argument("--run-id", required=True)
    _add_json_flag(show_parser)

    metrics_parser = sub.add_parser("metrics", help="List metric series for a group.")
    metrics_parser.add_argument("--group-id", required=True)
    metrics_parser.add_argument("--metric", default=None)
    _add_json_flag(metrics_parser)

    artifacts_parser = sub.add_parser("artifacts", help="List artifacts for a run.")
    artifacts_parser.add_argument("--run-id", required=True)
    _add_json_flag(artifacts_parser)

    export_parser = sub.add_parser("export", help="Export compact dashboard-friendly registry snapshot.")
    _add_json_flag(export_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = Registry(args.state_dir.resolve())
    registry.init()
    if args.cmd == "summary":
        return summary(registry=registry, as_json=args.json)
    if args.cmd == "runs":
        return runs(registry=registry, group_id=args.group_id, limit=args.limit, as_json=args.json)
    if args.cmd == "show":
        return show(registry=registry, run_id=args.run_id, as_json=args.json)
    if args.cmd == "metrics":
        return metrics(registry=registry, group_id=args.group_id, metric_name=args.metric, as_json=args.json)
    if args.cmd == "artifacts":
        return artifacts(registry=registry, run_id=args.run_id, as_json=args.json)
    if args.cmd == "export":
        return export(registry=registry, as_json=True)
    return 1


def _emit(value: Any, *, as_json: bool, text_formatter) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
        return
    print(text_formatter(value))


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def _format_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No registry runs found."
    lines = []
    for row in rows:
        lines.append(
            " | ".join(
                [
                    str(row.get("group_id", "")),
                    str(row.get("run_id", "")),
                    str(row.get("failure_class", "")),
                    str(row.get("research_outcome", "unknown")),
                    f"accuracy={row.get('accuracy')}",
                    f"latency_ms={row.get('latency_ms')}",
                    str(row.get("branch", "")),
                ]
            )
        )
    return "\n".join(lines)


def _format_runs(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No runs found."
    return "\n".join(
        f"{row.get('created_at')} {row.get('run_id')} {row.get('group_id')} {row.get('status')} {row.get('failure_class')}"
        for row in rows
    )


def _format_show(payload: dict[str, Any]) -> str:
    if not payload.get("run"):
        return "Run not found."
    return json.dumps(payload, indent=2)


def _format_metrics(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No metrics found."
    return "\n".join(
        f"{row.get('created_at')} {row.get('run_id')} {row.get('metric_name')}={row.get('metric_value')}"
        for row in rows
    )


def _format_artifacts(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No artifacts found."
    return "\n".join(
        f"{row.get('artifact_type')} {row.get('artifact_path')} bytes={row.get('size_bytes')} sha256={row.get('sha256')}"
        for row in rows
    )


if __name__ == "__main__":
    raise SystemExit(main())
