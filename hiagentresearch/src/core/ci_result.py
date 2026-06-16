"""Typed view of the eval node's CI artifact bundle.

The eval node writes three JSON files per run — ``failure_class.json``,
``research_outcome.json``, ``metrics.json``. Historically every consumer re-read
those files and threaded bare ``dict.get(...)`` calls with ad-hoc defaults and
``normalize_research_outcome_name`` scattered at each call site. ``CIResult`` parses
the bundle ONCE at the boundary and exposes typed, already-normalized fields, so
downstream control flow reads attributes instead of poking dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiagentresearch.src.core.json_io import read_json_object
from hiagentresearch.src.core.outcomes import (
    normalize_research_outcome_name,
    outcome_met_targets,
)


@dataclass(frozen=True)
class CIResult:
    """Parsed, normalized result of one CI/eval node run.

    Field defaults mirror the historical per-call-site fallbacks so behavior is
    unchanged when an artifact is missing a key. ``research_outcome`` is stored
    already normalized (legacy aliases collapsed).
    """

    failure_class: str = "infra_failure"
    research_outcome: str = "unknown"
    next_action: str = ""
    reason: str = ""
    primary_error: str = ""
    error: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def met_targets(self) -> bool:
        return outcome_met_targets(self.research_outcome)

    @property
    def execution_blocked(self) -> bool:
        return self.failure_class != "none"

    def decision(self) -> str:
        """The next action to steer the following cycle toward.

        Prefer the eval node's explicit ``next_action``; otherwise derive it from
        whether targets were met / execution was blocked — matching the historical
        inline default.
        """
        if self.next_action:
            return self.next_action
        if self.met_targets:
            return "done"
        return "repair" if self.execution_blocked else "continue"

    def first_reason(self) -> str:
        """Best single human-readable reason: primary error, then error, then reason."""
        for value in (self.primary_error, self.error, self.reason):
            text = value.strip()
            if text:
                return text
        return ""

    @classmethod
    def from_dicts(
        cls,
        failure: dict[str, Any],
        outcome: dict[str, Any],
        metrics: dict[str, Any] | None = None,
    ) -> "CIResult":
        return cls(
            failure_class=str(failure.get("failure_class", "infra_failure")),
            research_outcome=normalize_research_outcome_name(
                str(outcome.get("research_outcome", "unknown"))
            ),
            next_action=str(outcome.get("next_action", "")),
            reason=str(outcome.get("reason") or ""),
            primary_error=str(failure.get("primary_error") or ""),
            error=str(failure.get("error") or ""),
            metrics=dict(metrics or {}),
        )

    @classmethod
    def from_ci_dir(cls, ci_dir: Path) -> "CIResult":
        return cls.from_dicts(
            read_json_object(ci_dir / "failure_class.json"),
            read_json_object(ci_dir / "research_outcome.json"),
            read_json_object(ci_dir / "metrics.json"),
        )
