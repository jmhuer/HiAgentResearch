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
from hiagentresearch.src.runtime.promote import promote_research_baseline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HiAgentResearch command line interface.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize local runtime registry.")

    status = sub.add_parser("status", help="Print registry-backed status.")
    status.add_argument("--group-id", default=None)

    run = sub.add_parser("run-group", help="Run one research group cycle.")
    run.add_argument("--group-id", required=True)
    run.add_argument("--workdir", type=Path, default=REPO_ROOT)
    run.add_argument("--agent-model", default="", help="Override config.agent.model; empty uses config.")

    loops = sub.add_parser("loops", help="Run backend-owned research loops.")
    loops.add_argument("--group-id", default="model_architecture")
    loops.add_argument("--branch", default=None)
    loops.add_argument("--loops", type=int, default=3)
    loops.add_argument("--workdir", type=Path, default=REPO_ROOT)
    loops.add_argument("--agent-model", default="", help="Override config.agent.model; empty uses config.")
    loops.add_argument("--run-exact-loops", action="store_true", help="Do not stop early when quality is met.")

    loops_all = sub.add_parser("loops-all", help="Run all research groups in configured execution waves.")
    loops_all.add_argument("--loops", type=int, default=3)
    loops_all.add_argument("--workdir", type=Path, default=REPO_ROOT)
    loops_all.add_argument("--agent-model", default="", help="Override config.agent.model; empty uses config.")
    loops_all.add_argument("--run-exact-loops", action="store_true", help="Do not stop early when quality is met.")
    loops_all.add_argument(
        "--parallel",
        action="store_true",
        help="Run groups within each wave in parallel using git worktrees.",
    )

    promote = sub.add_parser("promote", help="Promote a research policy winner onto a baseline branch.")
    promote.add_argument("--group-id", default="", help="Override orchestration.promote_from_group.")
    promote.add_argument("--commit", default="", help="Override the resolved policy-selected commit.")
    promote.add_argument(
        "--target-branch",
        default="",
        help="Branch to receive the promoted product tree. Defaults to orchestration.baseline_ref.",
    )
    promote.add_argument("--dry-run", action="store_true", help="Resolve and print the planned promotion only.")
    promote.add_argument("--json", action="store_true", help="Print the result as JSON.")
    promote.add_argument("--push", action="store_true", help="Push the target branch after promotion.")

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

    parser = build_parser()
    args = parser.parse_args(raw_args)
    if args.cmd == "init":
        return init_state()
    if args.cmd == "status":
        return status_report(group_id=args.group_id)
    if args.cmd == "run-group":
        ensure_cursor_api_key()
        return run_group(
            group_id=args.group_id,
            workdir=args.workdir.resolve(),
            agent_model=args.agent_model,
        )
    if args.cmd == "loops":
        ensure_cursor_api_key()
        summary = run_loops(
            group_id=args.group_id,
            branch=args.branch,
            loops=args.loops,
            workdir=args.workdir.resolve(),
            agent_model=args.agent_model,
            stop_on_success=not args.run_exact_loops,
        )
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if summary.ok else 1
    if args.cmd == "loops-all":
        ensure_cursor_api_key()
        return run_loops_all(
            loops=args.loops,
            workdir=args.workdir.resolve(),
            agent_model=args.agent_model,
            stop_on_success=not args.run_exact_loops,
            parallel=args.parallel,
        )
    if args.cmd == "promote":
        result = promote_research_baseline(
            group_id=args.group_id,
            commit_sha=args.commit,
            target_branch=args.target_branch,
            dry_run=args.dry_run,
            push=args.push,
        )
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            mode = "Dry run" if result.dry_run else "Promoted"
            action = "create" if result.target_created else "update"
            print(f"{mode}: {action} {result.target_branch} from {result.anchor.commit_sha}")
            print(
                f"Source group: {result.anchor.promote_from_group} "
                f"({result.anchor.anchor_metric}={result.anchor.metric_value}, "
                f"policy={result.anchor.top_commit_policy})"
            )
            if result.diff_stat.strip():
                print(result.diff_stat.rstrip())
            if result.committed:
                print(f"Commit: {result.promoted_sha}")
            if result.pushed:
                print(f"Pushed: {result.target_branch}")
        return 0
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
