import json
from pathlib import Path

from hiagentresearch.src.config import load_config
from hiagentresearch.src.github_actions import GitHubRun
from hiagentresearch.src.loop_controller import run_loops


class FakeGit:
    def __init__(self) -> None:
        self.committed = False
        self.pushed = False

    def checkout(self, branch: str) -> None:
        self.branch = branch

    def checkout_or_create(self, branch: str, *, base_branch: str = "main") -> None:
        self.branch = branch
        self.base_branch = base_branch

    def stage_paths(self, paths: list[str]) -> None:
        self.staged_paths = paths

    def changed_files(self, *, staged: bool = False) -> list[str]:
        return ["mnist/pipeline/model.py"] if staged else []

    def has_core_staged_change(self, *, allowed_paths: list[str], supporting_paths: list[str]) -> bool:
        return "mnist/pipeline/model.py" in allowed_paths

    def commit(self, *, subject: str, body: str) -> str:
        self.committed = True
        self.subject = subject
        self.body = body
        return "abc"

    def push(self, *, remote: str, branch: str) -> None:
        self.pushed = True
        self.remote = remote


class FakeGitHub:
    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir

    def find_run_for_head(self, **kwargs):
        return GitHubRun(
            database_id="123",
            head_sha=kwargs["head_sha"],
            name=kwargs["workflow_name"],
            status="completed",
        )

    def watch_run(self, run_id: str) -> bool:
        return True

    def download_artifacts(self, *, run_id: str, target_dir: Path, clean: bool = True) -> Path:
        return target_dir

    def artifact_payload_dir(self, download_dir: Path) -> Path:
        return self.artifact_dir


def test_loop_controller_commits_pushes_and_ingests(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("hiagentresearch.src.loop_controller.init_state", lambda: 0)
    artifact_dir = tmp_path / "hiagentresearch-123"
    artifact_dir.mkdir()
    (artifact_dir / "run_meta.json").write_text(
        json.dumps({"correlation_id": "run_test", "workflow_run_id": "123"}),
        encoding="utf-8",
    )
    (artifact_dir / "failure_class.json").write_text(
        json.dumps({"failure_class": "none", "exit_code": 0}),
        encoding="utf-8",
    )
    (artifact_dir / "research_outcome.json").write_text(
        json.dumps(
            {
                "research_outcome": "improved_baseline",
                "improved_baseline": True,
                "metrics_ok": True,
                "next_action": "continue",
                "reason": "configured improvement metrics were met",
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "metrics.json").write_text(json.dumps({"tests_passed": 1}), encoding="utf-8")
    (artifact_dir / "stdout.txt").write_text("{}", encoding="utf-8")
    (artifact_dir / "stderr.txt").write_text("", encoding="utf-8")

    def fake_run_group(**kwargs):
        print(json.dumps({"ok": True, "run_id": "run_test", "failure_class": "none"}))
        return 0

    ingested = {}

    def fake_ingest(run_id, group_id, branch, artifact_path):
        ingested["args"] = (run_id, group_id, branch, artifact_path)
        return 0

    git = FakeGit()
    summary = run_loops(
        group_id="model_architecture",
        branch="research/model-architecture",
        loops=1,
        workdir=Path(".").resolve(),
        quick=True,
        evidence_path=None,
        agent_model="composer-2.5",
        config=load_config(Path("config.yaml")),
        git=git,
        github=FakeGitHub(artifact_dir),
        run_group_func=fake_run_group,
        ingest_func=fake_ingest,
    )

    assert summary.ok is True
    assert summary.cycles[0].github_research_outcome == "improved_baseline"
    assert git.committed is True
    assert git.pushed is True
    assert "HiAgentResearch-Run-ID: run_test" == git.body
    assert ingested["args"][0] == "gh_123"
