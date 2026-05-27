import json
from pathlib import Path

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.github.actions import GitHubRun
from hiagentresearch.src.runtime.loop_controller import run_loops
from hiagentresearch.src.registry.store import Registry


class FakeGit:
    def __init__(self) -> None:
        self.committed = False
        self.pushed = False

    def checkout(self, branch: str) -> None:
        self.branch = branch

    def checkout_or_create(
        self, branch: str, *, base_branch: str = "main", start_ref: str | None = None
    ) -> None:
        self.branch = branch
        self.base_branch = base_branch
        self.start_ref = start_ref

    def resolve_ref(self, ref: str) -> str:
        return "mainsha"

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
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.init_state", lambda: 0)
    run_dir = tmp_path / ".hiagentresearch" / "runs" / "run_test"
    run_dir.mkdir(parents=True)
    (run_dir / "experiment_intent.json").write_text(
        json.dumps(
            {
                "run_id": "run_test",
                "group_id": "model_architecture",
                "objective": "Improve model architecture while preserving latency budget.",
                "hypothesis_id": "model_architecture-h1",
                "hypothesis": "Try a bounded model change.",
                "planned_code_changes": ["Replace one model layer with a smaller equivalent."],
                "target_files": ["mnist/pipeline/model.py"],
                "success_criteria": ["accuracy improves"],
                "rollback_plan": "Revert the model layer.",
            }
        ),
        encoding="utf-8",
    )
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
    installed = {}
    monkeypatch.setattr(
        "hiagentresearch.src.runtime.loop_controller._install_dependency_files",
        lambda config: installed.setdefault("called", True),
    )
    summary = run_loops(
        group_id="model_architecture",
        branch="research/model-architecture",
        loops=1,
        workdir=tmp_path,
        quick=True,
        agent_model="composer-2.5",
        config=load_config(Path("config.yaml")),
        git=git,
        github=FakeGitHub(artifact_dir),
        run_group_func=fake_run_group,
        ingest_func=fake_ingest,
    )

    assert summary.ok is True
    assert installed["called"] is True
    assert summary.cycles[0].github_research_outcome == "improved_baseline"
    assert (
        tmp_path / ".hiagentresearch" / "experiments" / "model_architecture" / "run_test.json"
    ).exists()
    assert ".hiagentresearch/experiments/model_architecture/run_test.json" in git.staged_paths
    assert git.subject == "Phase 1, loop 1: Replace one model layer with a smaller equivalent"
    registry = Registry(tmp_path / ".hiagentresearch" / "state")
    registry.init()
    assert registry.experiment_for_run("run_test")["hypothesis_id"] == "model_architecture-h1"
    assert git.committed is True
    assert git.pushed is True
    assert "HiAgentResearch-Run-ID: run_test" in git.body
    assert "Experiment-Manifest: .hiagentresearch/experiments/model_architecture/run_test.json" in git.body
    assert ingested["args"][0] == "gh_123"
