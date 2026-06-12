import subprocess

import pytest

from hiagentresearch.src.git.service import GitService, GitServiceError
from hiagentresearch.src.git.worktree import WorktreeManager


def test_git_service_parses_status_and_staged_files(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, " M a.py\nR  old.py -> new.py\n", "")
        if args[1:] == ["diff", "--name-only", "--cached"]:
            return subprocess.CompletedProcess(args, 0, "a.py\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitService(tmp_path)

    assert service.changed_files() == ["a.py", "new.py"]
    assert service.changed_files(staged=True) == ["a.py"]
    assert (
        service.has_staged_workspace_change(
            workdir=".",
            generated_paths=[],
            reference_paths=[],
            hidden_paths=[],
        )
        is True
    )


def test_git_service_raises_on_failed_command(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitService(tmp_path)

    with pytest.raises(GitServiceError, match="boom"):
        service.checkout("missing")


def test_git_service_creates_missing_branch_from_main(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:4] == ["rev-parse", "--verify", "research/demo"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitService(tmp_path)

    service.checkout_or_create("research/demo", base_branch="main")

    assert ["git", "checkout", "-b", "research/demo", "main"] in calls


def test_worktree_requires_start_ref_for_new_branch(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "--verify", "research/demo"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager = WorktreeManager(repo_root=tmp_path)

    with pytest.raises(GitServiceError, match="missing start_ref"):
        manager.ensure("demo", "research/demo")


def test_worktree_creates_new_branch_from_start_ref(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["rev-parse", "--verify", "research/demo"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager = WorktreeManager(repo_root=tmp_path)

    manager.ensure("demo", "research/demo", start_ref="main-layer2")

    assert ["git", "worktree", "add", "-b", "research/demo", str(manager.path_for("demo")), "main-layer2"] in calls


def test_git_service_syncs_existing_branch_to_start_ref(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["rev-parse", "--verify", "research/demo"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[1:] == ["rev-parse", "abc123"]:
            return subprocess.CompletedProcess(args, 0, "abc123\n", "")
        if args[1:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, "old456\n", "")
        if args[1:] == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitService(tmp_path)

    service.checkout_or_create("research/demo", start_ref="abc123", sync_to_ref=True)

    assert ["git", "checkout", "research/demo"] in calls
    assert ["git", "reset", "--hard", "abc123"] in calls


def test_git_service_checks_out_existing_branch(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitService(tmp_path)

    service.checkout_or_create("research/demo", base_branch="main")

    assert ["git", "checkout", "research/demo"] in calls
    assert ["git", "checkout", "-b", "research/demo"] not in calls
