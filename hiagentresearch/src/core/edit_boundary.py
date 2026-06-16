"""Classify a changed file into exactly one edit-boundary category.

The orchestrator must decide, for each file an agent cycle changed, whether the edit
is allowed (a real workspace source change) or a boundary violation (touched the
read-only eval zone, a protected path, or strayed outside the editable subtree). This
used to be a nested if/else inside ``_validate_edit_boundary``; it now lives here as a
single ``classify(path) -> PathCategory`` so the rule is data-driven and unit-testable,
and the validator just aggregates categories into a pass/fail with a precise message.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from hiagentresearch.src.core.models import ResearchGroup
from hiagentresearch.src.core.pathspec import is_under_any, is_within, matches_any

# Dependency lockfiles a package manager (e.g. `uv`) may auto-write at the repo root as a
# side effect of an agent running it. They are tool output, never source edits, and are
# never committed (staging only stages within the workspace) — so a stray one must not
# invalidate a cycle.
_TOOL_LOCKFILES = frozenset({"uv.lock", "poetry.lock", "Pipfile.lock"})


def is_generated_artifact(path: str, generated_paths: list[str]) -> bool:
    """A framework/tool-produced file that is not an agent source edit."""
    if is_under_any(path, generated_paths):
        return True
    if Path(path).name in _TOOL_LOCKFILES:
        return True
    # Experiment manifests are framework-generated bookkeeping, not source edits.
    return is_under_any(path, [".hiagentresearch/cycles"])


class PathCategory(str, Enum):
    """The single category a changed file falls into. Order of the checks in
    ``PathClassifier.classify`` encodes precedence; see that method."""

    REFERENCE = "reference"  # read-only reference / eval zone — never editable
    HIDDEN = "hidden"  # protected denylist path — never editable
    IGNORED = "ignored"  # run-local artifact, generated path, or tool lockfile
    OUTSIDE_WORKDIR = "outside_workdir"  # a real edit, but outside the workspace
    OUTSIDE_EDITABLE = "outside_editable"  # in the workspace but outside the editable allowlist
    WORKSPACE = "workspace"  # an allowed workspace source change


class PathClassifier:
    """Assigns each changed file exactly one :class:`PathCategory`.

    Precedence (first match wins) preserves the historical validator's behavior:
    a path in the read-only reference zone is REFERENCE even if it also looks
    generated; protected (hidden) paths come next; framework/tool artifacts are
    IGNORED; only then is the path placed relative to the workspace and its
    editable allowlist.
    """

    def __init__(self, group: ResearchGroup, *, run_id: str) -> None:
        self._group = group
        self._run_prefix = f".hiagentresearch/runs/{run_id}/"

    def _is_ignored(self, path: str) -> bool:
        return path.startswith(self._run_prefix) or is_generated_artifact(
            path, self._group.generated_paths
        )

    def classify(self, path: str) -> PathCategory:
        group = self._group
        if is_under_any(path, group.reference_paths):
            return PathCategory.REFERENCE
        if is_under_any(path, group.hidden_paths):
            return PathCategory.HIDDEN
        if self._is_ignored(path):
            return PathCategory.IGNORED
        if not is_within(path, group.workdir):
            return PathCategory.OUTSIDE_WORKDIR
        if group.editable_paths and not matches_any(path, group.editable_paths):
            return PathCategory.OUTSIDE_EDITABLE
        return PathCategory.WORKSPACE
