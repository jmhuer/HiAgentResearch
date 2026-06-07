from __future__ import annotations

import argparse
import json
from pathlib import Path

from hiagentresearch.src.core.config import DEFAULT_CONFIG_PATH, load_config
from hiagentresearch.src.dashboard.build import build_from_artifacts, build_from_registry
from hiagentresearch.src.paths import DEFAULT_STATE_DIR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build optional HiAgentResearch static dashboard assets.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build", help="Build a dashboard bundle from a local registry DB.")
    build.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    build.add_argument("--output-dir", type=Path, default=None)
    build.add_argument("--require-sqlite-assets", action="store_true")
    build.add_argument(
        "--prefer-json",
        action="store_true",
        help="Render from the JSON snapshot (shows lineage stars) and skip the GitHub "
        "baseline dispatch — use for offline local review.",
    )

    artifacts = sub.add_parser("build-from-artifacts", help="Build a dashboard bundle from downloaded GitHub artifacts.")
    artifacts.add_argument("--artifact-root", type=Path, required=True)
    artifacts.add_argument("--output-dir", type=Path, default=None)
    artifacts.add_argument("--require-sqlite-assets", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.cmd == "build":
        result = build_from_registry(
            state_dir=args.state_dir,
            output_dir=args.output_dir,
            config=config,
            require_sqlite_assets=args.require_sqlite_assets,
            # github_artifacts source => frontend prefers the JSON snapshot and the
            # build skips the baseline dispatch; ideal for offline local review.
            source_label="github_artifacts:local" if args.prefer_json else "local_registry",
        )
    elif args.cmd == "build-from-artifacts":
        result = build_from_artifacts(
            artifact_root=args.artifact_root,
            output_dir=args.output_dir,
            config=config,
            require_sqlite_assets=args.require_sqlite_assets,
        )
    else:
        return 1
    print(json.dumps({"ok": True, **result.to_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
