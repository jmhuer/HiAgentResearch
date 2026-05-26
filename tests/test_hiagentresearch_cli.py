from hiagentresearch import cli


def test_cli_delegates_registry(monkeypatch) -> None:
    seen = {}

    def fake_registry(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli.registry_view, "main", fake_registry)

    assert cli.main(["registry", "summary", "--json"]) == 0
    assert seen["argv"] == ["summary", "--json"]


def test_cli_runs_loop_command(monkeypatch, tmp_path) -> None:
    seen = {}

    class Summary:
        ok = True

        def to_dict(self):
            return {"ok": True}

    def fake_run_loops(**kwargs):
        seen.update(kwargs)
        return Summary()

    monkeypatch.setattr(cli, "run_loops", fake_run_loops)

    assert cli.main(["loops", "--group-id", "model_architecture", "--loops", "2", "--workdir", str(tmp_path)]) == 0
    assert seen["group_id"] == "model_architecture"
    assert seen["loops"] == 2
