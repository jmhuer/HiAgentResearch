import subprocess

from hiagentresearch.src.github.actions import GitHubActionsService


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


def test_github_actions_watch_returns_false_on_failed_workflow(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "workflow failed")

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
