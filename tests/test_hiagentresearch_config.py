from pathlib import Path

import pytest

from hiagentresearch.src.core.config import AgentContractConfig, EvaluationConfig, HiAgentResearchConfig, LineageConfig, MetricExpectation, ResearchGroupConfig, load_config, resolve_group_id_for_branch
from hiagentresearch.src.core.guidance import DEFAULT_GUIDANCE_FILES


def test_lineage_config_rejects_legacy_anchor_policy() -> None:
    """The overloaded anchor_policy field was split into inherit_policy and
    top_commit_policy; an old config that still uses it must fail fast."""
    with pytest.raises(Exception, match="anchor_policy"):
        LineageConfig(mode="inherit", inherit_from="parent", anchor_policy="last_commit")


def test_lineage_policies_default_to_best_commit() -> None:
    lineage = LineageConfig(mode="inherit", inherit_from="parent")
    assert lineage.inherit_policy == "best_commit"
    assert lineage.top_commit_policy == "best_commit"


def test_agent_settings_are_config_driven() -> None:
    """Agent model + reasoning effort come from config (strongest thinking for all
    groups), not a hardcoded CLI default."""
    config = load_config()
    assert config.agent.model == "composer-2.5"
    assert config.agent.thinking == "high"
    # Sensible defaults exist for the cursor execution knobs.
    assert config.agent.startup_attempts >= 1
    assert config.agent.unary_timeout_sec > 0


def test_anchor_metric_defaults_to_primary_configured_metric() -> None:
    """The lineage anchor metric is config-driven: groups that don't set it inherit
    the project's primary metric (first dashboard metric), so a non-accuracy use case
    works without touching code."""
    config = HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"f1_score": {"min": 0.9}, "throughput": {"min": 100.0}}},
        policy_modes={"explore": "Explore.", "exploit": "Exploit."},
        dashboard={"enabled": True, "metrics": ["f1_score", "throughput"]},
        research_groups=[
            ResearchGroupConfig(id="model_architecture", branch="research/model-architecture",
                                objective="t", policy_mode="explore"),
            ResearchGroupConfig(id="polish", branch="research/polish", objective="t", policy_mode="exploit",
                                lineage=LineageConfig(mode="baseline", anchor_metric="throughput")),
        ],
    )
    by_id = {g.id: g for g in config.research_groups}
    # Unset -> resolves to the primary configured metric (not hardcoded "accuracy").
    assert by_id["model_architecture"].lineage.anchor_metric == "f1_score"
    # Explicit value is preserved.
    assert by_id["polish"].lineage.anchor_metric == "throughput"


def test_metric_direction_is_derived_from_target() -> None:
    """Optimization direction is generic: a target with only `max` means lower-is-better
    (minimize); `min`, a min+max range, or no target means higher-is-better (maximize).
    No metric names are hardcoded."""
    evaluation = EvaluationConfig(
        entrypoint=".hiagentresearch/eval/run.py",
        command_template="true",
        targets={
            "accuracy": MetricExpectation(min=0.9),       # higher better
            "latency_ms": MetricExpectation(max=13.0),    # lower better
            "loss": MetricExpectation(max=0.5),           # arbitrary lower-better metric
            "calibrated": MetricExpectation(min=0.1, max=0.9),  # range -> maximize default
        },
    )
    assert evaluation.metric_minimizes("latency_ms") is True
    assert evaluation.metric_minimizes("loss") is True
    assert evaluation.metric_minimizes("accuracy") is False
    assert evaluation.metric_minimizes("calibrated") is False
    assert evaluation.metric_minimizes("unconfigured") is False


def test_load_root_config() -> None:
    config = load_config(Path("configs/standard.yaml"))

    assert config.project_id == "mnist"
    assert config.workdir == "mnist"
    assert config.evaluation.entrypoint == ".hiagentresearch/eval/run_phase1_eval.py"
    assert config.all_reference_paths() == [
        ".hiagentresearch/eval/",
        ".hiagentresearch/eval/run_phase1_eval.py",
    ]
    assert "reference_paths" not in HiAgentResearchConfig.model_fields
    assert "mnist/data/" in config.generated_paths_resolved()
    assert "mnist/src/checkpoints/" in config.generated_paths_resolved()
    assert "mnist/data" in config.commit_excluded_paths()
    assert "mnist/src/checkpoints" in config.commit_excluded_paths()
    assert "artifact_contract" not in HiAgentResearchConfig.model_fields
    # enabled is an operational toggle (CI publish on/off); don't pin its value.
    assert isinstance(config.dashboard.enabled, bool)
    assert config.dashboard.metrics == ["accuracy", "latency_ms"]
    command = config.format_eval_command(config.group_by_id("model_architecture"))
    assert "--group-id model_architecture" in command
    assert "--workdir mnist" in command
    assert "model_architecture" in config.research_groups_by_id()
    assert config.dependency_files == ["mnist/requirements.txt"]
    assert config.dependency_file_paths(Path(".").resolve())[0].name == "requirements.txt"
    assert config.workspace_agents_path() == "mnist/AGENTS.md"

    group = config.research_groups_by_id()["model_architecture"]
    assert group.workdir == "mnist"
    assert group.evaluation.command == command
    assert ".hiagentresearch/eval/" in group.reference_paths
    assert "mnist/data/" in group.generated_paths
    assert group.workspace_agents_path == "mnist/AGENTS.md"
    assert group.guidance_files == list(DEFAULT_GUIDANCE_FILES)
    assert "guidance_files" not in AgentContractConfig.model_fields


def test_group_resolution_from_branch() -> None:
    config = load_config(Path("configs/standard.yaml"))

    assert resolve_group_id_for_branch("research/model-architecture", config) == "model_architecture"
    assert resolve_group_id_for_branch("research/model-architecture/try-1", config) == "model_architecture"
    assert resolve_group_id_for_branch("feature/other", config) == "unknown"


def test_config_rejects_dependency_files_outside_workdir(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: app
dependency_files:
  - requirements.txt
evaluation:
  entrypoint: .hiagentresearch/eval/run.py
  command_template: "python {entrypoint} --workdir {workdir}"
  targets:
    f1:
      min: 0.9
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: explore
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="dependency_files"):
        load_config(config_path)


def test_config_rejects_eval_entrypoint_inside_workdir(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: app
evaluation:
  entrypoint: app/eval/run.py
  command_template: "python {entrypoint} --workdir {workdir}"
  targets:
    f1:
      min: 0.9
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: explore
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="entrypoint must live outside"):
        load_config(config_path)


def test_config_rejects_unknown_policy_mode(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
project_id: demo
workdir: app
evaluation:
  entrypoint: .hiagentresearch/eval/run.py
  command_template: "python {entrypoint} --workdir {workdir}"
  targets:
    f1:
      min: 0.9
policy_modes:
  explore: Explore.
research_groups:
  - id: demo
    branch: research/demo
    objective: Demo
    policy_mode: exploit
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="policy_mode"):
        load_config(config_path)


def _merge_config(execution_waves):
    """A minimal config with two baseline lineages + a near-zero merge group."""
    return HiAgentResearchConfig(
        project_id="demo",
        workdir=".",
        evaluation={
            "entrypoint": ".hiagentresearch/eval/run.py",
            "command_template": "true",
            "targets": {"accuracy": {"min": 0.9}},
        },
        policy_modes={"explore": "Explore.", "exploit": "Exploit."},
        orchestration={"execution_waves": execution_waves},
        research_groups=[
            ResearchGroupConfig(id="a", branch="research/a", objective="t", policy_mode="explore"),
            ResearchGroupConfig(id="b", branch="research/b", objective="t", policy_mode="explore"),
            ResearchGroupConfig(
                id="merge_best", branch="research/merge", policy_mode="exploit",
                task_kind="merge", lineage=LineageConfig(mode="inherit"),
            ),
        ],
    )


def test_merge_group_is_near_zero_config_with_auto_objective() -> None:
    """A merge group needs no objective and no inherit_from (both auto); the runtime
    group gets a generated objective."""
    config = _merge_config([["a"], ["b"], ["merge_best"]])
    merge = {g.id: g for g in config.research_groups}["merge_best"]
    assert merge.objective == ""
    assert merge.lineage.inherit_from is None
    assert merge.lineage.draw_from == []
    rg = config.to_research_group(merge)
    assert "Combine the strongest" in rg.objective


def test_merge_group_must_run_after_every_other_group() -> None:
    with pytest.raises(Exception, match="after every other group"):
        _merge_config([["merge_best", "a"], ["b"]])  # merge not after b


def test_non_merge_inherit_still_requires_inherit_from() -> None:
    with pytest.raises(Exception, match="inherit_from is required"):
        ResearchGroupConfig(
            id="x", branch="research/x", objective="t", policy_mode="explore",
            task_kind="engineering", lineage=LineageConfig(mode="inherit"),
        )


def test_engineering_group_defaults_to_last_commit_top_policy() -> None:
    """Engineering steps preserve the metric rather than beat it, so best_commit would discard
    their (metric-neutral) commit. They default to last_commit; an explicit choice is honored."""
    eng = ResearchGroupConfig(
        id="polish", branch="research/polish", objective="t", policy_mode="exploit",
        task_kind="engineering", lineage=LineageConfig(mode="inherit", inherit_from="opt"),
    )
    assert eng.lineage.top_commit_policy == "last_commit"
    # A metric cycle keeps the best_commit default.
    metric = ResearchGroupConfig(
        id="arch", branch="research/arch", objective="t", policy_mode="explore",
    )
    assert metric.lineage.top_commit_policy == "best_commit"
    # An explicit author choice on an engineering group wins.
    explicit = ResearchGroupConfig(
        id="polish2", branch="research/polish2", objective="t", policy_mode="exploit",
        task_kind="engineering",
        lineage=LineageConfig(mode="inherit", inherit_from="opt", top_commit_policy="best_commit"),
    )
    assert explicit.lineage.top_commit_policy == "best_commit"


def test_draw_from_to_unknown_group_fails() -> None:
    with pytest.raises(Exception, match="draw_from references unknown"):
        HiAgentResearchConfig(
            project_id="demo", workdir=".",
            evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                        "targets": {"accuracy": {"min": 0.9}}},
            policy_modes={"explore": "Explore.", "exploit": "Exploit."},
            orchestration={"execution_waves": [["a"], ["merge_best"]]},
            research_groups=[
                ResearchGroupConfig(id="a", branch="research/a", objective="t", policy_mode="explore"),
                ResearchGroupConfig(
                    id="merge_best", branch="research/merge", policy_mode="exploit", task_kind="merge",
                    lineage=LineageConfig(mode="inherit", draw_from=["nope"]),
                ),
            ],
        )


# --- Hierarchical area desugar (§4) ---

def _area_config(groups, **kw) -> HiAgentResearchConfig:
    return HiAgentResearchConfig(
        project_id="demo", workdir=".",
        evaluation={"entrypoint": ".hiagentresearch/eval/run.py", "command_template": "true",
                    "targets": {"accuracy": {"min": 0.9}}},
        policy_modes={"explore": "x", "exploit": "y"},
        research_groups=groups, **kw,
    )


def test_desugar_expands_area_into_leaves_and_collapse() -> None:
    cfg = _area_config([
        ResearchGroupConfig(id="augmentation", objective="aug", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"),
                            approaches=["randaugment", "mixup"]),
    ])
    by_id = {g.id: g for g in cfg.research_groups}
    # 2 leaves + 1 collapse (single terminal area → no final merge node).
    assert set(by_id) == {"augmentation__a1", "augmentation__a2", "augmentation__collapse"}
    assert by_id["augmentation__a1"].seed_approach == "randaugment"
    assert by_id["augmentation__a1"].role == "leaf"
    assert by_id["augmentation__a1"].area == "augmentation"
    assert by_id["augmentation__a1"].branch == "research/augmentation-a1"
    collapse = by_id["augmentation__collapse"]
    assert collapse.task_kind == "merge" and collapse.role == "collapse"
    assert collapse.lineage.draw_from == ["augmentation__a1", "augmentation__a2"]
    # combine defaults true → a real merge (uses the run's loop budget).
    assert collapse.loops is None
    # Waves: leaves first, then the collapse. No final merge (single area).
    assert cfg.execution_waves() == [["augmentation__a1", "augmentation__a2"], ["augmentation__collapse"]]


def test_desugar_merge_keep_policy_defaults_best_and_is_opt_in() -> None:
    """Merges default to best_commit (guardrail against a regressing integration). An area can
    opt its collapse into last_commit via its own lineage.top_commit_policy, and the final merge
    via the top-level final_merge_top_commit_policy — to keep the integrated result over score."""
    # Default: collapse and final merge both best_commit.
    default = _area_config([
        ResearchGroupConfig(id="a", objective="a", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), approaches=["x", "y"]),
        ResearchGroupConfig(id="b", objective="b", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), approaches=["p", "q"]),
    ])
    by_id = {g.id: g for g in default.research_groups}
    assert by_id["a__collapse"].lineage.top_commit_policy == "best_commit"
    assert by_id["final_merge"].lineage.top_commit_policy == "best_commit"

    # Opt-in: area keeps its collapsed result latest; final merge keeps its integrated result.
    opted = _area_config([
        ResearchGroupConfig(id="a", objective="a", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline", top_commit_policy="last_commit"),
                            approaches=["x", "y"]),
        ResearchGroupConfig(id="b", objective="b", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), approaches=["p", "q"]),
    ], final_merge_top_commit_policy="last_commit")
    opted_by_id = {g.id: g for g in opted.research_groups}
    assert opted_by_id["a__collapse"].lineage.top_commit_policy == "last_commit"
    assert opted_by_id["b__collapse"].lineage.top_commit_policy == "best_commit"  # untouched
    assert opted_by_id["final_merge"].lineage.top_commit_policy == "last_commit"


def test_desugar_combine_false_makes_zero_loop_select() -> None:
    cfg = _area_config([
        ResearchGroupConfig(id="architecture", objective="arch", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), combine=False,
                            approaches=["deepen", "widen"]),
    ])
    collapse = {g.id: g for g in cfg.research_groups}["architecture__collapse"]
    assert collapse.task_kind == "merge"
    assert collapse.loops == 0  # select: adopt strongest leaf, no integration loops


def test_desugar_inherit_rewrites_to_upstream_collapse_and_auto_merges_terminals() -> None:
    cfg = _area_config([
        ResearchGroupConfig(id="architecture", objective="a", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), combine=False,
                            approaches=["deepen", "widen", "se"]),
        ResearchGroupConfig(id="optimization", objective="o", policy_mode="exploit",
                            lineage=LineageConfig(mode="inherit", inherit_from="architecture"),
                            approaches=["cosine", "wd"]),
        ResearchGroupConfig(id="augmentation", objective="g", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"),
                            approaches=["randaug", "mixup"]),
    ])
    by_id = {g.id: g for g in cfg.research_groups}
    # optimization's leaves inherit from architecture's COLLAPSE node, not the area id.
    assert by_id["optimization__a1"].lineage.inherit_from == "architecture__collapse"
    # Terminal areas (optimization + augmentation; architecture is upstream) auto-merge.
    final = by_id["final_merge"]
    assert final.role == "final_merge" and final.task_kind == "merge"
    assert set(final.lineage.draw_from) == {"optimization__collapse", "augmentation__collapse"}
    # Topo waves: level-0 leaves parallel, their collapses, level-1 leaves, its collapse, final.
    waves = cfg.execution_waves()
    assert waves[0] == ["architecture__a1", "architecture__a2", "architecture__a3",
                        "augmentation__a1", "augmentation__a2"]
    assert waves[-1] == ["final_merge"]
    # The non-terminal merge (architecture collapse) validates despite not being last.


def test_desugar_single_goal_is_one_lineage_no_collapse() -> None:
    """K<=1 converges with the no-fan-out case: one lineage (the area itself), no collapse
    node, no pointless 1-source merge. The lone goal becomes the leaf's seed."""
    cfg = _area_config([
        ResearchGroupConfig(id="solo", objective="o", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), approaches=["one idea"]),
    ])
    ids = [g.id for g in cfg.research_groups]
    assert ids == ["solo"]  # no solo__a1, no solo__collapse, no final_merge (single terminal)
    solo = cfg.research_groups[0]
    assert solo.role == "leaf" and solo.task_kind != "merge"
    assert solo.seed_approach == "one idea"
    assert solo.approaches == []  # cleared on the emitted leaf
    assert cfg.execution_waves() == [["solo"]]


def test_desugar_combine_areas_overrides_final_merge() -> None:
    cfg = _area_config([
        ResearchGroupConfig(id="a", objective="a", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), approaches=["x", "y"]),
        ResearchGroupConfig(id="b", objective="b", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), approaches=["p", "q"]),
        ResearchGroupConfig(id="c", objective="c", policy_mode="explore",
                            lineage=LineageConfig(mode="baseline"), approaches=["m", "n"]),
    ], combine_areas=["a", "b"])
    final = {g.id: g for g in cfg.research_groups}["final_merge"]
    assert set(final.lineage.draw_from) == {"a__collapse", "b__collapse"}


def test_desugar_no_approaches_is_today_verbatim() -> None:
    """A flat config (no approaches anywhere) is passed through untouched: same groups,
    same explicit branches, manual waves preserved."""
    cfg = load_config()  # canonical flat config.yaml
    ids = [g.id for g in cfg.research_groups]
    assert "model_architecture" in ids
    assert all("__a" not in i and "__collapse" not in i for i in ids)
    # Explicit branch + manual waves are preserved (no auto-derivation kicks in).
    assert cfg.group_by_id("model_architecture").branch == "research/model-architecture"


def test_shipped_configs_load() -> None:
    """Both shipped configs validate: standard (flat, default) and fanout (desugars)."""
    standard = load_config(Path("configs/standard.yaml"))
    assert "model_architecture" in [g.id for g in standard.research_groups]
    # No reset policy mode anymore.
    assert "reset" not in standard.policy_modes

    fanout = load_config(Path("configs/fanout.yaml"))
    ids = [g.id for g in fanout.research_groups]
    # Areas fanned out into leaves + collapses, with a degenerate engineering leaf (polish)
    # and an auto final merge.
    assert "architecture__a1" in ids and "architecture__collapse" in ids
    assert "polish" in ids  # degenerate single-leaf engineering area
    assert "final_merge" in ids


def test_default_config_is_standard() -> None:
    assert load_config().project_id == load_config(Path("configs/standard.yaml")).project_id


def test_desugar_rejects_unknown_inherit_area() -> None:
    with pytest.raises(Exception, match="inherit_from references unknown area"):
        _area_config([
            ResearchGroupConfig(id="a", objective="a", policy_mode="explore",
                                lineage=LineageConfig(mode="inherit", inherit_from="ghost"),
                                approaches=["x"]),
        ])
