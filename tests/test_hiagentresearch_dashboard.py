import json
import sqlite3
import subprocess
from pathlib import Path

from hiagentresearch.src.core.config import load_config
from hiagentresearch.src.dashboard import build as dashboard_build
from hiagentresearch.src.dashboard.build import _area_lineage, _area_tabs, _collapse_result_points, _dashboard_display_metric_names, _lineage_parents, _lineage_topology, _repository_metadata, build_from_artifacts, build_from_registry
from hiagentresearch.src.dashboard.cli import main
from hiagentresearch.src.registry.store import Registry


def test_dashboard_display_metric_names_preserve_config_order() -> None:
    assert _dashboard_display_metric_names(
        configured=["macro_f1", "duration_sec", "weighted_f1"],
        available=["duration_sec", "macro_f1", "weighted_f1", "pytest_exit_code"],
    ) == ["macro_f1", "duration_sec", "weighted_f1"]


def test_dashboard_summary_metric_names_follow_config_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "jmhuer/HiAgentResearch")
    runtime_root = Path(__file__).resolve().parents[1]
    config = load_config(runtime_root / "configs" / "standard.yaml")
    state_dir = tmp_path / "state"
    _seed_registry(state_dir)
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=config)
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["metric_names"] == ["accuracy", "latency_ms"]


def test_dashboard_build_outputs_sanitized_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "jmhuer/HiAgentResearch")
    state_dir = tmp_path / "state"
    registry = _seed_registry(state_dir)
    artifact = tmp_path / "stdout.txt"
    artifact.write_text("{}", encoding="utf-8")
    registry.record_artifact(run_id="run_abc", artifact_path=artifact, artifact_type="local_eval", base_dir=tmp_path)

    output_dir = tmp_path / "dashboard"
    runtime_root = Path(__file__).resolve().parents[1]
    result = build_from_registry(
        state_dir=state_dir,
        output_dir=output_dir,
        config=load_config(runtime_root / "configs" / "standard.yaml"),
    )

    assert result.database_path.exists()
    assert (output_dir / "index.html").exists()
    assert (output_dir / "app.js").exists()
    assert "Single result — no competing approaches to select or merge." in (
        output_dir / "app.js"
    ).read_text(encoding="utf-8")
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dashboard_schema_version"] == 1
    assert manifest["sqlite"]["worker_url"] == "sqlite.worker.js"
    assert manifest["sqlite"]["wasm_url"] == "sql-wasm.wasm"
    assert manifest["repository"]["commit_url_template"] == "https://github.com/jmhuer/HiAgentResearch/commit/{commit_sha}"
    assert manifest["repository"]["workflow_run_url_template"] == "https://github.com/jmhuer/HiAgentResearch/actions/runs/{workflow_run_id}"
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    assert snapshot["metric_names"] == ["accuracy", "latency_ms"]
    assert snapshot["cycles"][0]["goal_id"] == "h1"
    config = load_config(runtime_root / "configs" / "standard.yaml")
    accuracy_min = config.evaluation.targets["accuracy"].min
    assert {
        "group_id": "model_architecture",
        "metric_name": "accuracy",
        "min": accuracy_min,
        "max": None,
        "source": "global",
    } in snapshot["metric_targets"]
    # The merge group runs in the final wave but is NOT a chain node (it has its own
    # Merge section), so chains are unchanged and merge_best only shows in the waves.
    assert snapshot["lineage_topology"]["chains"] == [
        ["model_architecture", "optimization_strategy", "hyperparameter_optimization", "polish_code"],
        ["data_augmentation"],
    ]
    assert snapshot["lineage_topology"]["execution_waves"] == [
        ["model_architecture", "data_augmentation"],
        ["optimization_strategy"],
        ["hyperparameter_optimization"],
        ["polish_code"],
        ["merge_best"],
    ]
    assert [m["group_id"] for m in snapshot["lineage_topology"]["merge_groups"]] == ["merge_best"]
    assert all("trajectory_x" in row for row in snapshot["metrics"])

    conn = sqlite3.connect(result.database_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM metric_series").fetchone()[0] == 2
        assert (
            conn.execute(
                """
                SELECT min_value
                FROM metric_expectations
                WHERE group_id = 'model_architecture' AND metric_name = 'accuracy'
                """
            ).fetchone()[0]
            == accuracy_min
        )
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'intent_packets'").fetchone() is None
    finally:
        conn.close()


def test_dashboard_stars_best_commit_per_viewed_metric(tmp_path) -> None:
    """Each run contributes a row for every configured metric (regression for a dedupe
    bug that kept only the alphabetically-first metric), AND the chart ★ marks the best
    commit FOR THE VIEWED metric — direction-aware. With accuracy-best and latency-best
    on different commits, each metric stars its own winner, not a single shared one."""
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir, with_baseline=True, baseline_metrics={"accuracy": 0.81, "latency_ms": 50.0}
    )
    # data_augmentation is its own chain (no inherit children, so no branch-point
    # diamonds to confuse the assertions). m1 = lowest latency; m2 = highest accuracy.
    for run_id, sha, acc, latency, loop in (
        ("gh_m1", "m1sha", 0.90, 5.0, 1),
        ("gh_m2", "m2sha", 0.95, 9.0, 2),
    ):
        registry.record_run(
            run_id=run_id,
            group_id="data_augmentation",
            branch="research/data-augmentation",
            status="finished",
            failure_class="none",
            metrics={"accuracy": acc, "latency_ms": latency},
            commit_sha=sha,
        )
        registry.record_cycle_manifest(
            run_id=run_id,
            manifest_path=f".hiagentresearch/cycles/data_augmentation/{run_id}.json",
            manifest={"group_id": "data_augmentation", "loop_index": loop},
        )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))

    def cell(run_id, metric):
        return next(
            r for r in snapshot["metrics"]
            if r.get("run_id") == run_id and r.get("metric_name") == metric
        )

    # Both metrics are emitted for each run (dedupe regression guard).
    assert cell("gh_m1", "latency_ms")["metric_value"] == 5.0
    assert cell("gh_m1", "accuracy")["metric_value"] == 0.9
    # accuracy chart: m2 (0.95) is starred; m1 is a plain point.
    assert cell("gh_m2", "accuracy")["symbol"] == "star"
    assert cell("gh_m1", "accuracy")["symbol"] == "circle"
    # latency chart: m1 (5.0, lowest) is starred. m2 is the accuracy-best lineage tip, so the
    # final merge now builds from it — making m2 a branch-point anchor (diamond, metric-
    # independent) on the latency chart where it isn't the winner.
    assert cell("gh_m1", "latency_ms")["symbol"] == "star"
    assert cell("gh_m2", "latency_ms")["symbol"] == "diamond"


def test_repository_link_is_host_agnostic(monkeypatch) -> None:
    """The repository button + commit/workflow links are derived from the configured
    git remote (config.github.remote), host-agnostically — so they resolve for both
    public github.com and GitHub Enterprise with the single `github.remote` knob, no
    GitHub Actions env required. Locally the only config option is the remote name."""
    config = load_config()

    # GitHub Enterprise SSH remote.
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(
        dashboard_build, "_git_remote_url",
        lambda remote: "git@github.disney.com:InformationAdvantage-AIML/HiAgentResearch.git",
    )
    repo = _repository_metadata(config)
    assert repo["web_url"] == "https://github.disney.com/InformationAdvantage-AIML/HiAgentResearch"
    assert repo["repository"] == "InformationAdvantage-AIML/HiAgentResearch"
    assert repo["commit_url_template"].startswith("https://github.disney.com/")

    # Public github.com HTTPS remote — same code path, no special-casing.
    monkeypatch.setattr(
        dashboard_build, "_git_remote_url",
        lambda remote: "https://github.com/jmhuer/HiAgentResearch.git",
    )
    repo = _repository_metadata(config)
    assert repo["web_url"] == "https://github.com/jmhuer/HiAgentResearch"
    assert repo["repository"] == "jmhuer/HiAgentResearch"

    # GitHub Actions path (env set) is honored when present.
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.disney.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Org/Repo")
    repo = _repository_metadata(config)
    assert repo["web_url"] == "https://github.disney.com/Org/Repo"
    assert repo["repository"] == "Org/Repo"


def test_dashboard_summary_includes_baseline_snapshot(tmp_path) -> None:
    state_dir = tmp_path / "state"
    _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())

    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    baseline = snapshot["lineage_topology"]["baseline_snapshot"]
    assert baseline["metrics"]["accuracy"] == 0.81
    assert summary["lineage_topology"]["baseline_snapshot"]["metrics"]["accuracy"] == 0.81
    assert "optimization_strategy" in summary["lineage_topology"]["inherit_anchors"]
    anchors = [row for row in snapshot["metrics"] if row.get("is_baseline_anchor")]
    assert anchors
    assert all(row["trajectory_x"] == 0 for row in anchors)


def test_dashboard_skips_l0_baseline_for_inherit_groups(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="gh_opt",
        group_id="optimization_strategy",
        branch="research/optimization-strategy",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.88, "latency_ms": 8.0},
        commit_sha="optsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_opt",
        manifest_path=".hiagentresearch/cycles/optimization_strategy/gh_opt.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "parentsha",
            "lineage_anchor_policy": "best_commit",
        },
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    opt_baselines = [
        row
        for row in snapshot["metrics"]
        if row.get("group_id") == "optimization_strategy" and row.get("is_baseline_anchor")
    ]
    model_baselines = [
        row
        for row in snapshot["metrics"]
        if row.get("group_id") == "model_architecture" and row.get("is_baseline_anchor")
    ]
    assert not opt_baselines
    assert model_baselines


def test_dashboard_inherit_anchor_uses_resolved_best_commit(tmp_path, monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.949, "latency_ms": 6.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="gh_loop1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.914, "latency_ms": 8.0},
        commit_sha="loop1sha",
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    topology = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))["lineage_topology"]
    anchor = topology["inherit_anchors"]["optimization_strategy"]
    assert anchor["commit_sha"] == "mainsha"
    assert anchor["parent_trajectory_step"] == 0


def test_topology_emits_area_tabs_and_group_metadata() -> None:
    """The desugared hierarchy projects to a tab per area (leaves + collapse) plus a
    final-merge tab; group_meta carries area/role so the frontend is backend-driven."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, ResearchGroupConfig

    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "x", "exploit": "y"},
        research_groups=[
            ResearchGroupConfig(id="arch", objective="a", policy_mode="explore",
                                lineage=LineageConfig(mode="baseline"), combine=False,
                                approaches=["deepen", "widen"]),
            ResearchGroupConfig(id="opt", objective="o", policy_mode="exploit",
                                lineage=LineageConfig(mode="inherit", inherit_from="arch"),
                                approaches=["cosine", "wd"]),
            ResearchGroupConfig(id="aug", objective="g", policy_mode="explore",
                                lineage=LineageConfig(mode="baseline"),
                                approaches=["randaug", "mixup"]),
        ],
    )
    topology = _lineage_topology(config)
    tabs = topology["tabs"]
    # One tab per area; the final merge has NO tab of its own (it lives on the Overview).
    assert [t["area"] for t in tabs] == ["arch", "opt", "aug"]
    arch_tab = tabs[0]
    assert arch_tab["leaves"] == ["arch__a1", "arch__a2"]
    assert arch_tab["collapse"] == "arch__collapse"
    assert all(t["area"] != "final_merge" for t in tabs)
    # group_meta carries hierarchy placement for per-tab scoping.
    meta = topology["groups"]
    assert meta["arch__a1"]["area"] == "arch" and meta["arch__a1"]["role"] == "leaf"
    assert meta["arch__collapse"]["role"] == "collapse"
    assert meta["final_merge"]["role"] == "final_merge"
    # Leaf nodes carry their goal so the lineage panel can label them by idea.
    assert meta["arch__a1"]["seed_approach"] == "deepen"
    assert meta["arch__collapse"]["seed_approach"] == ""


def test_area_lineage_is_area_level_for_both_modes() -> None:
    """The Overview map works at the area level: areas as result nodes, ancestor chains to
    L0. A linear config's areas are its groups, so it reduces to the group chains."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, ResearchGroupConfig

    fanout = HiAgentResearchConfig(
        project_id="d", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "x", "exploit": "y"},
        research_groups=[
            ResearchGroupConfig(id="arch", objective="a", policy_mode="explore",
                                lineage=LineageConfig(mode="baseline"), combine=False,
                                approaches=["deepen", "widen"]),
            ResearchGroupConfig(id="opt", objective="o", policy_mode="exploit",
                                lineage=LineageConfig(mode="inherit", inherit_from="arch"),
                                approaches=["cosine", "wd"]),
        ],
    )
    al = _area_lineage(fanout)
    # Areas are nodes; each represented by its result group (collapse for fan-out areas).
    assert al["areas"]["arch"]["result_group"] == "arch__collapse"
    assert al["areas"]["opt"]["result_group"] == "opt__collapse"
    # opt's lineage traces to L0 through arch (area-level ancestor, approaches abstracted).
    assert al["areas"]["opt"]["ancestors"] == ["arch"]
    assert al["chains"] == [["arch", "opt"]]

    # Linear: areas == groups, so the area chain is the group chain.
    al_flat = _area_lineage(load_config())
    flat_chain = next(c for c in al_flat["chains"] if "model_architecture" in c)
    assert flat_chain[0] == "model_architecture"
    assert al_flat["areas"]["model_architecture"]["result_group"] == "model_architecture"


def test_area_tabs_one_per_group_for_flat_config() -> None:
    """A flat config gets one tab per research group (each is a single-leaf area). The merge
    group (merge_best) gets NO tab — it's the Overview's final merge — so the dashboard is
    consistently tabbed across modes."""
    config = load_config()  # canonical flat standard.yaml
    tabs = _area_tabs(config)
    areas = [t["area"] for t in tabs]
    assert "model_architecture" in areas
    # The merge group is not a tab.
    assert "merge_best" not in areas
    # Each tab carries the group's objective for its description.
    arch_tab = next(t for t in tabs if t["area"] == "model_architecture")
    assert "architecture" in arch_tab["objective"].lower()


def test_dashboard_topology_includes_inherit_anchors(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="gh_parent",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.95, "latency_ms": 10.0},
        commit_sha="parentsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_parent",
        manifest_path=".hiagentresearch/cycles/model_architecture/gh_parent.json",
        manifest={
            "group_id": "model_architecture",
            "loop_index": 1,
            "goal_id": "parent",
            "goal": "Parent loop",
            "target_files": ["mnist/src/model.py"],
            "planned_code_changes": ["Edit model.py"],
        },
    )
    registry.record_cycle_manifest(
        run_id="run_child",
        manifest_path=".hiagentresearch/cycles/optimization_strategy/run_child.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "parentsha",
            "lineage_anchor_policy": "best_commit",
        },
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    topology = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))["lineage_topology"]
    anchor = topology["inherit_anchors"]["optimization_strategy"]
    assert anchor["commit_sha"] == "parentsha"
    assert anchor["parent_anchor_loop_index"] == 1


def test_dashboard_resolves_hyperparameter_anchor_from_optimization_origin(tmp_path, monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "main"]:
            return subprocess.CompletedProcess(args, 0, "mainsha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    state_dir = tmp_path / "state"
    registry = _seed_registry(
        state_dir,
        with_baseline=True,
        baseline_metrics={"accuracy": 0.80, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="gh_model_2",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.95, "latency_ms": 10.0},
        commit_sha="parentsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_opt_1",
        manifest_path=".hiagentresearch/cycles/optimization_strategy/gh_opt_1.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "parentsha",
            "lineage_parent_anchor_step": 2,
            "lineage_anchor_policy": "best_commit",
        },
    )
    registry.record_run(
        run_id="gh_opt_1",
        group_id="optimization_strategy",
        branch="research/optimization-strategy",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.91, "latency_ms": 9.8},
        commit_sha="optloop1sha",
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    topology = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))["lineage_topology"]
    anchor = topology["inherit_anchors"]["hyperparameter_optimization"]
    assert anchor["commit_sha"] == "parentsha"
    assert anchor["parent_trajectory_step"] == 2
    # The anchor commit belongs to model_architecture (the grandparent peak), so the
    # dashboard must attribute the connector there, not to optimization_strategy.
    assert anchor["anchor_source_group"] == "model_architecture"


def test_lineage_parents_chain_uses_collapse_not_hidden_leaf_and_ends_at_l0() -> None:
    """The lineage-DAG parent chain (the structure the frontend walks to the nearest in-scope
    ancestor) lists each group's DIRECT lineage parent (`inherit_anchors.parent_group_id` — the
    area RESULT / collapse), never the hidden adopted leaf (`anchor_source_group`). Every chain
    terminates at the L0 baseline. Merge fold-in sources land in `secondary`. This is the single
    structure that dissolves the select-collapse connector bugs."""
    topology = {
        "groups": {
            "polish": {"mode": "baseline", "role": "leaf"},
            "architecture__a2": {"mode": "inherit", "role": "leaf"},
            "architecture__collapse": {"mode": "inherit", "role": "collapse"},
            "optimization__a1": {"mode": "inherit", "role": "leaf"},
            "optimization__a2": {"mode": "inherit", "role": "leaf"},
            "optimization__collapse": {"mode": "inherit", "role": "collapse"},
        },
        "inherit_anchors": {
            "architecture__a2": {"parent_group_id": "polish", "anchor_source_group": "polish"},
            # SELECT collapse adopted architecture__a2's commit.
            "architecture__collapse": {"parent_group_id": "architecture__a2", "anchor_source_group": "architecture__a2"},
            # The leaf inherits the COLLAPSE, but the adopted commit belongs to the hidden leaf.
            "optimization__a1": {"parent_group_id": "architecture__collapse", "anchor_source_group": "architecture__a2"},
            "optimization__a2": {"parent_group_id": "architecture__collapse", "anchor_source_group": "architecture__a2"},
            "optimization__collapse": {"parent_group_id": "optimization__a1", "anchor_source_group": "optimization__a1"},
        },
        "group_trajectory_winners": {
            "polish": {"trajectory_step": 3, "commit_sha": "polishsha"},
            "architecture__a2": {"trajectory_step": 4, "commit_sha": "archsha"},
            "architecture__collapse": {"trajectory_step": 4, "commit_sha": "archsha"},
            "optimization__a1": {"trajectory_step": 5, "commit_sha": "optsha"},
        },
        "baseline_snapshot": {"ref": "main", "commit_sha": "base0", "metrics": {"accuracy": 0.8}},
        "merge_groups": [
            {"group_id": "optimization__collapse", "participants": [
                {"group_id": "optimization__a1", "commit_sha": "optsha"},
                {"group_id": "optimization__a2", "commit_sha": "opt2sha"},
            ]},
        ],
    }
    lp = _lineage_parents(topology)

    def chain(gid):
        return [h["group_id"] for h in lp[gid]["primary"]]

    # The KEY property: the leaf's nearest hop is the in-scope COLLAPSE, never the hidden leaf.
    assert chain("optimization__a1")[0] == "architecture__collapse"
    assert "architecture__a2" not in chain("optimization__a1")[:1]
    # Chains are nearest-first and terminate at L0 (group_id None, is_baseline).
    assert chain("optimization__a1") == ["architecture__collapse", "architecture__a2", "polish", None]
    assert lp["optimization__a1"]["primary"][-1]["is_baseline"] is True
    assert lp["polish"]["primary"] == [{"group_id": None, "trajectory_step": 0, "commit_sha": "base0", "is_baseline": True}]
    # A hop carries the ancestor's representative (winner) position.
    assert lp["optimization__a1"]["primary"][0]["trajectory_step"] == 4
    # Merge fold-in sources land in secondary (the non-base participants).
    assert [h["group_id"] for h in lp["optimization__collapse"]["secondary"]] == ["optimization__a2"]
    # No cycles / infinite walk even though inherit_anchors form a chain.
    assert chain("architecture__collapse") == ["architecture__a2", "polish", None]


def test_final_merge_base_parent_is_the_terminal_collapse_not_its_hidden_leaf() -> None:
    """A merge whose base is a SELECT-collapse RESULT must chain to that rendered collapse, not the
    collapse's hidden adopted leaf. The recorded parent flattens to the leaf (a SELECT collapse has
    no commit of its own), so without correction the Overview walk would skip the rendered collapse
    node and teleport the merge connector back to an earlier area. Resolving the merge's base to the
    planned SOURCE whose result commit equals the merge's base commit keeps the chain on the
    rendered collapse — while an area collapse (whose base source is a real leaf) is unchanged."""
    topology = {
        "groups": {
            "polish": {"mode": "baseline", "role": "leaf"},
            "hyper__a1": {"mode": "inherit", "role": "leaf"},
            "hyper__collapse": {"mode": "inherit", "role": "collapse"},
            "aug__a1": {"mode": "inherit", "role": "leaf"},
            "aug__collapse": {"mode": "inherit", "role": "collapse"},
            "final_merge": {"mode": "inherit", "role": "final_merge"},
        },
        "inherit_anchors": {
            "hyper__a1": {"parent_group_id": "polish", "anchor_source_group": "polish", "commit_sha": "h1"},
            # SELECT collapse adopts the leaf's commit (h1) — it has no commit of its own.
            "hyper__collapse": {"parent_group_id": "hyper__a1", "anchor_source_group": "hyper__a1", "commit_sha": "h1"},
            "aug__a1": {"parent_group_id": "polish", "anchor_source_group": "polish", "commit_sha": "g1"},
            "aug__collapse": {"parent_group_id": "aug__a1", "anchor_source_group": "aug__a1", "commit_sha": "g1"},
            # final_merge's recorded parent flattens to the hidden leaf (the commit owner).
            "final_merge": {"parent_group_id": "hyper__a1", "anchor_source_group": "hyper__a1", "commit_sha": "h1"},
        },
        "group_trajectory_winners": {
            "polish": {"trajectory_step": 3, "commit_sha": "p"},
            "hyper__a1": {"trajectory_step": 13, "commit_sha": "h1"},
            "hyper__collapse": {"trajectory_step": 13, "commit_sha": "h1"},
            "aug__collapse": {"trajectory_step": 6, "commit_sha": "g1"},
        },
        "baseline_snapshot": {"ref": "main", "commit_sha": "base0"},
        "merge_groups": [
            {"group_id": "final_merge", "planned_sources": ["hyper__collapse", "aug__collapse"],
             "participants": [{"group_id": "hyper__a1", "commit_sha": "h1"},
                              {"group_id": "aug__a1", "commit_sha": "g1"}]},
        ],
    }
    lp = _lineage_parents(topology)
    chain = [h["group_id"] for h in lp["final_merge"]["primary"]]
    # The NEAREST hop is the rendered terminal collapse, NOT the hidden leaf hyper__a1.
    assert chain[0] == "hyper__collapse"
    assert lp["final_merge"]["primary"][0]["trajectory_step"] == 13
    assert chain[-1] is None and lp["final_merge"]["primary"][-1]["is_baseline"] is True
    # The fold-in (secondary) source resolves to the rendered collapse too, not the hidden leaf.
    assert [h["group_id"] for h in lp["final_merge"]["secondary"]] == ["aug__collapse"]
    assert lp["final_merge"]["secondary"][0]["trajectory_step"] == 6


def test_collapse_traces_adopted_source_climb_into_base_for_select_and_merge() -> None:
    """EVERY collapse re-emits its adopted source leaf's climb *up to* the adopted/base commit
    (tagged path_of_leaf) so the Overview line passes through every real commit instead of
    teleporting across the hidden source leaf. This is unconditional — it holds for a SELECT
    (adopts the leaf outright) AND a MERGE (starts from the leaf commit, then runs its own loops),
    because the climb into the base is a structural fact, independent of metric and of whether the
    MERGE later improved past its base. The path stops AT the adopted commit; the leaf's own anchor
    is the lower bound (below it is the previous area, drawn by the walk). The frontend suppresses
    these on the leaf's tab. The auto final_merge (role != collapse) is NOT traced (its child
    collapse already draws that climb — tracing would double-draw)."""
    topology = {
        "groups": {
            "architecture__a2": {"mode": "inherit", "role": "leaf"},
            "architecture__collapse": {"mode": "inherit", "role": "collapse"},
            "optimization__a1": {"mode": "inherit", "role": "leaf"},
            "optimization__collapse": {"mode": "inherit", "role": "collapse"},
            "final_merge": {"mode": "inherit", "role": "final_merge"},
        },
        "inherit_anchors": {
            # SELECT: leaf inherited polish@L3 (its loops L4,L5); collapse adopted the leaf's L5.
            "architecture__a2": {"anchor_source_group": "polish", "parent_trajectory_step": 3, "commit_sha": "polishsha"},
            "architecture__collapse": {"anchor_source_group": "architecture__a2", "parent_trajectory_step": 5, "commit_sha": "a2_l5"},
            # MERGE that IMPROVED past its base: leaf inherited arch@L5 (its loops L6,L7); the merge
            # adopted the leaf's L7 as base, then ran its own loops. The leaf's L6 climb into the
            # base MUST be traced (this is the "missing L6" the gated version dropped).
            "optimization__a1": {"anchor_source_group": "architecture__collapse", "parent_trajectory_step": 5, "commit_sha": "a2_l5"},
            "optimization__collapse": {"anchor_source_group": "optimization__a1", "parent_trajectory_step": 7, "commit_sha": "opt_l7"},
            # final_merge resolves (flattened) to a leaf commit — but role != collapse, so untraced.
            "final_merge": {"anchor_source_group": "optimization__a1", "parent_trajectory_step": 7, "commit_sha": "opt_l7"},
        },
        "merge_groups": [
            {"group_id": "architecture__collapse", "is_select": True},
            {"group_id": "optimization__collapse", "is_select": False},
            {"group_id": "final_merge", "is_select": False},
        ],
    }
    rows = [
        {"group_id": "architecture__a2", "metric_name": "accuracy", "trajectory_x": 4, "metric_value": 0.90, "commit_sha": "a2_l4"},
        {"group_id": "architecture__a2", "metric_name": "accuracy", "trajectory_x": 5, "metric_value": 0.93, "commit_sha": "a2_l5"},
        {"group_id": "optimization__a1", "metric_name": "accuracy", "trajectory_x": 6, "metric_value": 0.948, "commit_sha": "opt_l6"},
        {"group_id": "optimization__a1", "metric_name": "accuracy", "trajectory_x": 7, "metric_value": 0.958, "commit_sha": "opt_l7"},
    ]
    out = _collapse_result_points(rows, topology, ["accuracy"])
    by_run = {r["run_id"]: r for r in out}

    # SELECT collapse: base node at the adopted L5 + a path node at the intermediate L4.
    base = by_run["collapsebase:architecture__collapse"]
    assert base["trajectory_x"] == 5 and base["commit_sha"] == "a2_l5" and base["metric_value"] == 0.93
    path = by_run["collapsepath:architecture__collapse:4"]
    assert path["trajectory_x"] == 4 and path["metric_value"] == 0.90 and path["path_of_leaf"] == "architecture__a2"
    # No path node beyond the adopted step, and none below the leaf's own anchor (L3).
    assert "collapsepath:architecture__collapse:5" not in by_run
    assert "collapsepath:architecture__collapse:3" not in by_run

    # MERGE collapse (improved): base node at the adopted L7 + the leaf's L6 climb into it.
    opt_base = by_run["collapsebase:optimization__collapse"]
    assert opt_base["trajectory_x"] == 7 and opt_base["commit_sha"] == "opt_l7"
    opt_path = by_run["collapsepath:optimization__collapse:6"]
    assert opt_path["trajectory_x"] == 6 and opt_path["metric_value"] == 0.948
    assert opt_path["path_of_leaf"] == "optimization__a1"

    # final_merge (role != collapse): neither base nor path is emitted here.
    assert not any(r["group_id"] == "final_merge" for r in out)


def test_collapse_traces_baseline_root_source_climb_into_base() -> None:
    """A first-wave baseline leaf implicitly starts at L0, so an adopted L3 commit
    must re-emit L1 and L2 on the collapse series for Overview."""
    topology = {
        "groups": {
            "prompting__a1": {"mode": "baseline", "role": "leaf"},
            "prompting__collapse": {"mode": "inherit", "role": "collapse"},
        },
        "inherit_anchors": {
            "prompting__collapse": {
                "anchor_source_group": "prompting__a1",
                "parent_trajectory_step": 3,
                "commit_sha": "a1_l3",
            },
        },
    }
    rows = [
        {"group_id": "prompting__a1", "metric_name": "accuracy", "trajectory_x": 0, "metric_value": 0.80, "commit_sha": "base", "is_baseline_anchor": True},
        {"group_id": "prompting__a1", "metric_name": "accuracy", "trajectory_x": 1, "metric_value": 0.84, "commit_sha": "a1_l1"},
        {"group_id": "prompting__a1", "metric_name": "accuracy", "trajectory_x": 2, "metric_value": 0.82, "commit_sha": "a1_l2"},
        {"group_id": "prompting__a1", "metric_name": "accuracy", "trajectory_x": 3, "metric_value": 0.88, "commit_sha": "a1_l3"},
        {"group_id": "prompting__a1", "metric_name": "accuracy", "trajectory_x": 4, "metric_value": 0.81, "commit_sha": "a1_l4"},
    ]
    out = _collapse_result_points(rows, topology, ["accuracy"])
    by_run = {r["run_id"]: r for r in out}

    base = by_run["collapsebase:prompting__collapse"]
    assert base["trajectory_x"] == 3 and base["commit_sha"] == "a1_l3" and base["metric_value"] == 0.88
    assert by_run["collapsepath:prompting__collapse:1"]["metric_value"] == 0.84
    assert by_run["collapsepath:prompting__collapse:2"]["metric_value"] == 0.82
    assert "collapsepath:prompting__collapse:0" not in by_run
    assert "collapsepath:prompting__collapse:3" not in by_run
    assert "collapsepath:prompting__collapse:4" not in by_run


def test_select_collapse_flagged_distinctly_from_merge_collapse() -> None:
    """A select collapse (combine:false → loops==0) is flagged is_select so the dashboard renders
    it as 'adopt the strongest competing leaf', not a fold-in merge chain. A real merge collapse
    (combine:true) and the auto final merge are not flagged."""
    config = load_config(Path("configs/fanout.yaml"))
    topology = _lineage_topology(config, registry=None)
    flags = {mg["group_id"]: mg["is_select"] for mg in topology["merge_groups"]}
    assert flags["architecture__collapse"] is True   # combine: false → select
    assert flags["optimization__collapse"] is False   # combine: true → real merge
    assert flags["augmentation__collapse"] is False
    assert flags["final_merge"] is False


def test_dashboard_lineage_winners_include_polish_last_commit_and_row_flags(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = Registry(state_dir)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.80, "latency_ms": 50.0, "duration_sec": 1.0})
    registry.record_run(
        run_id="gh_data_1",
        group_id="data_augmentation",
        branch="research/data-augmentation",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.85, "latency_ms": 9.8},
        commit_sha="datasha",
    )
    registry.record_cycle_manifest(
        run_id="gh_data_1",
        manifest_path=".hiagentresearch/cycles/data_augmentation/gh_data_1.json",
        manifest={"group_id": "data_augmentation", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_model_1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.90, "latency_ms": 9.9},
        commit_sha="modelsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_model_1",
        manifest_path=".hiagentresearch/cycles/model_architecture/gh_model_1.json",
        manifest={"group_id": "model_architecture", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_opt_1",
        group_id="optimization_strategy",
        branch="research/optimization-strategy",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.91, "latency_ms": 9.7},
        commit_sha="optsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_opt_1",
        manifest_path=".hiagentresearch/cycles/optimization_strategy/gh_opt_1.json",
        manifest={
            "group_id": "optimization_strategy",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "model_architecture",
            "lineage_anchor_sha": "modelsha",
            "lineage_anchor_policy": "best_commit",
            "lineage_parent_anchor_step": 1,
            "lineage_anchor_source_group": "model_architecture",
        },
    )
    registry.record_run(
        run_id="gh_hyper_1",
        group_id="hyperparameter_optimization",
        branch="research/hyperparameter-optimization",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.92, "latency_ms": 9.6},
        commit_sha="hypersha",
    )
    registry.record_cycle_manifest(
        run_id="gh_hyper_1",
        manifest_path=".hiagentresearch/cycles/hyperparameter_optimization/gh_hyper_1.json",
        manifest={
            "group_id": "hyperparameter_optimization",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "optimization_strategy",
            "lineage_anchor_sha": "optsha",
            "lineage_anchor_policy": "best_commit",
            "lineage_parent_anchor_step": 2,
            "lineage_anchor_source_group": "optimization_strategy",
        },
    )
    registry.record_run(
        run_id="gh_polish_1",
        group_id="polish_code",
        branch="research/polish-code",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.89, "latency_ms": 9.5},
        commit_sha="polishold",
    )
    registry.record_cycle_manifest(
        run_id="gh_polish_1",
        manifest_path=".hiagentresearch/cycles/polish_code/gh_polish_1.json",
        manifest={
            "group_id": "polish_code",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "hyperparameter_optimization",
            "lineage_anchor_sha": "hypersha",
            "lineage_anchor_policy": "last_commit",
            "lineage_parent_anchor_step": 3,
            "lineage_anchor_source_group": "hyperparameter_optimization",
        },
    )
    registry.record_run(
        run_id="gh_polish_2",
        group_id="polish_code",
        branch="research/polish-code",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.88, "latency_ms": 9.4},
        commit_sha="polishnew",
    )
    registry.record_cycle_manifest(
        run_id="gh_polish_2",
        manifest_path=".hiagentresearch/cycles/polish_code/gh_polish_2.json",
        manifest={
            "group_id": "polish_code",
            "loop_index": 2,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "hyperparameter_optimization",
            "lineage_anchor_sha": "hypersha",
            "lineage_anchor_policy": "last_commit",
            "lineage_parent_anchor_step": 3,
            "lineage_anchor_source_group": "hyperparameter_optimization",
        },
    )

    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    topology = snapshot["lineage_topology"]
    winners = topology["lineage_winners"]
    assert winners["model_architecture"]["winner_commit_sha"] == "polishnew"
    assert winners["model_architecture"]["leaf_group_id"] == "polish_code"
    assert winners["data_augmentation"]["leaf_group_id"] == "data_augmentation"
    row = next(row for row in snapshot["metrics"] if row.get("run_id") == "gh_polish_2")
    assert row["is_group_policy_winner"] is True
    anchor_row = next(row for row in snapshot["metrics"] if row.get("run_id") == "gh_hyper_1")
    assert anchor_row["is_inherit_anchor"] is True
    assert "polish_code" in anchor_row["inherit_anchor_for_groups"]


def test_lineage_winner_after_wave_one_uses_model_not_unrun_inherit_children(tmp_path) -> None:
    """Wave-1-only registry: model chain winner must not jump to baseline-only inherit groups."""
    state_dir = tmp_path / "state"
    registry = Registry(state_dir)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.879, "latency_ms": 50.0, "duration_sec": 1.0})
    for run_id, acc, sha, loop in (
        ("gh_m1", 0.861, "sha1", 1),
        ("gh_m2", 0.923, "sha2", 2),
        ("gh_m3", 0.815, "sha3", 3),
    ):
        registry.record_run(
            run_id=run_id,
            group_id="model_architecture",
            branch="research/model-architecture",
            status="finished",
            failure_class="none",
            metrics={"accuracy": acc, "latency_ms": 10.0},
            commit_sha=sha,
        )
        registry.record_cycle_manifest(
            run_id=run_id,
            manifest_path=f".hiagentresearch/cycles/model_architecture/{run_id}.json",
            manifest={"group_id": "model_architecture", "loop_index": loop},
        )
    registry.record_run(
        run_id="gh_d1",
        group_id="data_augmentation",
        branch="research/data-augmentation",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.939, "latency_ms": 10.0},
        commit_sha="dsha1",
    )
    registry.record_cycle_manifest(
        run_id="gh_d1",
        manifest_path=".hiagentresearch/cycles/data_augmentation/gh_d1.json",
        manifest={"group_id": "data_augmentation", "loop_index": 1},
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    winners = snapshot["lineage_topology"]["lineage_winners"]
    assert winners["model_architecture"]["leaf_group_id"] == "model_architecture"
    assert winners["model_architecture"]["winner_commit_sha"] == "sha2"
    # Stars are per-metric, per-group now; scope to model_architecture's accuracy winner.
    model_star = next(
        row
        for row in snapshot["metrics"]
        if row.get("is_group_policy_winner")
        and row.get("group_id") == "model_architecture"
        and row.get("metric_name") == "accuracy"
    )
    assert model_star["run_id"] == "gh_m2"
    assert "optimization_strategy" not in snapshot["lineage_topology"]["group_trajectory_winners"]


def test_baseline_only_state_stars_l0_as_initial_top_commit(tmp_path) -> None:
    """Before any loop runs, a baseline-mode group's frozen L0 is the lineage
    winner (starred), so the bootstrap eval is the initial top commit."""
    state_dir = tmp_path / "state"
    registry = Registry(state_dir)
    registry.init()
    registry.record_baseline_snapshot(
        ref="main", metrics={"accuracy": 0.879, "latency_ms": 50.0, "duration_sec": 1.0}
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    winners = snapshot["lineage_topology"]["lineage_winners"]
    # The baseline-mode root owns the lineage star, anchored at the frozen L0.
    assert winners["model_architecture"]["leaf_group_id"] == "model_architecture"
    assert winners["model_architecture"]["is_baseline_anchor"] is True
    # Unrun inherit children never become winners off the back of the baseline.
    assert "optimization_strategy" not in snapshot["lineage_topology"]["group_trajectory_winners"]
    baseline_star = next(
        row
        for row in snapshot["metrics"]
        if row.get("is_group_policy_winner")
        and row.get("group_id") == "model_architecture"
        and row.get("is_baseline_anchor")
    )
    assert baseline_star["trajectory_x"] == 0
    # Backend is the single source of truth for the chart symbol.
    assert baseline_star["symbol"] == "star"
    for row in snapshot["metrics"]:
        # ★ = this lineage/area's best commit for the metric (per-group winner, matches the panels);
        # ◆ = a commit a downstream group inherits from (an anchor).
        expected = "star" if row.get("is_group_policy_winner") else "diamond" if row.get("is_inherit_anchor") else "circle"
        assert row.get("symbol") == expected


def test_unrun_select_collapse_claims_no_top_commit_at_baseline(tmp_path) -> None:
    """A SELECT collapse must NOT own a ★ top commit before any of its leaves run. Otherwise its
    bootstrap resolves up the unrun inherit chain to the frozen L0 baseline (step 0) and the
    dashboard renders a spurious 'top commit @ baseline' for an area that hasn't started."""
    state_dir = tmp_path / "state"
    registry = Registry(state_dir)
    registry.init()
    registry.record_baseline_snapshot(
        ref="main", metrics={"accuracy": 0.80, "latency_ms": 50.0, "duration_sec": 1.0}
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(
        state_dir=state_dir, output_dir=output_dir, config=load_config(Path("configs/fanout.yaml"))
    )
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    winners = snapshot["lineage_topology"]["group_trajectory_winners"]
    # architecture__collapse is a SELECT collapse; with no leaf runs it has no resolved result.
    assert "architecture__collapse" not in winners


def test_lineage_winner_uses_effective_leaf_when_configured_leaf_missing(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = Registry(state_dir)
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.80, "latency_ms": 50.0, "duration_sec": 1.0})
    registry.record_run(
        run_id="gh_model_1",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.90, "latency_ms": 9.9},
        commit_sha="modelsha",
    )
    registry.record_cycle_manifest(
        run_id="gh_model_1",
        manifest_path=".hiagentresearch/cycles/model_architecture/gh_model_1.json",
        manifest={"group_id": "model_architecture", "loop_index": 1},
    )
    registry.record_run(
        run_id="gh_hyper_1",
        group_id="hyperparameter_optimization",
        branch="research/hyperparameter-optimization",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.94, "latency_ms": 9.5},
        commit_sha="hypersha",
    )
    registry.record_cycle_manifest(
        run_id="gh_hyper_1",
        manifest_path=".hiagentresearch/cycles/hyperparameter_optimization/gh_hyper_1.json",
        manifest={
            "group_id": "hyperparameter_optimization",
            "loop_index": 1,
            "lineage_mode": "inherit",
            "lineage_parent_group_id": "optimization_strategy",
            "lineage_anchor_sha": "optsha",
            "lineage_anchor_policy": "best_commit",
        },
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    # The flat-config Lineage panel reads this map: the model chain's effective winner leaf is the
    # deepest run group (hyperparameter_optimization), even though the configured leaf (polish_code)
    # never ran. (Per-chain row stars were retired; per-group ★ is covered by the symbol tests.)
    winners = snapshot["lineage_topology"]["lineage_winners"]
    assert winners["model_architecture"]["leaf_group_id"] == "hyperparameter_optimization"
    assert winners["model_architecture"]["configured_leaf_group_id"] == "polish_code"
    assert winners["model_architecture"]["winner_commit_sha"] == "hypersha"


def test_dashboard_build_from_artifacts(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts" / "hiagentresearch-123"
    artifact_dir.mkdir(parents=True)
    _write_artifacts(artifact_dir)

    output_dir = tmp_path / "site"
    result = build_from_artifacts(artifact_root=tmp_path / "artifacts", output_dir=output_dir, config=load_config())

    assert result.database_path.exists()
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert snapshot["runs"][0]["run_id"] == "gh_123"
    assert snapshot["cycles"][0]["goal_id"] == "h1"
    assert snapshot["lineage_topology"]["baseline_snapshot"]["metrics"]["accuracy"] == 0.81
    assert summary["metric_targets"]


def test_dashboard_build_from_artifacts_synthesizes_missing_manifest(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts" / "hiagentresearch-456"
    artifact_dir.mkdir(parents=True)
    _write_artifacts(artifact_dir)
    (artifact_dir / "cycle_manifest.json").unlink()

    output_dir = tmp_path / "site"
    build_from_artifacts(artifact_root=tmp_path / "artifacts", output_dir=output_dir, config=load_config())

    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    cycle = snapshot["cycles"][0]
    assert cycle["goal_id"] == "model_architecture-direct-eval"
    assert "missing" in (cycle["goal"] or "").lower()


def test_build_from_artifacts_records_baseline_node_as_l0(tmp_path) -> None:
    """A collected baseline (node_kind=baseline) eval becomes the frozen L0 — shown
    as the baseline snapshot, not a phantom loop run under the anchor group."""
    artifact_dir = tmp_path / "artifacts" / "hiagentresearch-baseline"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "metrics.json").write_text('{"accuracy": 0.81, "latency_ms": 12.0}', encoding="utf-8")
    (artifact_dir / "failure_class.json").write_text('{"failure_class": "none", "exit_code": 0}', encoding="utf-8")
    (artifact_dir / "research_outcome.json").write_text(
        '{"research_outcome": "baseline", "next_action": "continue", "reason": "frozen"}', encoding="utf-8"
    )
    (artifact_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "run_id": "gh_baseline",
                "group_id": "model_architecture",
                "branch": "main",
                "baseline_ref": "main",
                "commit_sha": "mainsha",
                "node_kind": "baseline",
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "site"
    build_from_artifacts(artifact_root=tmp_path / "artifacts", output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    assert snapshot["lineage_topology"]["baseline_snapshot"]["metrics"]["accuracy"] == 0.81
    # The baseline is not a phantom run under the anchor group.
    assert all(run["branch"] != "main" for run in snapshot["runs"])
    assert "gh_baseline" not in {run["run_id"] for run in snapshot["runs"]}


def test_dashboard_excludes_runs_before_orchestration_session(tmp_path) -> None:
    state_dir = tmp_path / "state"
    registry = _seed_registry(state_dir)
    conn = sqlite3.connect(registry.db_path)
    try:
        conn.execute(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", "run_abc"),
        )
        conn.commit()
    finally:
        conn.close()
    registry.record_baseline_snapshot(
        ref="main",
        metrics={"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
    )
    registry.record_run(
        run_id="run_current",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.92, "latency_ms": 11.0},
        commit_sha="run_current_commit",
        correlation_id="run_current",
    )
    output_dir = tmp_path / "dashboard"
    build_from_registry(state_dir=state_dir, output_dir=output_dir, config=load_config())
    snapshot = json.loads((output_dir / "dashboard.json").read_text(encoding="utf-8"))
    run_ids = {row["run_id"] for row in snapshot["runs"]}
    assert "run_abc" not in run_ids
    assert "run_current" in run_ids
    assert snapshot["orchestration_session"]["started_at"]


def test_dashboard_cli_build(tmp_path, capsys) -> None:
    state_dir = tmp_path / "state"
    _seed_registry(state_dir)
    runtime_root = Path(__file__).resolve().parents[1]

    assert (
        main(
            [
                "--config",
                str(runtime_root / "configs" / "standard.yaml"),
                "build",
                "--state-dir",
                str(state_dir),
                "--output-dir",
                str(tmp_path / "site"),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["database_path"].endswith("dashboard.db")


def _seed_registry(
    state_dir,
    *,
    with_baseline: bool = False,
    baseline_metrics: dict | None = None,
):
    registry = Registry(state_dir)
    registry.init()
    if with_baseline:
        registry.record_baseline_snapshot(
            ref="main",
            metrics=baseline_metrics
            or {"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
        )
    registry.record_run(
        run_id="run_abc",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="finished",
        failure_class="none",
        metrics={"accuracy": 0.99, "latency_ms": 12.1},
        commit_sha="run_abc_commit",
        correlation_id="run_abc",
    )
    registry.record_research_outcome(
        run_id="run_abc",
        outcome={
            "research_outcome": "met_targets",
            "next_action": "continue",
            "reason": "ok",
        },
    )
    registry.record_cycle_manifest(
        run_id="run_abc",
        manifest_path=".hiagentresearch/cycles/model_architecture/run_abc.json",
        manifest=_manifest(),
    )
    return registry


def _write_artifacts(artifact_dir) -> None:
    (artifact_dir / "metrics.json").write_text('{"accuracy": 0.99, "latency_ms": 12.1}', encoding="utf-8")
    (artifact_dir / "failure_class.json").write_text('{"failure_class": "none", "exit_code": 0}', encoding="utf-8")
    (artifact_dir / "research_outcome.json").write_text(
        '{"research_outcome": "met_targets", "next_action": "continue", "reason": "ok"}',
        encoding="utf-8",
    )
    (artifact_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "run_id": "gh_123",
                "group_id": "model_architecture",
                "branch": "research/model-architecture",
                "commit_sha": "abc",
                "workflow_run_id": "123",
                "correlation_id": "run_abc",
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "stdout.txt").write_text("{}", encoding="utf-8")
    (artifact_dir / "stderr.txt").write_text("", encoding="utf-8")
    (artifact_dir / "cycle_manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")


def _manifest() -> dict:
    return {
        "group_id": "model_architecture",
        "branch": "research/model-architecture",
        "loop_index": 1,
        "goal_id": "h1",
        "goal": "Try a dashboard-ready model change.",
        "target_files": ["mnist/src/model.py"],
        "planned_code_changes": ["Edit model.py"],
        "lineage_baseline_snapshot": {
            "ref": "main",
            "metrics": {"accuracy": 0.81, "latency_ms": 50.0, "duration_sec": 1.0},
        },
    }


def _merge_topology_config():
    """A two-lineage config plus an auto-resolved merge group (used by the merge tests)."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, OrchestrationConfig, ResearchGroupConfig

    return HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "Explore.", "exploit": "Exploit."},
        orchestration=OrchestrationConfig(
            execution_waves=[["model_architecture", "data_augmentation"], ["merge_best"]],
        ),
        research_groups=[
            ResearchGroupConfig(id="model_architecture", branch="research/model-architecture",
                                objective="t", policy_mode="explore"),
            ResearchGroupConfig(id="data_augmentation", branch="research/data-augmentation",
                                objective="t", policy_mode="explore"),
            ResearchGroupConfig(id="merge_best", branch="research/merge-best", policy_mode="exploit",
                                task_kind="merge", lineage=LineageConfig(mode="inherit", anchor_metric="accuracy")),
        ],
    )


def test_merge_group_resolves_to_its_own_row_not_a_chain(tmp_path) -> None:
    """A merge converges every lineage, so it is NOT a node in any chain: it gets its own
    merge_groups entry. Once its sources have winners it resolves to the strongest as base
    and carries the rest (best->worst) as the integration order."""
    from hiagentresearch.src.dashboard.build import _lineage_topology

    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_model", group_id="model_architecture", branch="research/model-architecture",
        status="completed", failure_class="none", metrics={"accuracy": 0.95}, commit_sha="modelsha",
    )
    registry.record_run(
        run_id="gh_data", group_id="data_augmentation", branch="research/data-augmentation",
        status="completed", failure_class="none", metrics={"accuracy": 0.90}, commit_sha="datasha",
    )
    topology = _lineage_topology(_merge_topology_config(), registry=registry)
    # The merge never appears in a chain.
    assert all("merge_best" not in chain for chain in topology["chains"])
    merge = next(m for m in topology["merge_groups"] if m["group_id"] == "merge_best")
    parts = merge["participants"]
    # Base first (strongest accuracy), then the integration source; both are real winners.
    assert parts[0]["group_id"] == "model_architecture"
    assert parts[0]["commit_sha"] == "modelsha"
    assert parts[0]["known"] is True
    assert [p["group_id"] for p in parts[1:]] == ["data_augmentation"]
    assert all(p["known"] for p in parts)
    meta = topology["groups"]["merge_best"]
    assert meta["draw_from"] == ["data_augmentation"]
    assert meta["intent_label"] == "Merge goal"
    assert meta["preserve_metrics"] is True


def test_merge_topology_prefers_persisted_merge_plan(tmp_path) -> None:
    from hiagentresearch.src.dashboard.build import _lineage_topology

    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_run(
        run_id="gh_model",
        group_id="model_architecture",
        branch="research/model-architecture",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.95},
        commit_sha="modelsha",
    )
    registry.record_run(
        run_id="gh_data",
        group_id="data_augmentation",
        branch="research/data-augmentation",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.90},
        commit_sha="datasha",
    )
    registry.record_run(
        run_id="gh_merge",
        group_id="merge_best",
        branch="research/merge-best",
        status="completed",
        failure_class="none",
        metrics={"accuracy": 0.96},
        commit_sha="mergesha",
    )
    merge_plan = {
        "base": {"source_group_id": "data_augmentation", "group_id": "data_augmentation", "commit_sha": "datasha"},
        "fold_ins": [
            {"source_group_id": "model_architecture", "group_id": "model_architecture", "commit_sha": "modelsha"}
        ],
        "no_ops": [],
        "ranking_metric": "accuracy",
        "policy": "best_commit",
        "resolved_at": "2026-06-11T00:00:00+00:00",
    }
    registry.record_cycle_manifest(
        run_id="gh_merge",
        manifest_path=".hiagentresearch/cycles/merge_best/gh_merge.json",
        manifest={"group_id": "merge_best", "loop_index": 1, "merge_plan": merge_plan},
    )

    snapshot = registry.dashboard_snapshot()
    topology = _lineage_topology(_merge_topology_config(), registry=registry, cycles=snapshot["cycles"])
    merge = next(m for m in topology["merge_groups"] if m["group_id"] == "merge_best")

    assert [p["group_id"] for p in merge["participants"]] == [
        "data_augmentation",
        "model_architecture",
    ]
    assert merge["merge_plan"] == merge_plan
    assert topology["groups"]["merge_best"]["draw_from"] == ["model_architecture"]


def test_merge_with_three_lineages_has_two_integration_steps(tmp_path) -> None:
    """N lineages collapse through N-1 sequential integration steps: a 3-lineage merge has
    a base (strongest) + 2 ranked sources, which the UI renders as 2 merge step nodes."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, OrchestrationConfig, ResearchGroupConfig
    from hiagentresearch.src.dashboard.build import _lineage_topology

    registry = Registry(tmp_path / "state")
    registry.init()
    for gid, branch, acc, sha in (
        ("line_a", "research/line-a", 0.91, "shaa"),
        ("line_b", "research/line-b", 0.95, "shab"),
        ("line_c", "research/line-c", 0.88, "shac"),
    ):
        registry.record_run(run_id=f"gh_{gid}", group_id=gid, branch=branch, status="completed",
                            failure_class="none", metrics={"accuracy": acc}, commit_sha=sha)
    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "Explore.", "exploit": "Exploit."},
        orchestration=OrchestrationConfig(execution_waves=[["line_a", "line_b", "line_c"], ["merge_best"]]),
        research_groups=[
            ResearchGroupConfig(id="line_a", branch="research/line-a", objective="t", policy_mode="explore"),
            ResearchGroupConfig(id="line_b", branch="research/line-b", objective="t", policy_mode="explore"),
            ResearchGroupConfig(id="line_c", branch="research/line-c", objective="t", policy_mode="explore"),
            ResearchGroupConfig(id="merge_best", branch="research/merge-best", policy_mode="exploit",
                                task_kind="merge", lineage=LineageConfig(mode="inherit", anchor_metric="accuracy")),
        ],
    )
    merge = next(m for m in _lineage_topology(config, registry=registry)["merge_groups"] if m["group_id"] == "merge_best")
    parts = merge["participants"]
    assert all(p["known"] for p in parts)
    assert parts[0]["group_id"] == "line_b"  # strongest accuracy (0.95) is the base
    # Two integration steps (N-1 for 3 lineages), best→worst => line_a (0.91) then line_c (0.88).
    assert [p["group_id"] for p in parts[1:]] == ["line_a", "line_c"]


def test_planned_merge_shown_before_any_source_runs(tmp_path) -> None:
    """A configured-but-not-yet-resolvable merge still appears (greyed in the UI): the
    lineages it will combine are known from config, so planned_sources is populated."""
    from hiagentresearch.src.dashboard.build import _lineage_topology

    registry = Registry(tmp_path / "state")
    registry.init()
    topology = _lineage_topology(_merge_topology_config(), registry=registry)
    merge = next(m for m in topology["merge_groups"] if m["group_id"] == "merge_best")
    # Nothing resolved yet: no participants, but the lineages it WILL combine are known
    # from config (the UI renders these as greyed placeholders).
    assert merge["participants"] == []
    assert merge["planned_sources"] == ["model_architecture", "data_augmentation"]


def test_merge_participants_pending_until_lineages_produce_real_runs(tmp_path) -> None:
    """With a baseline but no lineage runs yet, a merge participant resolves to the frozen
    baseline — which is NOT a real result. Such participants are flagged known=False so the
    UI shows a placeholder instead of pretending the baseline is a lineage win."""
    from hiagentresearch.src.dashboard.build import _lineage_topology

    registry = Registry(tmp_path / "state")
    registry.init()
    registry.record_baseline_snapshot(ref="main", metrics={"accuracy": 0.9}, commit_sha="base000")
    merge = next(
        m for m in _lineage_topology(_merge_topology_config(), registry=registry)["merge_groups"]
        if m["group_id"] == "merge_best"
    )
    # Participants exist (each lineage's anchor falls back to baseline) but none is "known"
    # yet, so the base is still TBD in the UI.
    assert merge["participants"]
    assert all(p["known"] is False for p in merge["participants"])


def test_no_merge_groups_when_none_configured(tmp_path) -> None:
    """A config with no merge group => nothing to render in the Merge section."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, ResearchGroupConfig
    from hiagentresearch.src.dashboard.build import _lineage_topology

    config = HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "Explore."},
        research_groups=[
            ResearchGroupConfig(id="model_architecture", branch="research/model-architecture",
                                objective="t", policy_mode="explore"),
        ],
    )
    registry = Registry(tmp_path / "state")
    registry.init()
    topology = _lineage_topology(config, registry=registry)
    assert topology["merge_groups"] == []


def test_dashboard_defaults_to_te_bundle(tmp_path, monkeypatch) -> None:
    """The dashboard ships the 'te' look by default: brand mark, collapsibles, theme overlay."""
    monkeypatch.delenv("HIAGENTRESEARCH_DASHBOARD_THEME", raising=False)
    out = tmp_path / "te"
    out.mkdir()
    dashboard_build._copy_static_assets(out)
    index = (out / "index.html").read_text()
    assert "brand-mark" in index
    assert '<details class="collapsible"' in index
    assert (out / "theme-te.css").exists()


def test_dashboard_classic_bundle_is_opt_in_via_env(tmp_path, monkeypatch) -> None:
    """The original look is retained for comparison only, behind an env var (not config)."""
    monkeypatch.setenv("HIAGENTRESEARCH_DASHBOARD_THEME", "classic")
    out = tmp_path / "classic"
    out.mkdir()
    dashboard_build._copy_static_assets(out)
    index = (out / "index.html").read_text()
    assert "brand-mark" not in index
    assert "<details" not in index
    assert not (out / "theme-te.css").exists()


def test_area_lineage_branching_yields_full_root_to_leaf_chains() -> None:
    """A shared foundation that several areas inherit from renders as full inheritance paths
    (root -> ... -> leaf), not disconnected per-area singletons."""
    from hiagentresearch.src.core.config import HiAgentResearchConfig, LineageConfig, ResearchGroupConfig

    cfg = HiAgentResearchConfig(
        project_id="d", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "x", "exploit": "y"},
        research_groups=[
            ResearchGroupConfig(id="base", objective="b", policy_mode="exploit",
                                task_kind="engineering", lineage=LineageConfig(mode="baseline")),
            ResearchGroupConfig(id="arch", objective="a", policy_mode="explore",
                                lineage=LineageConfig(mode="inherit", inherit_from="base"),
                                combine=False, approaches=["deepen", "widen"]),
            ResearchGroupConfig(id="opt", objective="o", policy_mode="exploit",
                                lineage=LineageConfig(mode="inherit", inherit_from="arch"),
                                approaches=["cosine", "wd"]),
            ResearchGroupConfig(id="aug", objective="g", policy_mode="explore",
                                lineage=LineageConfig(mode="inherit", inherit_from="base"),
                                approaches=["affine", "cutout"]),
        ],
    )
    al = _area_lineage(cfg)
    # base branches into arch (-> opt) and aug -> two full root-to-leaf paths, not 4 singletons.
    assert al["chains"] == [["base", "arch", "opt"], ["base", "aug"]]
    assert al["areas"]["opt"]["ancestors"] == ["base", "arch"]
