import os
import subprocess
from pathlib import Path

from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, OrchestrationConfig, ResearchGroupConfig
from hiagentresearch.src.git.service import GitService
from hiagentresearch.src.lineage.promotion import resolve_promotion_anchor
from hiagentresearch.src.registry.store import Registry
from hiagentresearch.src.runtime.promote import promote_research_baseline


def _group(group_id: str, *, top_commit_policy: str = "best_commit") -> ResearchGroupConfig:
    return ResearchGroupConfig(
        id=group_id,
        branch=f"research/{group_id}",
        objective="test",
        policy_mode="explore",
        lineage=LineageConfig(top_commit_policy=top_commit_policy),
    )


def _config(*, groups: list[ResearchGroupConfig], targets: dict | None = None, promote_from_group: str = ""):
    return HiAgentResearchConfig(
        project_id="demo",
        workdir="src",
        evaluation={
            "entrypoint": ".hiagentresearch/eval/run.py",
            "command_template": "true",
            "targets": targets or {"accuracy": {"min": 0.9}},
        },
        policy_modes={"explore": "Explore."},
        orchestration=OrchestrationConfig(
            baseline_ref="main",
            promote_from_group=promote_from_group,
        ),
        research_groups=groups,
    )


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    proc = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def test_resolve_promotion_anchor_uses_config_group(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_prompt",
        group_id="prompt",
        branch="research/prompt",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.92},
        commit_sha="promptsha",
    )
    registry.record_run(
        run_id="gh_gate",
        group_id="gate",
        branch="research/gate",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.95},
        commit_sha="gatesha",
    )

    anchor = resolve_promotion_anchor(
        config=_config(groups=[_group("prompt"), _group("gate")], promote_from_group="prompt"),
        registry=registry,
        git=GitService(repo),
    )

    assert anchor.promote_from_group == "prompt"
    assert anchor.commit_sha == "promptsha"


def test_resolve_promotion_anchor_auto_policy_winner_is_direction_aware(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_slow",
        group_id="slow",
        branch="research/slow",
        status="finished",
        failure_class="none",
        metrics={"latency_ms": 12.0},
        commit_sha="slowsha",
    )
    registry.record_run(
        run_id="gh_fast",
        group_id="fast",
        branch="research/fast",
        status="finished",
        failure_class="none",
        metrics={"latency_ms": 4.0},
        commit_sha="fastsha",
    )

    anchor = resolve_promotion_anchor(
        config=_config(
            groups=[_group("slow"), _group("fast")],
            targets={"latency_ms": {"max": 10.0}},
        ),
        registry=registry,
        git=GitService(repo),
    )

    assert anchor.promote_from_group == "fast"
    assert anchor.commit_sha == "fastsha"
    assert anchor.metric_value == 4.0


def test_resolve_promotion_anchor_honors_last_commit_policy(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_old_best",
        group_id="engineering",
        branch="research/engineering",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.99},
        commit_sha="metricbestsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_old_best",
        manifest_path=".hiagentresearch/cycles/engineering/gh_old_best.json",
        manifest={"group_id": "engineering", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_latest",
        group_id="engineering",
        branch="research/engineering",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.80},
        commit_sha="latestsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_latest",
        manifest_path=".hiagentresearch/cycles/engineering/gh_latest.json",
        manifest={"group_id": "engineering", "loop_index": 2},
    )

    anchor = resolve_promotion_anchor(
        config=_config(
            groups=[_group("engineering", top_commit_policy="last_commit")],
            promote_from_group="engineering",
        ),
        registry=registry,
        git=GitService(repo),
    )

    assert anchor.top_commit_policy == "last_commit"
    assert anchor.commit_sha == "latestsha"
    assert anchor.metric_value == 0.80


def test_promote_research_baseline_creates_target_branch_and_restores_policy_winner_workdir_only(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "src").mkdir()
    (repo / "eval").mkdir()
    (repo / "src" / "app.py").write_text("baseline product\n", encoding="utf-8")
    (repo / "eval" / "tool.py").write_text("baseline control\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    _git(repo, "checkout", "-b", "research/prompt")
    (repo / "src" / "app.py").write_text("policy-selected product\n", encoding="utf-8")
    (repo / "eval" / "tool.py").write_text("policy-selected control\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "winner")
    winner_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")

    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_prompt",
        group_id="prompt",
        branch="research/prompt",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.95},
        commit_sha=winner_sha,
    )
    config = _config(groups=[_group("prompt")], promote_from_group="prompt")

    result = promote_research_baseline(
        config=config,
        registry=registry,
        git=GitService(repo),
        target_branch="main-next",
    )

    assert result.target_created is True
    assert result.committed is True
    assert _git(repo, "branch", "--show-current") == "main-next"
    assert (repo / "src" / "app.py").read_text(encoding="utf-8") == "policy-selected product\n"
    assert (repo / "eval" / "tool.py").read_text(encoding="utf-8") == "baseline control\n"
