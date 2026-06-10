from hiagentresearch import cli


def test_cli_init_does_not_require_cursor_credentials(monkeypatch) -> None:
    called = {}

    def fail_credentials():
        raise AssertionError("init should not require Cursor credentials")

    def fake_init():
        called["init"] = True
        return 0

    monkeypatch.setattr(cli, "ensure_cursor_api_key", fail_credentials)
    monkeypatch.setattr(cli, "init_state", fake_init)

    assert cli.main(["init"]) == 0
    assert called["init"] is True


def test_cli_delegates_registry(monkeypatch) -> None:
    seen = {}

    def fake_registry(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli.registry_view, "main", fake_registry)

    assert cli.main(["registry", "summary", "--json"]) == 0
    assert seen["argv"] == ["summary", "--json"]


def test_cli_delegates_dashboard(monkeypatch) -> None:
    seen = {}

    def fake_dashboard(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli.dashboard_cli, "main", fake_dashboard)

    assert cli.main(["dashboard", "build"]) == 0
    assert seen["argv"] == ["build"]


def test_cli_runs_loop_command(monkeypatch, tmp_path) -> None:
    seen = {}
    credentials = {}

    class Summary:
        ok = True

        def to_dict(self):
            return {"ok": True}

    def fake_run_loops(**kwargs):
        seen.update(kwargs)
        return Summary()

    def fake_credentials():
        credentials["checked"] = True

    monkeypatch.setattr(cli, "run_loops", fake_run_loops)
    monkeypatch.setattr(cli, "ensure_cursor_api_key", fake_credentials)

    assert cli.main(["loops", "--group-id", "model_architecture", "--loops", "2", "--workdir", str(tmp_path)]) == 0
    assert credentials["checked"] is True
    assert seen["group_id"] == "model_architecture"
    assert seen["loops"] == 2
