import json
from pathlib import Path

from hiagentresearch.src.core.artifacts import (
    INGEST_REQUIRED,
    RUN_CYCLE_INDEX,
    ingest_required_names,
    local_run_index_names,
    validate_ingest_bundle,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_ingest_required_is_four_json_files() -> None:
    assert ingest_required_names() == INGEST_REQUIRED
    assert len(INGEST_REQUIRED) == 4
    assert all(name.endswith(".json") for name in INGEST_REQUIRED)


def test_local_run_index_includes_run_cycle_artifacts() -> None:
    names = local_run_index_names()
    for name in RUN_CYCLE_INDEX:
        assert name in names


def test_validate_ingest_bundle_accepts_complete_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_json(bundle / "metrics.json", {"accuracy": 0.9})
    _write_json(bundle / "failure_class.json", {"failure_class": "none"})
    _write_json(bundle / "research_outcome.json", {"passed": True})
    _write_json(bundle / "run_meta.json", {"run_id": "run_1"})

    assert validate_ingest_bundle(bundle) == ""


def test_validate_ingest_bundle_rejects_missing_files(tmp_path: Path) -> None:
    bundle = tmp_path / "empty"
    bundle.mkdir()

    error = validate_ingest_bundle(bundle)

    assert "missing required artifacts" in error


def test_validate_ingest_bundle_rejects_malformed_json(tmp_path: Path) -> None:
    bundle = tmp_path / "bad"
    bundle.mkdir()
    for name in INGEST_REQUIRED:
        (bundle / name).write_text("not-json", encoding="utf-8")

    error = validate_ingest_bundle(bundle)

    assert "malformed required artifacts" in error
