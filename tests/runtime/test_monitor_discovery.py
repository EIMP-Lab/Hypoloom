"""CC re-entry derives identities from actual Host state, without a registry."""

import json
import os
import shlex
import subprocess
import sys

import pytest

from hypoloom import monitor_launch
from hypoloom.cli import build_parser as cli_parser
from hypoloom.state_store import StateStore
from test_monitor_launcher import _create_monitorable_run, LAUNCHER


def inspect(repo, **options):
    return monitor_launch.inspect_runs(
        python=sys.executable, working_directory=str(repo), **options
    )


def test_empty_project_and_missing_explicit_root_create_nothing(tmp_path):
    assert inspect(tmp_path)["status"] == "empty"
    missing = tmp_path / "missing"
    result = inspect(tmp_path, state_root=str(missing))
    assert result["status"] == "unavailable"
    assert result["warnings"][0]["reason"] == "state_database_missing"
    assert not list(tmp_path.iterdir())


def test_discovery_reconstructs_commands_after_a_new_helper_process(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo's $literal; space"
    root = repo / "state" / "directory-is-not-the-run-id"
    _create_monitorable_run(root, "actual-run")
    before = (root / "state.sqlite").read_bytes()
    # Inspection has no supervisor, transport or provider operation, even in tmux.
    with monkeypatch.context() as patch:
        patch.setattr(
            monitor_launch.subprocess,
            "run",
            lambda *a, **kw: pytest.fail("inspection spawned a process"),
        )
        first = inspect(repo, interval=1.25, web_port=12345, web_enabled=False)
    env = {
        **os.environ,
        "TMUX": "present-but-must-not-launch",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("HYPOLOOM_RUNTIME_FAMILY", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(LAUNCHER),
            "--inspect",
            "--working-directory",
            str(repo),
            "--python",
            sys.executable,
            "--interval",
            "1.25",
            "--web-port",
            "12345",
            "--no-web",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=10,
    )
    assert json.loads(completed.stdout) == first
    assert first["status"] == "ready" and first["host_liveness"] == "not_checked"
    assert first["selected"]["run_id"] == "actual-run"
    assert first["selected"]["state_root"] == str(root)
    commands = first["commands"]
    cc = shlex.split(commands["cc_command"])
    assert cc[:2] == ["/autoresearch-monitor", "actual-run"]
    assert cc[cc.index("--state-root") + 1] == str(root)
    change_dir, command = commands["terminal_command"].split(" && ", 1)
    assert shlex.split(change_dir) == ["cd", "--", str(repo)]
    monitor = shlex.split(command)
    parsed = cli_parser().parse_args(monitor[monitor.index("monitor") :])
    assert parsed.state_root == str(root) and parsed.run_id == "actual-run"
    assert (
        parsed.interval == 1.25 and parsed.web_port == 12345 and not parsed.web_enabled
    )
    launch = monitor_launch.build_parser().parse_args(commands["launcher_argv"][4:])
    assert (
        launch.run_id == parsed.run_id
        and launch.state_root == parsed.state_root
        and not launch.inspect
    )
    json_command = commands["json_argv"]
    assert (
        cli_parser()
        .parse_args(json_command[json_command.index("monitor") :])
        .json_output
    )
    assert (root / "state.sqlite").read_bytes() == before
    assert not StateStore(root).inspect("SELECT * FROM execution_attempts")
    assert sorted(path.name for path in repo.iterdir()) == ["state"]


def test_multiple_roots_and_duplicate_ids_require_an_exact_selection(tmp_path):
    for name, identifier in (
        ("one", "shared-id"),
        ("two", "shared-id"),
        ("three", "different-id"),
    ):
        _create_monitorable_run(tmp_path / "state" / name, identifier)
    page = inspect(tmp_path, limit=1)
    assert (
        page["status"] == "needs_selection"
        and page["total"] == 3
        and page["next_offset"] == 1
    )
    assert "commands" not in page
    second = inspect(tmp_path, limit=1, offset=page["next_offset"])
    assert second["runs"] != page["runs"] and second["status"] == "needs_selection"
    duplicate = inspect(tmp_path, run_id="shared-id")
    assert duplicate["total"] == 2 and duplicate["status"] == "needs_selection"
    chosen = inspect(
        tmp_path, run_id="shared-id", state_root=str(tmp_path / "state/one")
    )
    assert chosen["status"] == "ready" and chosen["selected"]["state_root"].endswith(
        "/one"
    )
    absent = inspect(tmp_path, run_id="does-not-exist")
    assert absent["status"] == "not_found" and "commands" not in absent


def test_unreadable_root_prevents_auto_selection_but_explicit_identity_still_works(
    tmp_path,
):
    known = tmp_path / "state/known"
    _create_monitorable_run(known)
    damaged = tmp_path / "state/broken"
    damaged.mkdir()
    (damaged / "state.sqlite").write_bytes(b"not a state database")
    result = inspect(tmp_path)
    assert (
        result["total"] == 1
        and result["status"] == "needs_selection"
        and result["warnings"]
    )
    assert "commands" not in result
    assert inspect(tmp_path, state_root=str(known))["status"] == "ready"
    assert (damaged / "state.sqlite").read_bytes() == b"not a state database"


def test_external_roots_require_an_explicit_path_and_aliases_are_not_scanned(tmp_path):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    external = tmp_path / "outside"
    _create_monitorable_run(external)
    (repo / "state/alias").symlink_to(external, target_is_directory=True)
    assert inspect(repo)["status"] == "empty"
    assert inspect(repo, state_root=str(external))["status"] == "ready"


@pytest.mark.parametrize(
    "options",
    [
        {"run_id": "../invalid"},
        {"limit": 0},
        {"offset": -1},
        {"interval": float("nan")},
    ],
)
def test_invalid_choices_do_not_create_a_source(tmp_path, options):
    with pytest.raises(monitor_launch.MonitorLaunchError):
        inspect(tmp_path, **options)
    assert not list(tmp_path.iterdir())


def test_changed_source_does_not_produce_a_launch_command(tmp_path, monkeypatch):
    from hypoloom import monitor

    _create_monitorable_run(tmp_path / "state/known")

    def disappeared(*args):
        raise monitor.MonitorError("unavailable")

    monkeypatch.setattr(monitor, "validate_monitor_source", disappeared)
    result = inspect(tmp_path)
    assert result["status"] == "unavailable" and "commands" not in result
    assert result["warnings"][0]["reason"] == "source_changed"
