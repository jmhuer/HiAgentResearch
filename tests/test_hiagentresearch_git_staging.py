import subprocess
from pathlib import Path

import pytest

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.git.service import GitService, GitServiceError


def _init_repo(tmp_path: Path) -> GitService:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(
        "mnist/data/\nmnist/src/checkpoints/\n__pycache__/\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=tmp_path, check=True)
    return GitService(tmp_path)


def test_stage_research_commit_excludes_generated_artifacts(tmp_path: Path) -> None:
    service = _init_repo(tmp_path)
    config = load_config()
    excluded = config.commit_excluded_paths()

    (tmp_path / "mnist" / "src").mkdir(parents=True)
    (tmp_path / "mnist" / "src" / "model.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "mnist" / "data" / "MNIST").mkdir(parents=True)
    (tmp_path / "mnist" / "data" / "MNIST" / "raw.bin").write_bytes(b"\x00" * 1024)
    (tmp_path / "mnist" / "src" / "checkpoints").mkdir(parents=True)
    (tmp_path / "mnist" / "src" / "checkpoints" / "mnist_cnn_ensemble.pt").write_bytes(b"\x00" * 2048)
    cache_dir = tmp_path / "mnist" / "src" / "__pycache__"
    cache_dir.mkdir(parents=True)
    (cache_dir / "model.cpython-312.pyc").write_bytes(b"compiled")

    manifest = ".hiagentresearch/experiments/model_architecture/run_test.json"
    (tmp_path / manifest).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / manifest).write_text('{"run_id": "run_test"}\n', encoding="utf-8")

    service.stage_research_commit(
        workdir="mnist",
        manifest_path=manifest,
        excluded_paths=excluded,
    )

    staged = set(service.changed_files(staged=True))
    assert "mnist/src/model.py" in staged
    assert manifest in staged
    assert not any(path.startswith("mnist/data/") for path in staged)
    assert not any(path.startswith("mnist/src/checkpoints/") for path in staged)
    assert not any("__pycache__" in path for path in staged)


def test_stage_research_commit_rejects_force_staged_artifacts(tmp_path: Path) -> None:
    service = _init_repo(tmp_path)
    config = load_config()
    excluded = config.commit_excluded_paths()

    artifact = tmp_path / "mnist" / "src" / "checkpoints" / "mnist_cnn_ensemble.pt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"\x00" * 64)
    subprocess.run(["git", "add", "-f", str(artifact.relative_to(tmp_path))], cwd=tmp_path, check=True)

    with pytest.raises(GitServiceError, match="generated or read-only"):
        service.stage_research_commit(
            workdir="mnist",
            manifest_path=".hiagentresearch/experiments/model_architecture/run_x.json",
            excluded_paths=excluded,
        )
