from __future__ import annotations

import argparse
import json
from pathlib import Path

from hiagentresearch.src.core import config as config_cli
from hiagentresearch.src.registry import view as registry_view
from hiagentresearch.src.agents.credentials import ensure_cursor_api_key
from hiagentresearch.src.dashboard import cli as dashboard_cli
from hiagentresearch.src.paths import REPO_ROOT
from hiagentresearch.src.runtime.loop_controller import run_loops, run_loops_all
from hiagentresearch.src.runtime.orchestrator import init_state, resolve_group, run_group, status_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HiAgentResearch command line interface.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize local runtime registry.")

    status = sub.add_parser("status", help="Print registry-backed status.")
    status.add_argument("--group-id", default=None)

    run = sub.add_parser("run-group", help="Run one research group cycle.")
    run.add_argument("--group-id", required=True)
    run.add_argument("--workdir", type=Path, default=REPO_ROOT)
    run.add_argument("--quick", action="store_true")
    run.add_argument("--agent-model", default="composer-2.5")

    loops = sub.add_parser("loops", help="Run backend-owned research loops.")
    loops.add_argument("--group-id", default="model_architecture")
    loops.add_argument("--branch", default=None)
    loops.add_argument("--loops", type=int, default=3)
    loops.add_argument("--workdir", type=Path, default=REPO_ROOT)
    loops.add_argument("--quick", action="store_true")
    loops.add_argument("--agent-model", default="composer-2.5")
    loops.add_argument("--run-exact-loops", action="store_true", help="Do not stop early when quality is met.")

    loops_all = sub.add_parser("loops-all", help="Run all research groups in configured execution waves.")
    loops_all.add_argument("--loops", type=int, default=3)
    loops_all.add_argument("--workdir", type=Path, default=REPO_ROOT)
    loops_all.add_argument("--quick", action="store_true")
    loops_all.add_argument("--agent-model", default="composer-2.5")
    loops_all.add_argument("--run-exact-loops", action="store_true", help="Do not stop early when quality is met.")
    loops_all.add_argument(
        "--parallel",
        action="store_true",
        help="Run groups within each wave in parallel using git worktrees.",
    )

    resolve = sub.add_parser("resolve-group", help="Resolve group id for a branch.")
    resolve.add_argument("--branch", required=True)

    sub.add_parser(
        "render-workspace-docs",
        help="Regenerate the workspace AGENTS.md from config (command + targets).",
    )

    sub.add_parser("config", help="Delegate to config helper commands.")
    sub.add_parser("registry", help="Delegate to registry inspection commands.")
    sub.add_parser("dashboard", help="Delegate to dashboard build commands.")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(argv if argv is not None else [])
    if argv is None:
        import sys

        raw_args = sys.argv[1:]
    if raw_args[:1] == ["registry"]:
        return registry_view.main(raw_args[1:])
    if raw_args[:1] == ["config"]:
        return config_cli.main(raw_args[1:])
    if raw_args[:1] == ["dashboard"]:
        return dashboard_cli.main(raw_args[1:])

    ensure_cursor_api_key()
    parser = build_parser()
    args = parser.parse_args(raw_args)
    if args.cmd == "init":
        return init_state()
    if args.cmd == "status":
        return status_report(group_id=args.group_id)
    if args.cmd == "run-group":
        return run_group(
            group_id=args.group_id,
            workdir=args.workdir.resolve(),
            quick=args.quick,
            agent_model=args.agent_model,
        )
    if args.cmd == "loops":
        summary = run_loops(
            group_id=args.group_id,
            branch=args.branch,
            loops=args.loops,
            workdir=args.workdir.resolve(),
            quick=args.quick,
            agent_model=args.agent_model,
            stop_on_success=not args.run_exact_loops,
        )
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.ok else 1
    if args.cmd == "loops-all":
        return run_loops_all(
            loops=args.loops,
            workdir=args.workdir.resolve(),
            quick=args.quick,
            agent_model=args.agent_model,
            stop_on_success=not args.run_exact_loops,
            parallel=args.parallel,
        )
    if args.cmd == "resolve-group":
        return resolve_group(branch=args.branch)
    if args.cmd == "render-workspace-docs":
        from hiagentresearch.src.project.docs import write_workspace_agents

        path = write_workspace_agents()
        print(json.dumps({"ok": True, "workspace_agents": str(path)}, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
