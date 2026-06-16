"""Canonical eval diagnostics contract (framework-owned, v1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class DiagnosticsValidationError(ValueError):
    """Raised when diagnostics payload fails schema validation."""


@dataclass(slots=True)
class DiagnosticsAttachment:
    name: str
    role: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "role": self.role, "description": self.description}


@dataclass(slots=True)
class DiagnosticsPhase:
    name: str
    exit_code: int | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "exit_code": self.exit_code, "error": self.error}


@dataclass(slots=True)
class DiagnosticsCoverage:
    expected: int
    completed: int
    failed: int
    unscored: int

    def to_dict(self) -> dict[str, int]:
        return {
            "expected": self.expected,
            "completed": self.completed,
            "failed": self.failed,
            "unscored": self.unscored,
        }


@dataclass(slots=True)
class EvalDiagnostics:
    """Slim index for CI steering and agent exploration pointers."""

    summary: str
    primary_failure: str | None = None
    coverage: DiagnosticsCoverage | None = None
    phases: list[DiagnosticsPhase] = field(default_factory=list)
    attachments: list[DiagnosticsAttachment] = field(default_factory=list)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "summary": self.summary,
            "primary_failure": self.primary_failure,
            "phases": [phase.to_dict() for phase in self.phases],
            "attachments": [item.to_dict() for item in self.attachments],
        }
        if self.coverage is not None:
            payload["coverage"] = self.coverage.to_dict()
        return payload


def validate_diagnostics(payload: dict[str, Any], *, execution_blocked: bool) -> EvalDiagnostics:
    if payload.get("schema_version") != 1:
        raise DiagnosticsValidationError("diagnostics.schema_version must be 1")
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise DiagnosticsValidationError("diagnostics.summary is required")
    attachments_raw = payload.get("attachments")
    if not isinstance(attachments_raw, list):
        raise DiagnosticsValidationError("diagnostics.attachments must be a list")
    attachments = [
        DiagnosticsAttachment(
            name=str(item.get("name") or ""),
            role=str(item.get("role") or ""),
            description=str(item.get("description") or ""),
        )
        for item in attachments_raw
        if isinstance(item, dict)
    ]
    if execution_blocked and not attachments:
        raise DiagnosticsValidationError("diagnostics.attachments required when execution is blocked")
    phases_raw = payload.get("phases")
    if phases_raw is not None and not isinstance(phases_raw, list):
        raise DiagnosticsValidationError("diagnostics.phases must be a list")
    phases = [
        DiagnosticsPhase(
            name=str(item.get("name") or ""),
            exit_code=item.get("exit_code") if item.get("exit_code") is None else int(item["exit_code"]),
            error=str(item["error"]) if item.get("error") else None,
        )
        for item in (phases_raw or [])
        if isinstance(item, dict)
    ]
    coverage = None
    coverage_raw = payload.get("coverage")
    if coverage_raw is not None:
        if not isinstance(coverage_raw, dict):
            raise DiagnosticsValidationError("diagnostics.coverage must be an object")
        coverage = DiagnosticsCoverage(
            expected=int(coverage_raw.get("expected") or 0),
            completed=int(coverage_raw.get("completed") or 0),
            failed=int(coverage_raw.get("failed") or 0),
            unscored=int(coverage_raw.get("unscored") or 0),
        )
    primary_failure = payload.get("primary_failure")
    return EvalDiagnostics(
        summary=summary,
        primary_failure=str(primary_failure) if primary_failure else None,
        coverage=coverage,
        phases=phases,
        attachments=attachments,
    )


def diagnostics_from_dict(payload: dict[str, Any] | None, *, execution_blocked: bool) -> EvalDiagnostics | None:
    if not payload:
        return None
    return validate_diagnostics(payload, execution_blocked=execution_blocked)
