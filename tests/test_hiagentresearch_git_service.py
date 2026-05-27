import subprocess

import pytest

from hiagentresearch.src.git.service import GitService, GitServiceError


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
    assert service.has_core_staged_change(allowed_paths=["a.py"], supporting_paths=[]) is True


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
