"""Framework artifact contracts for eval nodes and research run cycles.

Projects do not configure artifact filenames in config.yaml. Any repo using
HiAgentResearch must adhere to these contracts: the frozen eval adapter emits
canonical JSON (see evaluation.targets for metric keys), and agents write
planning artifacts under .hiagentresearch/runs/<run_id>/.
"""

from __future__ import annotations

import json
from pathlib import Path

FRAMEWORK_ARTIFACT_CONTRACT_VERSION = 1

INGEST_REQUIRED = (
    "metrics.json",
    "failure_class.json",
    "research_outcome.json",
    "run_meta.json",
)

EVAL_NODE_INDEX = INGEST_REQUIRED + (
    "stdout.txt",
    "stderr.txt",
    "parsed_eval.json",
    "diagnostics.json",
)

RUN_CYCLE_INDEX = (
    "agent_actions.jsonl",
    "cycle_intent.json",
    "cycle_plan.md",
)

CYCLE_MANIFEST = "cycle_manifest.json"


def ingest_required_names() -> tuple[str, ...]:
    return INGEST_REQUIRED


def eval_node_index_names() -> tuple[str, ...]:
    """Flat filenames in an eval bundle dir (local run_dir eval outputs or GH artifact root)."""
    return EVAL_NODE_INDEX


def local_run_index_names() -> tuple[str, ...]:
    """Filenames under .hiagentresearch/runs/<run_id>/ for registry indexing after a local cycle."""
    return EVAL_NODE_INDEX + RUN_CYCLE_INDEX


def validate_ingest_bundle(artifact_dir: Path) -> str:
    """Return an error string if the eval bundle is missing or malformed; else \"\"."""
    missing = [name for name in INGEST_REQUIRED if not (artifact_dir / name).exists()]
    if missing:
        return f"missing required artifacts: {missing}"
    malformed: list[str] = []
    for name in INGEST_REQUIRED:
        path = artifact_dir / name
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
