import subprocess

import pytest

from hiagentresearch.src.github.actions import (
    GitHubActionsError,
    GitHubActionsService,
    _parse_remote_url,
    gh_repo_slug,
)


def test_parse_remote_url_handles_ssh_https_and_enterprise() -> None:
    assert _parse_remote_url("git@github.disney.com:Org/Repo.git") == ("github.disney.com", "Org/Repo")
    assert _parse_remote_url("https://github.com/owner/repo.git") == ("github.com", "owner/repo")
    assert _parse_remote_url("ssh://git@github.disney.com/Org/Repo") == ("github.disney.com", "Org/Repo")


def test_gh_repo_slug_targets_configured_remote(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["git", "remote", "get-url"]:
            url = "git@github.disney.com:Org/Repo.git" if args[3] == "disney" else "https://github.com/owner/repo.git"
            return subprocess.CompletedProcess(args, 0, url + "\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    # Enterprise host => HOST/OWNER/REPO; github.com => OWNER/REPO.
    assert gh_repo_slug(tmp_path, "disney") == "github.disney.com/Org/Repo"
    assert gh_repo_slug(tmp_path, "origin") == "owner/repo"


def test_gh_repo_slug_fails_fast_on_unknown_remote(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 2, "", "No such remote 'disney'")
    )
    with pytest.raises(GitHubActionsError, match="get-url"):
        gh_repo_slug(tmp_path, "disney")


def test_service_injects_repo_flag_into_gh_calls(monkeypatch, tmp_path) -> None:
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitHubActionsService(tmp_path, repo="github.disney.com/Org/Repo")
    service.list_runs(branch="research/x")
    assert "--repo" in seen["args"] and "github.disney.com/Org/Repo" in seen["args"]


def test_github_actions_retries_transient_network_errors(monkeypatch, tmp_path) -> None:
    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess(args, 1, "", "net/http: TLS handshake timeout")
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("hiagentresearch.src.github.actions.time.sleep", lambda *_: None)
    service = GitHubActionsService(tmp_path)

    assert service.list_runs(branch="research/data-augmentation") == []
    assert calls["n"] == 2


def test_github_actions_retries_artifact_availability_race(monkeypatch, tmp_path) -> None:
    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess(args, 1, "", "no valid artifacts found to download")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("hiagentresearch.src.github.actions.time.sleep", lambda *_: None)
    service = GitHubActionsService(tmp_path)

    service.download_artifacts(run_id="123", target_dir=tmp_path / "artifacts")

    assert calls["n"] == 2


def test_github_actions_does_not_retry_real_errors(monkeypatch, tmp_path) -> None:
    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        return subprocess.CompletedProcess(args, 1, "", "unknown command \"bogus\" for \"gh\"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("hiagentresearch.src.github.actions.time.sleep", lambda *_: None)
    service = GitHubActionsService(tmp_path)

    try:
        service.list_runs(branch="research/data-augmentation")
        raise AssertionError("expected GitHubActionsError")
    except GitHubActionsError:
        pass
    assert calls["n"] == 1


def test_github_actions_lists_and_finds_runs(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        if args[1:3] == ["run", "list"]:
            return subprocess.CompletedProcess(
                args,
                0,
                '[{"databaseId": 123, "headSha": "abc", "name": "hiagentresearch-research-eval", "status": "completed"}]',
                "",
            )
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitHubActionsService(tmp_path)

    run = service.find_run_for_head(
        branch="research/model-architecture",
        head_sha="abc",
        workflow_name="hiagentresearch-research-eval",
        attempts=1,
        sleep_sec=0,
    )

    assert run.database_id == "123"
    assert run.head_sha == "abc"


def test_github_actions_watch_returns_true_on_successful_workflow(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        assert args[1:4] == ["run", "view", "123"]
        return subprocess.CompletedProcess(args, 0, '{"status": "completed", "conclusion": "success"}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitHubActionsService(tmp_path)

    assert service.watch_run("123") is True


def test_github_actions_watch_returns_false_on_failed_workflow(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, '{"status": "completed", "conclusion": "failure"}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitHubActionsService(tmp_path)

    assert service.watch_run("123") is False


def test_github_actions_dispatches_workflow_with_inputs(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = GitHubActionsService(tmp_path)

    service.dispatch_workflow(
        workflow_name="hiagentresearch-research-eval",
        ref="main",
        inputs={"node_kind": "baseline", "group_id": "model_architecture"},
    )

    assert calls == [
        [
            "gh",
            "workflow",
            "run",
            "hiagentresearch-research-eval",
            "--ref",
            "main",
            "-f",
            "node_kind=baseline",
            "-f",
            "group_id=model_architecture",
        ]
    ]
