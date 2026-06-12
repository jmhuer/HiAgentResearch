import json
from pathlib import Path

from hiagentresearch.src.core.config import HiAgentResearchConfig, ResearchGroupConfig, load_config
from hiagentresearch.src.github.actions import GitHubRun
from hiagentresearch.src.runtime.baseline import ensure_baseline_snapshot
from hiagentresearch.src.runtime.loop_controller import (
    _extract_last_json_object,
    _preserve_parallel_failure_artifacts,
    _run_group_capture,
    run_loops,
    run_loops_all,
)
from hiagentresearch.src.registry.store import Registry
from hiagentresearch.src.core.models import IntentPacket
from hiagentresearch.src.lineage.resolve import BranchBootstrap
from hiagentresearch.src.runtime.loop_controller import _metric_regression_note


class FakeGit:
    def __init__(self) -> None:
        self.committed = False
        self.pushed = False
        self.discard_count = 0

    def discard_worktree_changes(self) -> None:
        self.discard_count += 1

    def checkout(self, branch: str) -> None:
        self.branch = branch

    def checkout_or_create(
        self,
        branch: str,
        *,
        base_branch: str = "main",
        start_ref: str | None = None,
        sync_to_ref: bool = False,
    ) -> None:
        self.branch = branch
        self.base_branch = base_branch
        self.start_ref = start_ref
        self.sync_to_ref = sync_to_ref

    def resolve_ref(self, ref: str) -> str:
        return "mainsha"

    def stage_research_commit(
        self,
        *,
        workdir: str,
        manifest_path: str,
        excluded_paths: list[str],
    ) -> None:
        self.staged_paths = [workdir, manifest_path]
        self.staged_workdir = workdir
        self.staged_manifest = manifest_path
        self.staged_excluded = excluded_paths

    def changed_files(self, *, staged: bool = False) -> list[str]:
        return ["mnist/src/model.py"] if staged else []

    def has_staged_workspace_change(
        self,
        *,
        workdir: str,
        generated_paths: list[str],
        reference_paths: list[str],
        hidden_paths: list[str],
    ) -> bool:
        return workdir == "mnist"

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


def test_extract_last_json_object_ignores_leading_noise() -> None:
    text = "Requirement already satisfied: torch\n{\"ok\": true, \"run_id\": \"r1\"}\n"
    payload = _extract_last_json_object(text)
    assert payload == {"ok": True, "run_id": "r1"}


def test_run_group_capture_parses_json_after_pip_noise() -> None:
    def fake_run_group(**kwargs):
        print("pip install noise line")
        print(json.dumps({"ok": True, "run_id": "run_x", "failure_class": "none"}))
        return 0

    payload = _run_group_capture(fake_run_group, group_id="model_architecture")
    assert payload["run_id"] == "run_x"
    assert payload["failure_class"] == "none"


def test_loop_controller_commits_pushes_and_ingests(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    monkeypatch.setenv("HIAGENTRESEARCH_STATE_DIR", str(tmp_path / ".hiagentresearch" / "state"))
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.init_state", lambda: 0)
    run_dir = tmp_path / ".hiagentresearch" / "runs" / "run_test"
    run_dir.mkdir(parents=True)
    (run_dir / "cycle_intent.json").write_text(
        json.dumps(
            {
                "run_id": "run_test",
                "group_id": "model_architecture",
                "objective": "Improve model architecture while preserving latency budget.",
                "goal_id": "model_architecture-g1",
                "goal": "Try a bounded model change.",
                "planned_code_changes": ["Replace one model layer with a smaller equivalent."],
                "target_files": ["mnist/src/model.py"],
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
                "research_outcome": "met_targets",
                "next_action": "continue",
                "reason": "configured targets were met",
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
        "hiagentresearch.src.runtime.loop_controller.install_dependency_files",
        lambda config: installed.setdefault("called", True),
    )
    registry = Registry(tmp_path / ".hiagentresearch" / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.93, "latency_ms": 5.0, "duration_sec": 1.0})
    summary = run_loops(
        group_id="model_architecture",
        branch="research/model-architecture",
        loops=1,
        workdir=tmp_path,
        agent_model="composer-2.5",
        config=load_config(Path("configs/standard.yaml")),
        git=git,
        github=FakeGitHub(artifact_dir),
        run_group_func=fake_run_group,
        ingest_func=fake_ingest,
    )

    assert summary.ok is True
    assert installed["called"] is True
    assert summary.cycles[0].github_research_outcome == "met_targets"
    assert (
        tmp_path / ".hiagentresearch" / "cycles" / "model_architecture" / "run_test.json"
    ).exists()
    assert ".hiagentresearch/cycles/model_architecture/run_test.json" in git.staged_paths
    assert git.subject == "Phase 1, loop 1: Replace one model layer with a smaller equivalent"
    assert registry.cycle_for_run("run_test")["goal_id"] == "model_architecture-g1"
    manifest = json.loads(
        (
            tmp_path / ".hiagentresearch" / "cycles" / "model_architecture" / "run_test.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["lineage_baseline_snapshot"]["metrics"]["accuracy"] == 0.93
    assert git.committed is True
    assert git.pushed is True
    assert "HiAgentResearch-Run-ID: run_test" in git.body
    assert "Experiment-Manifest: .hiagentresearch/cycles/model_architecture/run_test.json" in git.body
    assert ingested["args"][0] == "gh_123"


def test_agent_moved_head_blocks_the_loop(monkeypatch, tmp_path) -> None:
    """If the agent commits/moves HEAD during a cycle (a contract violation), the cycle
    fails fast with a clear agent_moved_head reason rather than silently continuing."""
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    monkeypatch.setenv("HIAGENTRESEARCH_STATE_DIR", str(tmp_path / ".hiagentresearch" / "state"))
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.init_state", lambda: 0)
    monkeypatch.setattr(
        "hiagentresearch.src.runtime.loop_controller.install_dependency_files", lambda config: None
    )
    registry = Registry(tmp_path / ".hiagentresearch" / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.93, "latency_ms": 5.0, "duration_sec": 1.0})

    def fake_run_group(**kwargs):
        print(json.dumps({
            "ok": False, "run_id": "run_head", "failure_class": "agent_moved_head",
            "error": "agent moved HEAD during the cycle (aaaaaaa -> bbbbbbb)",
        }))
        return 1

    summary = run_loops(
        group_id="model_architecture",
        branch="research/model-architecture",
        loops=3,
        workdir=tmp_path,
        agent_model="composer-2.5",
        config=load_config(Path("configs/standard.yaml")),
        git=FakeGit(),
        github=FakeGitHub(tmp_path / "unused"),
        run_group_func=fake_run_group,
        ingest_func=lambda *a, **k: 0,
    )

    assert summary.ok is False
    assert "agent_moved_head" in summary.reason
    assert summary.cycles == []  # blocked on loop 1, never reached commit/eval


def test_is_transient_cycle_failure_only_for_cursor_status_error() -> None:
    """A transient agent-infra failure (Cursor SDK status=error) is retryable; a deterministic
    block (agent_moved_head, a contract invalid_cycle without the transient marker) is not."""
    from hiagentresearch.src.runtime.loop_controller import _is_transient_cycle_failure

    assert _is_transient_cycle_failure({"failure_class": "invalid_cycle", "cursor_run_status": "error"}) is True
    assert _is_transient_cycle_failure({"failure_class": "invalid_cycle", "cursor_run_status": "ERROR"}) is True
    # Not transient: no cursor status marker, a clean run, or a genuine policy violation.
    assert _is_transient_cycle_failure({"failure_class": "invalid_cycle"}) is False
    assert _is_transient_cycle_failure({"failure_class": "invalid_cycle", "cursor_run_status": "finished"}) is False
    assert _is_transient_cycle_failure({"failure_class": "agent_moved_head", "cursor_run_status": "error"}) is False
    assert _is_transient_cycle_failure({"failure_class": "none"}) is False


def test_transient_agent_error_is_retried_from_clean_worktree(monkeypatch, tmp_path) -> None:
    """A transient Cursor agent error (status=error) is retried from a reset worktree instead of
    aborting the leaf — so one infra hiccup cannot cascade into aborting the whole parallel wave.
    A persistently transient error still fails fast once the bounded retry budget is exhausted."""
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    monkeypatch.setenv("HIAGENTRESEARCH_STATE_DIR", str(tmp_path / ".hiagentresearch" / "state"))
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.init_state", lambda: 0)
    monkeypatch.setattr(
        "hiagentresearch.src.runtime.loop_controller.install_dependency_files", lambda config: None
    )
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller._CYCLE_TRANSIENT_RETRIES", 3)
    registry = Registry(tmp_path / ".hiagentresearch" / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.93, "latency_ms": 5.0, "duration_sec": 1.0})

    calls = {"n": 0}

    def fake_run_group(**kwargs):
        calls["n"] += 1
        print(json.dumps({
            "ok": False, "run_id": f"run_t{calls['n']}", "failure_class": "invalid_cycle",
            "cursor_run_status": "error",
            "error": "Cursor agent run did not finish successfully (status=error).",
        }))
        return 1

    git = FakeGit()
    summary = run_loops(
        group_id="model_architecture",
        branch="research/model-architecture",
        loops=3,
        workdir=tmp_path,
        agent_model="composer-2.5",
        config=load_config(Path("configs/standard.yaml")),
        git=git,
        github=FakeGitHub(tmp_path / "unused"),
        run_group_func=fake_run_group,
        ingest_func=lambda *a, **k: 0,
    )

    # Loop 1 retried the full transient budget (3 attempts), then blocked — never reaching loop 2.
    assert calls["n"] == 3
    # The worktree was reset between attempts (twice: after attempts 1 and 2, not after the last).
    assert git.discard_count == 2
    assert summary.ok is False
    assert "invalid_cycle" in summary.reason
    assert summary.cycles == []


def test_select_collapse_zero_loops_returns_ok_without_agent(monkeypatch, tmp_path) -> None:
    """A group with loops=0 (a select collapse) runs no agent cycles: the branch is
    created at the resolved base and the loop returns ok immediately."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, ResearchGroupConfig

    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    monkeypatch.setenv("HIAGENTRESEARCH_STATE_DIR", str(tmp_path / ".hiagentresearch" / "state"))
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.init_state", lambda: 0)
    monkeypatch.setattr(
        "hiagentresearch.src.runtime.loop_controller.install_dependency_files", lambda config: None
    )
    registry = Registry(tmp_path / ".hiagentresearch" / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.9})

    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "x"},
        research_groups=[
            ResearchGroupConfig(id="g", branch="research/g", policy_mode="explore",
                                lineage=LineageConfig(mode="baseline"), loops=0),
        ],
    )

    calls = {"n": 0}

    def fake_run_group(**kwargs):
        calls["n"] += 1
        return 0

    summary = run_loops(
        group_id="g", branch="research/g", loops=5, workdir=tmp_path,
        agent_model="composer-2.5", config=config, git=FakeGit(),
        github=FakeGitHub(tmp_path / "unused"), run_group_func=fake_run_group,
        ingest_func=lambda *a, **k: 0,
    )

    assert summary.ok is True
    assert summary.cycles == []
    assert calls["n"] == 0  # the per-group loops=0 override skips all agent cycles
    assert "select collapse" in summary.reason


def test_metric_regression_note_flags_drop_below_inherited_floor(tmp_path) -> None:
    """Engineering preserve-metrics check: a note is produced only when the metric
    regressed below the floor it inherited (direction-aware); else empty."""
    registry = Registry(tmp_path)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.90, "latency_ms": 8.0})
    boot = BranchBootstrap(branch="research/polish-code", mode="baseline", start_ref="main")

    # accuracy (higher is better): dropping below the 0.90 floor regresses.
    assert _metric_regression_note(
        registry=registry, bootstrap=boot, metric="accuracy", current=0.85, minimize=False
    )
    assert not _metric_regression_note(
        registry=registry, bootstrap=boot, metric="accuracy", current=0.93, minimize=False
    )
    # latency_ms (lower is better): rising above the 8.0 floor regresses.
    assert _metric_regression_note(
        registry=registry, bootstrap=boot, metric="latency_ms", current=9.5, minimize=True
    )
    assert not _metric_regression_note(
        registry=registry, bootstrap=boot, metric="latency_ms", current=6.0, minimize=True
    )
    # No floor / no value -> no false positive.
    assert not _metric_regression_note(
        registry=registry, bootstrap=boot, metric="unknown_metric", current=1.0, minimize=False
    )


def test_loop_controller_feeds_ci_outcome_into_intent_packet(monkeypatch, tmp_path) -> None:
    """The local cycle no longer evaluates; the authoritative CI outcome must be
    written back into the intent packet so the next cycle's agent prompt reflects
    how the change actually scored (last failure class + next action)."""
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    monkeypatch.setenv("HIAGENTRESEARCH_STATE_DIR", str(tmp_path / ".hiagentresearch" / "state"))
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.init_state", lambda: 0)
    monkeypatch.setattr(
        "hiagentresearch.src.runtime.loop_controller.install_dependency_files",
        lambda config: None,
    )
    run_dir = tmp_path / ".hiagentresearch" / "runs" / "run_ci"
    run_dir.mkdir(parents=True)
    (run_dir / "cycle_intent.json").write_text(
        json.dumps(
            {
                "run_id": "run_ci",
                "group_id": "model_architecture",
                "objective": "Improve model architecture while preserving latency budget.",
                "goal_id": "model_architecture-g1",
                "goal": "Try a bounded model change.",
                "planned_code_changes": ["Edit model.py"],
                "target_files": ["mnist/src/model.py"],
                "success_criteria": ["accuracy improves"],
                "rollback_plan": "Revert.",
            }
        ),
        encoding="utf-8",
    )
    artifact_dir = tmp_path / "hiagentresearch-ci"
    artifact_dir.mkdir()
    (artifact_dir / "run_meta.json").write_text(
        json.dumps({"correlation_id": "run_ci", "workflow_run_id": "777"}), encoding="utf-8"
    )
    # Authoritative CI verdict for this cycle: a code failure that wants a repair.
    (artifact_dir / "failure_class.json").write_text(
        json.dumps({"failure_class": "code_failure", "exit_code": 1}), encoding="utf-8"
    )
    (artifact_dir / "research_outcome.json").write_text(
        json.dumps(
            {"research_outcome": "below_targets", "next_action": "repair", "reason": "regressed"}
        ),
        encoding="utf-8",
    )
    (artifact_dir / "metrics.json").write_text(json.dumps({"tests_passed": 1}), encoding="utf-8")
    (artifact_dir / "stdout.txt").write_text("{}", encoding="utf-8")
    (artifact_dir / "stderr.txt").write_text("", encoding="utf-8")

    registry = Registry(tmp_path / ".hiagentresearch" / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.93, "latency_ms": 5.0})
    # Seed a stale intent packet to prove the CI verdict overwrites it.
    registry.write_intent_packet(
        IntentPacket(
            group_id="model_architecture",
            active_goal_id="model_architecture-g1",
            goal_text="Try a bounded model change.",
            attempt_count=1,
            last_failure_class="none",
            next_action="continue",
        )
    )

    def fake_run_group(**kwargs):
        print(json.dumps({"ok": True, "run_id": "run_ci", "failure_class": "none"}))
        return 0

    run_loops(
        group_id="model_architecture",
        branch="research/model-architecture",
        loops=1,
        workdir=tmp_path,
        agent_model="composer-2.5",
        config=load_config(Path("configs/standard.yaml")),
        git=FakeGit(),
        github=FakeGitHub(artifact_dir),
        run_group_func=fake_run_group,
        ingest_func=lambda *a, **k: 0,
    )

    updated = registry.read_intent_packet("model_architecture")
    assert updated is not None
    assert updated.last_failure_class == "code_failure"
    assert updated.next_action == "repair"


def test_preserve_parallel_failure_artifacts_copies_worktree_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    worktree_root = tmp_path / ".hiagentresearch" / "worktrees"
    run_dir = worktree_root / "model_architecture" / ".hiagentresearch" / "runs" / "run_failed"
    run_dir.mkdir(parents=True)
    (run_dir / "agent_stream.jsonl").write_text('{"type":"run_started"}\n', encoding="utf-8")
    (run_dir / "agent_backend_record.json").write_text(
        json.dumps({"raw_result": {"sdk_run_id": "sdk_run_123"}}),
        encoding="utf-8",
    )

    class FakeWorktrees:
        def path_for(self, group_id: str) -> Path:
            return worktree_root / group_id

    preserved = _preserve_parallel_failure_artifacts(["model_architecture"], FakeWorktrees())

    assert preserved == [".hiagentresearch/failed-runs/model_architecture/run_failed"]
    copied = tmp_path / preserved[0]
    assert (copied / "agent_stream.jsonl").read_text(encoding="utf-8") == '{"type":"run_started"}\n'
    assert json.loads((copied / "agent_backend_record.json").read_text(encoding="utf-8"))["raw_result"][
        "sdk_run_id"
    ] == "sdk_run_123"
    assert (copied / "worktree_status.txt").exists()
    assert (copied / "worktree_diff.patch").exists()


def test_parallel_loops_all_checks_out_configured_baseline(monkeypatch, tmp_path) -> None:
    """Parallel orchestration must respect the configured baseline branch.

    Layer-specific configs can use non-default baselines (for example main-layer2);
    checking out a hardcoded default branch before creating worktrees drops the files
    those configs depend on.
    """
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    monkeypatch.setenv("HIAGENTRESEARCH_STATE_DIR", str(tmp_path / ".hiagentresearch" / "state"))
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.ensure_cursor_api_key", lambda: None)
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.ensure_baseline_snapshot", lambda *a, **k: None)

    checked_out: list[str] = []

    class FakeGitService:
        def __init__(self, repo_root: Path) -> None:
            self.repo_root = repo_root

        def checkout(self, branch: str) -> None:
            checked_out.append(branch)

    class FakeWorktreeManager:
        def __init__(self, worktree_root: str) -> None:
            self.worktree_root = worktree_root
            self.removed = False

        def remove_all(self) -> None:
            self.removed = True

    parallel_calls: list[list[str]] = []

    def fake_run_wave_parallel(wave, **kwargs):
        parallel_calls.append(list(wave))
        return 0

    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.GitService", FakeGitService)
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.WorktreeManager", FakeWorktreeManager)
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller._run_wave_parallel", fake_run_wave_parallel)

    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={
            "entrypoint": ".hiagentresearch/eval/run.py",
            "command_template": "true",
            "targets": {"accuracy": {"min": 0.9}},
        },
        policy_modes={"explore": "Explore."},
        orchestration={
            "baseline_ref": "main-layer2",
            "execution_waves": [["g1", "g2"]],
            "worktree_root": ".hiagentresearch/worktrees",
        },
        research_groups=[
            ResearchGroupConfig(id="g1", branch="research/g1", objective="t", policy_mode="explore"),
            ResearchGroupConfig(id="g2", branch="research/g2", objective="t", policy_mode="explore"),
        ],
    )

    assert run_loops_all(
        loops=1,
        workdir=tmp_path,
        agent_model="composer-2.5",
        config=config,
        parallel=True,
    ) == 0

    assert checked_out == ["main-layer2"]
    assert parallel_calls == [["g1", "g2"]]


def test_ensure_baseline_snapshot_uses_github_eval_node(tmp_path) -> None:
    registry = Registry(tmp_path / ".hiagentresearch" / "state")
    registry.init()

    class FakeGit:
        def resolve_ref(self, ref: str) -> str:
            assert ref == "main"
            return "abcdef1234567890"

    class FakeGitHub:
        def __init__(self) -> None:
            self.dispatched = {}

        def list_runs(self, *, branch: str, limit: int = 20):
            assert branch == "main"
            return [GitHubRun(database_id="old", head_sha="abcdef1234567890", name="hiagentresearch-research-eval", status="completed")]

        def dispatch_workflow(self, *, workflow_name: str, ref: str, inputs: dict[str, str]) -> None:
            self.dispatched = {"workflow_name": workflow_name, "ref": ref, "inputs": inputs}

        def find_new_run_for_head(self, **kwargs):
            assert kwargs["known_run_ids"] == {"old"}
            return GitHubRun(
                database_id="123",
                head_sha=kwargs["head_sha"],
                name=kwargs["workflow_name"],
                status="completed",
            )

        def watch_run(self, run_id: str) -> bool:
            assert run_id == "123"
            return True

        def download_artifacts(self, *, run_id: str, target_dir: Path, clean: bool = True) -> Path:
            artifact_dir = target_dir / "hiagentresearch-123"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "metrics.json").write_text(
                json.dumps({"accuracy": 0.9, "latency_ms": 5.0}),
                encoding="utf-8",
            )
            (artifact_dir / "failure_class.json").write_text(
                json.dumps({"failure_class": "none"}),
                encoding="utf-8",
            )
            return target_dir

        def artifact_payload_dir(self, download_dir: Path) -> Path:
            return download_dir / "hiagentresearch-123"

    fake_github = FakeGitHub()

    ensure_baseline_snapshot(
        registry,
        load_config(Path("configs/standard.yaml")),
        github=fake_github,
        git=FakeGit(),
    )

    snapshot = registry.baseline_snapshot()
    assert snapshot["ref"] == "main"
    assert snapshot["metrics"]["accuracy"] == 0.9
    assert fake_github.dispatched["inputs"]["node_kind"] == "baseline"
    assert fake_github.dispatched["inputs"]["correlation_id"] == "baseline_abcdef123456"


# --- group success vs. code_failure (a crash is a discarded, repairable attempt) ---------

def _seed_loop_intent(tmp_path: Path, run_id: str) -> None:
    """Create the run-dir intent packet a loop reads before writing its manifest."""
    run_dir = tmp_path / ".hiagentresearch" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cycle_intent.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "group_id": "model_architecture",
                "objective": "Improve model architecture while preserving latency budget.",
                "goal_id": "model_architecture-g1",
                "goal": "Try a bounded model change.",
                "planned_code_changes": ["Edit model.py"],
                "target_files": ["mnist/src/model.py"],
                "success_criteria": ["accuracy improves"],
                "rollback_plan": "Revert.",
            }
        ),
        encoding="utf-8",
    )


def _write_ci_artifacts(
    artifact_dir: Path, *, correlation_id: str, failure_class: str, outcome: str, next_action: str
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "run_meta.json").write_text(
        json.dumps({"correlation_id": correlation_id, "workflow_run_id": correlation_id}),
        encoding="utf-8",
    )
    (artifact_dir / "failure_class.json").write_text(
        json.dumps({"failure_class": failure_class, "exit_code": 0 if failure_class == "none" else 1}),
        encoding="utf-8",
    )
    (artifact_dir / "research_outcome.json").write_text(
        json.dumps({"research_outcome": outcome, "next_action": next_action, "reason": "x"}),
        encoding="utf-8",
    )
    (artifact_dir / "metrics.json").write_text(json.dumps({"tests_passed": 1}), encoding="utf-8")
    (artifact_dir / "stdout.txt").write_text("{}", encoding="utf-8")
    (artifact_dir / "stderr.txt").write_text("", encoding="utf-8")


class SequencedGitHub:
    """Serves a distinct CI artifact dir + run id per loop, in call order."""

    def __init__(self, artifact_dirs: list[Path]) -> None:
        self._dirs = artifact_dirs
        self._idx = -1

    def find_run_for_head(self, **kwargs):
        self._idx += 1
        return GitHubRun(
            database_id=str(1000 + self._idx),
            head_sha=kwargs["head_sha"],
            name=kwargs["workflow_name"],
            status="completed",
        )

    def watch_run(self, run_id: str) -> bool:
        return True

    def download_artifacts(self, *, run_id: str, target_dir: Path, clean: bool = True) -> Path:
        return target_dir

    def artifact_payload_dir(self, download_dir: Path) -> Path:
        return self._dirs[self._idx]


def _run_loops_with_ci(monkeypatch, tmp_path, loop_specs):
    """Drive run_loops over `loop_specs` = [(run_id, failure_class, outcome, next_action)],
    one CI verdict per loop, and return the LoopSummary."""
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.REPO_ROOT", tmp_path)
    monkeypatch.setenv("HIAGENTRESEARCH_STATE_DIR", str(tmp_path / ".hiagentresearch" / "state"))
    monkeypatch.setattr("hiagentresearch.src.runtime.loop_controller.init_state", lambda: 0)
    monkeypatch.setattr(
        "hiagentresearch.src.runtime.loop_controller.install_dependency_files", lambda config: None
    )

    artifact_dirs: list[Path] = []
    for i, (run_id, failure_class, outcome, next_action) in enumerate(loop_specs):
        artifact_dir = tmp_path / f"hiagentresearch-ci-{i}"
        _write_ci_artifacts(
            artifact_dir,
            correlation_id=run_id,
            failure_class=failure_class,
            outcome=outcome,
            next_action=next_action,
        )
        artifact_dirs.append(artifact_dir)

    registry = Registry(tmp_path / ".hiagentresearch" / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.93, "latency_ms": 5.0})

    state = {"n": 0}

    def fake_run_group(**kwargs):
        run_id = loop_specs[state["n"]][0]
        state["n"] += 1
        _seed_loop_intent(tmp_path, run_id)
        print(json.dumps({"ok": True, "run_id": run_id, "failure_class": "none"}))
        return 0

    return run_loops(
        group_id="model_architecture",
        branch="research/model-architecture",
        loops=len(loop_specs),
        workdir=tmp_path,
        agent_model="composer-2.5",
        config=load_config(Path("configs/standard.yaml")),
        git=FakeGit(),
        github=SequencedGitHub(artifact_dirs),
        run_group_func=fake_run_group,
        ingest_func=lambda *a, **k: 0,
    )


def test_code_failure_then_clean_makes_group_ok(monkeypatch, tmp_path) -> None:
    """A code_failure is a discarded, repairable attempt — not terminal. If a later loop
    produces a clean committable result, the group is ok (so the parallel wave is not
    aborted by a crash that was subsequently repaired)."""
    summary = _run_loops_with_ci(
        monkeypatch,
        tmp_path,
        [
            ("run_l1", "code_failure", "execution_blocked", "repair"),
            ("run_l2", "none", "below_targets", "continue"),
        ],
    )
    assert summary.ok is True
    assert [cycle.github_failure_class for cycle in summary.cycles] == ["code_failure", "none"]
    assert summary.reason == "requested loops completed"


def test_all_code_failures_make_group_not_ok(monkeypatch, tmp_path) -> None:
    """An all-failure group has no clean result to inherit or merge: it is a genuine
    dead-end, stays not-ok, and (under loops-all) aborts the wave."""
    summary = _run_loops_with_ci(
        monkeypatch,
        tmp_path,
        [
            ("run_l1", "code_failure", "execution_blocked", "repair"),
            ("run_l2", "code_failure", "execution_blocked", "repair"),
        ],
    )
    assert summary.ok is False
    assert summary.reason == "max loops reached without a clean result"


def test_clean_then_code_failure_on_last_loop_is_ok(monkeypatch, tmp_path) -> None:
    """A crash on the final loop does not erase an earlier clean result: the group keeps
    its clean committable top commit and stays ok."""
    summary = _run_loops_with_ci(
        monkeypatch,
        tmp_path,
        [
            ("run_l1", "none", "below_targets", "continue"),
            ("run_l2", "code_failure", "execution_blocked", "repair"),
        ],
    )
    assert summary.ok is True
    assert [cycle.github_failure_class for cycle in summary.cycles] == ["none", "code_failure"]
