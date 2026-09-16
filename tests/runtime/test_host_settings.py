"""Private settings lifecycle; fixtures contain synthetic credentials only."""
from __future__ import annotations

import fcntl
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom import cli, workflow_host
from hypoloom.backup_restore import create_quiesced_backup, restore_backup


def _source(tmp_path: Path, content: bytes = b'{"env":{"ANTHROPIC_API_KEY":"fixture-private-key"}}') -> Path:
    path = tmp_path / "operator-settings.json"
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def _run_database(root: Path, *, rows: bool = True) -> None:
    root.mkdir(mode=0o700, exist_ok=True)
    with sqlite3.connect(root / "state.sqlite") as db:
        db.execute("CREATE TABLE workflow_runs(run_id TEXT PRIMARY KEY)")
        if rows:
            db.execute("INSERT INTO workflow_runs VALUES ('fixture-run')")


def test_snapshot_reuses_private_bytes_without_reading_changed_or_missing_source(tmp_path):
    from hypoloom.host_settings import prepare_host_settings
    source = _source(tmp_path)
    original = source.read_bytes()
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    private = prepare_host_settings(root, source)
    assert private == root / "host/claude-settings.json"
    assert private.read_bytes() == original
    assert private.stat().st_mode & 0o777 == 0o600
    assert private.parent.stat().st_mode & 0o777 == 0o700
    source.write_text('{"env":{"ANTHROPIC_API_KEY":"changed"}}')
    assert prepare_host_settings(root, source).read_bytes() == original
    source.unlink()
    assert prepare_host_settings(root, source).read_bytes() == original
    assert prepare_host_settings(root, tmp_path / "different-source").read_bytes() == original


def test_existing_run_missing_snapshot_never_rebinds_source(tmp_path):
    from hypoloom.host_settings import HostConfigurationError, prepare_host_settings
    root = tmp_path / "state"
    _run_database(root)
    source = _source(tmp_path)
    with pytest.raises(HostConfigurationError, match="missing.*private settings"):
        prepare_host_settings(root, source)
    assert not (root / "host").exists()


def test_empty_initialized_root_can_prepare_once(tmp_path):
    from hypoloom.host_settings import prepare_host_settings
    root = tmp_path / "state"
    _run_database(root, rows=False)
    assert prepare_host_settings(root, _source(tmp_path)).is_file()
    assert prepare_host_settings(root, None) is None


@pytest.mark.parametrize("damage", ["symlink", "permissions", "directory", "invalid-json", "env-list"])
def test_invalid_private_settings_fail_closed_without_replacement(tmp_path, damage):
    from hypoloom.host_settings import HostConfigurationError, prepare_host_settings
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    source = _source(tmp_path)
    private = prepare_host_settings(root, source)
    if damage == "symlink":
        private.unlink(); private.symlink_to(source)
    elif damage == "directory":
        private.unlink(); private.mkdir()
    elif damage == "permissions":
        private.chmod(0o644)
    else:
        private.write_text("fixture-private-key" if damage == "invalid-json" else '{"env":[]}')
    with pytest.raises(HostConfigurationError) as caught:
        prepare_host_settings(root, source)
    assert "fixture-private-key" not in str(caught.value)


def test_only_private_companion_has_portable_logical_identity(tmp_path):
    from hypoloom.host_settings import prepare_host_settings
    source = _source(tmp_path)
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir(); right.mkdir()
    a = prepare_host_settings(left, source)
    b = prepare_host_settings(right, source)
    version = workflow_host.installed_run_version
    assert version(runtime_files=(a,), private_settings_path=a) == version(runtime_files=(b,), private_settings_path=b)
    assert version(runtime_files=(a,)) != version(runtime_files=(b,))
    b.write_bytes(b'{"env":{"ANTHROPIC_API_KEY":"different"}}')
    assert version(runtime_files=(a,), private_settings_path=a) != version(runtime_files=(b,), private_settings_path=b)


def test_serve_prepares_under_existing_lock_before_host_version(tmp_path, monkeypatch):
    source = _source(tmp_path)
    root = tmp_path / "state"
    seen = []

    class Host:
        stop_requested = True
        operational_halt = None

        def __init__(self, state_root, **options):
            with (state_root / "controller.lock").open() as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            snapshot = options["private_settings_path"]
            assert options["claude_agent_host"].settings_path == snapshot
            seen.append(snapshot.read_bytes())

        def initialize(self): pass
        def close(self): pass

    monkeypatch.setattr(workflow_host, "WorkflowHost", Host)
    monkeypatch.setattr(workflow_host.signal, "signal", lambda *args: None)
    original = source.read_bytes()
    for _ in range(2):
        assert workflow_host.serve(root, claude_agent_host=SimpleNamespace(settings_path=source)) == 0
        source.write_bytes(b'{"env":{"ANTHROPIC_API_KEY":"changed"}}')
    assert seen == [original, original]


def test_bad_settings_release_serve_lock_and_cli_returns_configuration_exit(tmp_path, capsys):
    source = _source(tmp_path, b"fixture-private-key is not JSON")
    root = tmp_path / "state"
    arguments = ("serve", "--state-root", str(root), "--agent-adapter", "claude",
                 "--claude-executable", "/usr/bin/true", "--claude-model", "fixture-model",
                 "--claude-settings", str(source))
    for _ in range(2):
        assert cli.main(arguments) == 78
    assert "fixture-private-key" not in capsys.readouterr().err
    with (root / "controller.lock").open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("kind, expected", [("configuration", 78), ("operational", 2), ("io", 2)])
def test_exit_78_is_not_a_blanket_operational_or_io_mapping(tmp_path, monkeypatch, kind, expected):
    from hypoloom.host_settings import HostConfigurationError
    error = {"configuration": HostConfigurationError, "operational": workflow_host.OperationalHalt,
             "io": OSError}[kind]

    def fail(*args, **kwargs):
        raise error("fixture failure")

    monkeypatch.setattr(workflow_host, "serve", fail)
    assert cli.main(("serve", "--state-root", str(tmp_path / "state"))) == expected


def test_backup_excludes_settings_and_explicit_restore_preserves_version(tmp_path):
    from hypoloom.host_settings import HostConfigurationError, prepare_host_settings
    source = _source(tmp_path)
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    private = prepare_host_settings(root, source)
    _run_database(root)
    (root / "artifacts").mkdir()
    backup = create_quiesced_backup(root, tmp_path / "backups", "fixture", assert_quiescent=lambda *a: None,
                                   audit=lambda *a: None).path
    assert {p.name for p in backup.iterdir()} == {"state.sqlite", "artifacts"}
    restored = restore_backup(backup, tmp_path / "restored", audit=lambda *a: None, claude_settings=private)
    rebound = prepare_host_settings(restored, tmp_path / "source-not-needed")
    version = workflow_host.installed_run_version
    assert version(runtime_files=(private,), private_settings_path=private) == version(runtime_files=(rebound,), private_settings_path=rebound)
    inspection = restore_backup(backup, tmp_path / "inspection", audit=lambda *a: None)
    with pytest.raises(HostConfigurationError, match="missing.*private settings"):
        prepare_host_settings(inspection, source)
    assert not (backup / "host").exists()


def test_restore_cli_passes_explicit_private_source_only_to_fresh_restore(tmp_path, monkeypatch, capsys):
    observed = {}

    def restore(backup, root, **options):
        observed.update(options)
        return root

    monkeypatch.setattr(cli, "restore_backup", restore)
    source = _source(tmp_path)
    assert cli.main(("restore", "--backup-path", str(tmp_path / "backup"), "--state-root", str(tmp_path / "new"),
                     "--claude-settings", str(source))) == 0
    assert observed["claude_settings"] == str(source)
    assert "fixture-private-key" not in capsys.readouterr().out


def test_immutable_bundle_mismatch_is_a_typed_configuration_failure():
    from hypoloom.host_settings import HostConfigurationError
    version = workflow_host.installed_run_version()
    row = {name: getattr(version, name) for name in workflow_host._VERSION_FIELDS}
    row.update(run_id="fixture", runtime_environment_id="changed")
    host = object.__new__(workflow_host.WorkflowHost)
    host.version = version
    host.store = SimpleNamespace(inspect=lambda query: [row])
    with pytest.raises(HostConfigurationError, match="immutable bundle"):
        host._verify_versions()


def _bound_host(root: Path, private: Path):
    from test_phase8_parallel_adversarial import _ControlledAgentHost, _MODEL
    adapter = _ControlledAgentHost(Path(__file__).resolve())
    adapter.settings_path = private
    return workflow_host.WorkflowHost(root, claude_agent_host=adapter, claude_model=_MODEL,
                                      private_settings_path=private)


@pytest.mark.parametrize("damage", ["bytes", "missing"])
def test_actual_host_prewrite_drift_guard_leaves_no_new_attempt(tmp_path, damage):
    from hypoloom.host_settings import HostConfigurationError, prepare_host_settings
    from test_phase8_parallel_adversarial import _create_run
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    source = _source(tmp_path)
    private = prepare_host_settings(root, source)
    host = _bound_host(root, private)
    try:
        host.initialize()
        _create_run(host, "settings-prewrite")
        source.write_bytes(b'{"env":{"ANTHROPIC_API_KEY":"source-changed"}}')
        host._check_agent_runtime()
        previous = host.store.list_attempts()
        if damage == "bytes":
            private.write_bytes(b'{"env":{"ANTHROPIC_API_KEY":"private-changed"}}')
        else:
            private.unlink()
        with pytest.raises(HostConfigurationError):
            for _ in range(20):
                host.tick()
        assert host.store.list_attempts() == previous
        assert not previous
        assert not host.claude_agent_host.started
    finally:
        host.close()


def test_actual_host_restore_matches_original_bytes_and_rejects_new_credentials_before_recovery(tmp_path, monkeypatch):
    from hypoloom.host_settings import HostConfigurationError, prepare_host_settings
    from test_phase8_parallel_adversarial import _create_run
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    source = _source(tmp_path)
    private = prepare_host_settings(root, source)
    host = _bound_host(root, private)
    try:
        host.initialize()
        _create_run(host, "settings-restore")
        initial_version = host.version
    finally:
        host.close()
    backup = create_quiesced_backup(root, tmp_path / "backups", "fixture", assert_quiescent=lambda *a: None,
                                   audit=lambda *a: None).path
    restored = restore_backup(backup, tmp_path / "restored", audit=lambda *a: None, claude_settings=private)
    rebound = _bound_host(restored, restored / "host/claude-settings.json")
    try:
        assert rebound.version == initial_version
        rebound.initialize()
    finally:
        rebound.close()
    source.write_bytes(b'{"env":{"ANTHROPIC_API_KEY":"wrong-credential"}}')
    wrong = restore_backup(backup, tmp_path / "wrong", audit=lambda *a: None, claude_settings=source)
    rejected = _bound_host(wrong, wrong / "host/claude-settings.json")
    monkeypatch.setattr(rejected, "_recover_abandoned_attempts", lambda: pytest.fail("recovered before verifying credentials"))
    try:
        with pytest.raises(HostConfigurationError, match="immutable bundle"):
            rejected.initialize()
    finally:
        rejected.close()


def test_private_companion_is_not_in_full_cc_mounts_or_settings(tmp_path):
    from hypoloom.host_settings import prepare_host_settings
    from test_full_cc_runtime import _argv
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    private = prepare_host_settings(root, _source(tmp_path))
    argv = _argv(tmp_path, protected_paths=(private,))
    assert str(private) not in argv
    assert "fixture-private-key" not in " ".join(argv)
    with pytest.raises(ValueError, match="Host authority"):
        _argv(tmp_path, protected_paths=(private,), read_roots=(private.parent,))


@pytest.mark.parametrize("include_private_data", [False, True])
def test_support_export_never_reads_or_copies_private_companion(tmp_path, monkeypatch, include_private_data):
    from hypoloom.host_settings import prepare_host_settings
    from hypoloom.support_bundle import export_support_bundle
    from test_debug_mode import _seed_agent_retry
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    private = prepare_host_settings(root, _source(tmp_path))
    run_id, _, _ = _seed_agent_retry(root)
    original = Path.open

    def protected_open(path, *args, **kwargs):
        assert path != private, "support must not read credential input"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", protected_open)
    output = tmp_path / "support"
    export_support_bundle(root, run_id, output, include_private_data=include_private_data)
    assert not (output / "host").exists()
    assert all(b"fixture-private-key" not in path.read_bytes() for path in output.rglob("*") if path.is_file())
