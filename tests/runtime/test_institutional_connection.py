"""Connection preparation must not create research, adopt other browsers or expose CDP."""
import argparse
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from hypoloom import institutional_connection as connection
from hypoloom.institutional_options import add_arguments
from hypoloom.cli import build_parser


def args(*values):
    parser = argparse.ArgumentParser()
    add_arguments(parser, connection=True)
    return parser.parse_args(values)


def spec(tmp_path):
    return {"mode": "local", "endpoint": "http://127.0.0.1:19222", "ready_timeout": 3,
            "executable": "/usr/bin/chromium", "profile": str(tmp_path / "private profile"),
            "desktop": {"kind": "x11"}}


def test_default_external_status_and_stop_never_manage_resources(monkeypatch):
    monkeypatch.setattr(connection, "_run", lambda *a, **kw: pytest.fail("external endpoint managed a process"))
    monkeypatch.setattr(connection, "probe", lambda *a: {"connected": True, "browser": "Chromium"})
    selected = args("--institutional-browser-mode", "existing")
    assert connection.operate(selected, "start")["status"] == "ready"
    assert connection.operate(selected, "stop")["status"] == "unmanaged"


def test_off_and_legacy_omission_do_not_construct_browser_access(monkeypatch):
    from hypoloom import institutional_options, institutional_runtime
    monkeypatch.setattr(institutional_options, "PlaywrightInstitutionalBrowser", lambda *a, **k: pytest.fail("created browser"))
    monkeypatch.setattr(institutional_runtime, "RunAccess", lambda *a, **k: pytest.fail("created access queue"))
    assert institutional_options.build(args()) is None
    selected = args("--institutional-browser-mode", "off")
    assert institutional_options.build(selected) is None
    assert institutional_options.preview(selected) == {"institutional_connection": {"enabled": False, "mode": "off"}}
    assert institutional_options.launch_arguments(selected, include_connection=True) == ["--institutional-browser-mode", "off"]
    with pytest.raises(ValueError): connection.operate(selected, "start")


@pytest.mark.parametrize("values", [
    ("--institutional-browser-url", "http://127.0.0.1:9222"),
    ("--institutional-browser-ui-url", "http://127.0.0.1:5800"),
    ("--institutional-library-url", "https://library.example.edu/"),
    ("--institutional-browser-profile", "/tmp/dedicated"),
    ("--institutional-browser-executable", "/usr/bin/chromium"),
    ("--institutional-compose-file", "/tmp/browser.yaml"),
    ("--institutional-port", "9223"), ("--institutional-connect-timeout", "60"),
    ("--institutional-provider-mode", "IEEE=auto"),
    ("--institutional-acquisition-seconds", "90"), ("--institutional-manual-seconds", "300"),
])
def test_off_refuses_conflicting_institutional_configuration(values, monkeypatch):
    from hypoloom import institutional_options
    monkeypatch.setattr(institutional_options, "PlaywrightInstitutionalBrowser", lambda *a, **k: pytest.fail("created browser"))
    with pytest.raises(ValueError):
        institutional_options.build(args("--institutional-browser-mode", "off", *values))


@pytest.mark.parametrize("values", [
    ("--institutional-connect-timeout", "nan"), ("--institutional-connect-timeout", "121"),
    ("--institutional-browser-profile", "/tmp/profile"),
    ("--institutional-browser-mode", "local", "--institutional-browser-url", "http://0.0.0.0:9222"),
    ("--institutional-browser-mode", "server", "--institutional-browser-url", "http://127.0.0.1:9223"),
    ("--institutional-browser-mode", "local", "--institutional-browser-profile", "relative"),
])
def test_invalid_configuration_is_rejected_before_effects(values, monkeypatch):
    monkeypatch.setattr(connection, "_run", lambda *a, **kw: pytest.fail("invalid input caused effects"))
    with pytest.raises(ValueError): connection.plan(args(*values))


def test_profile_is_private_and_never_the_daily_browser(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for path in (tmp_path, tmp_path / "config", tmp_path / "config/google-chrome", tmp_path / "config/chromium/Default"):
        with pytest.raises(ValueError): connection._profile(str(path), "/bin/chromium")
    monkeypatch.setattr(connection, "_run", lambda *a, **kw: pytest.fail("preview starts systemd"))
    planned = connection.plan(args("--institutional-browser-mode", "local", "--institutional-browser-profile", str(tmp_path / "dedicated")))
    assert not Path(planned["profile"]).exists()


def test_desktop_uses_user_session_not_installed_desktop(monkeypatch, tmp_path):
    monkeypatch.setattr(connection.sys, "platform", "linux")
    monkeypatch.setattr(connection.os, "geteuid", lambda: os.getuid())
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-fixture")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert connection.desktop()["kind"] is None
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(tmp_path / "wayland-fixture"))
        assert connection.desktop()["kind"] == "wayland"
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("WAYLAND_DISPLAY", "missing")
    assert connection.desktop()["kind"] == "x11"
    monkeypatch.setattr(connection.os, "geteuid", lambda: 0)
    assert connection.desktop()["kind"] is None


@pytest.fixture
def manager(monkeypatch):
    state = {"live": False, "owned": False, "connected": False, "calls": []}
    monkeypatch.setattr(connection, "_unit", lambda s: "hypoloom-browser@fixture.service")
    monkeypatch.setattr(connection, "_unit_state", lambda u: {"ActiveState": "active" if state["live"] else "inactive"})
    monkeypatch.setattr(connection, "_owned", lambda s, p: state["owned"])
    monkeypatch.setattr(connection, "probe", lambda *a: {"connected": state["connected"]})
    monkeypatch.setattr(connection, "_occupied", lambda s: state["connected"])
    monkeypatch.setattr(connection, "_close_browser", lambda s: state.update(live=False, owned=False, connected=False))
    def run(argv, **kwargs):
        state["calls"].append(argv)
        if argv[0] == "systemd-run": state.update(live=True, owned=True, connected=True)
        if argv[:3] == ["systemctl", "--user", "stop"]: state.update(live=False, owned=False, connected=False)
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(connection, "_run", run)
    return state


def test_local_start_reuse_stop_preserves_profile(manager, tmp_path):
    settings = spec(tmp_path)
    assert connection._local(settings, "start")["status"] == "started"
    profile = Path(settings["profile"])
    (profile / "cookie-fixture").write_text("opaque")
    assert profile.stat().st_mode & 0o777 == 0o700
    assert connection._local(settings, "start")["status"] == "reused"
    assert len(manager["calls"]) == 1
    argv = manager["calls"][0]
    assert "--remote-debugging-address=127.0.0.1" in argv and "--ozone-platform=x11" in argv
    assert "--no-sandbox" not in argv and "--headless" not in argv
    assert connection._local(settings, "stop")["status"] == "stopped"
    assert (profile / "cookie-fixture").read_text() == "opaque"


@pytest.mark.parametrize("case", ["occupied", "foreign_unit", "locked_profile", "public_profile", "headless"])
def test_failed_local_start_never_kills_existing_processes(manager, tmp_path, case):
    settings = spec(tmp_path)
    if case == "occupied": manager["connected"] = True
    if case == "foreign_unit": manager["live"] = True
    if case in {"locked_profile", "public_profile"}:
        path = Path(settings["profile"]); path.mkdir(mode=0o755 if case == "public_profile" else 0o700)
        if case == "locked_profile": (path / "SingletonLock").symlink_to("gone-owner")
    if case == "headless": settings["desktop"] = {"kind": None, "reason": "graphical_session_unavailable"}
    with pytest.raises(connection.ConnectionFailure): connection._local(settings, "start")
    assert manager["calls"] == []


def test_simultaneous_start_reuses_winner_and_does_not_stop_it(manager, tmp_path, monkeypatch):
    def race(argv, **kwargs):
        manager["calls"].append(argv)
        assert argv[0] == "systemd-run"
        manager.update(live=True, owned=True, connected=True)
        return SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(connection, "_run", race)
    assert connection._local(spec(tmp_path), "start")["status"] == "reused"
    assert len(manager["calls"]) == 1


@pytest.mark.parametrize("boundary", ["probe", "_occupied", "_profile_busy"])
def test_concurrent_start_during_preflight_reuses_owned_winner(manager, tmp_path, monkeypatch, boundary):
    def winner(*a):
        manager.update(live=True, owned=True, connected=True)
        return {"connected": True} if boundary == "probe" else True
    monkeypatch.setattr(connection, boundary, winner)
    assert connection._local(spec(tmp_path), "start")["status"] == "reused"
    assert manager["calls"] == []


def test_concurrent_start_waits_for_activating_unit_pid(manager, tmp_path, monkeypatch):
    states = iter([
        {"ActiveState": "activating", "MainPID": "0"},
        {"ActiveState": "activating", "MainPID": "0"},
        {"ActiveState": "active", "MainPID": "123"},
    ])
    monkeypatch.setattr(connection, "_unit_state", lambda u: next(states))
    monkeypatch.setattr(connection, "_owned", lambda s, p: p["MainPID"] == "123")
    manager["connected"] = True
    assert connection._local(spec(tmp_path), "start")["status"] == "reused"
    assert manager["calls"] == []


@pytest.mark.parametrize("bad", ["missing", "public", "extra", "valid"])
def test_compose_checks_expanded_ports_before_start(tmp_path, monkeypatch, bad):
    path = tmp_path / "compose.yaml"; path.touch()
    settings = {"compose_file": str(path), "endpoint": "http://127.0.0.1:9222", "ready_timeout": 3}
    ports = [{"host_ip": "127.0.0.1", "published": str(p), "target": p} for p in (5800, 9222)]
    if bad == "missing": ports = []
    if bad == "public": ports[0]["host_ip"] = "0.0.0.0"
    if bad == "extra": ports.append({"host_ip": "127.0.0.1", "published": "5900", "target": 5900})
    calls = []
    monkeypatch.setattr(connection, "_occupied", lambda s: False)
    monkeypatch.setattr(connection, "probe", lambda *a: {"connected": any("up" in c for c in calls)})
    def run(argv, **kwargs):
        calls.append(argv)
        result = json.dumps({"services": {"browser": {"ports": ports}}}) if "config" in argv else ""
        return SimpleNamespace(returncode=0, stdout=result)
    monkeypatch.setattr(connection, "_run", run)
    if bad == "valid":
        assert connection._compose(settings, "start")["status"] == "started"
        assert "--no-deps" in calls[-1] and calls[-1][calls[-1].index("--pull") + 1] == "never"
    else:
        with pytest.raises(connection.ConnectionFailure, match="loopback"): connection._compose(settings, "start")
        assert not any("up" in c for c in calls)


def test_missing_container_bindings_do_not_pass_empty_all(tmp_path, monkeypatch):
    path = tmp_path / "compose.yaml"; path.touch()
    def run(argv, **kwargs):
        output = "id" if "ps" in argv else json.dumps([{"Config": {"Labels": {
            "com.docker.compose.project": "hypoloom-institutional-browser", "com.docker.compose.service": "browser"}},
            "HostConfig": {"PortBindings": {}}}])
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(connection, "_run", run)
    with pytest.raises(connection.ConnectionFailure, match="ownership"):
        connection._compose({"compose_file": str(path), "ready_timeout": 3}, "stop")


def test_prepare_connection_failure_does_not_open_login_or_create_run(monkeypatch, tmp_path):
    from hypoloom.institutional_prepare import run
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hypoloom.institutional_prepare._observe", lambda *a: {"state": "acquisition_timeout"})
    parsed = build_parser().parse_args(["institutional", "prepare", "--institutional-browser-mode", "existing", "--provider", "IEEE"])
    assert run(parsed, input_fn=lambda *a: pytest.fail("requested login"), output=lambda *a: None) == 1
    assert list(tmp_path.iterdir()) == [tmp_path / "state"]
    assert not list((tmp_path / "state").iterdir())


def test_catalog_ignores_startup_without_effects(monkeypatch):
    from hypoloom.institutional_prepare import run
    monkeypatch.setattr(connection, "operate", lambda *a: pytest.fail("catalog started a connection"))
    parsed = build_parser().parse_args(["institutional", "prepare", "--list", "--institutional-browser-mode", "server"])
    assert run(parsed, output=lambda *a: None) == 0


def test_windows_connector_with_real_powershell():
    executable = os.environ.get("HYPOLOOM_TEST_PWSH") or shutil.which("pwsh")
    if not executable: pytest.skip("PowerShell parser/runtime not installed")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([executable, "-NoProfile", "-File", str(root / "tests/runtime/fixtures/institutional_connector_checks.ps1"),
        "-Connector", str(root / "hypoloom/connection_assets/institutional-connect.ps1")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.environ.get("HYPOLOOM_INSTITUTIONAL_BROWSER_TEST") != "1", reason="explicit real-browser opt-in required")
def test_real_systemd_cdp_reuse_restart_and_session_persistence(tmp_path, monkeypatch):
    """Real systemd/Chrome, headless fixture only; no claim about GUI visibility or SSO."""
    executable = shutil.which("google-chrome") or shutil.which("chromium")
    if not executable: pytest.skip("Chrome/Chromium required")
    status = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True)
    if status.returncode: pytest.skip("user systemd required")
    import shlex
    from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser
    wrapper = tmp_path / "headless fixture"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(executable) + ' --headless --no-sandbox --password-store=basic "$@"\n')
    wrapper.chmod(0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    selected = args("--institutional-browser-mode", "local", "--institutional-browser-executable", str(wrapper),
        "--institutional-browser-url", f"http://127.0.0.1:{port}", "--institutional-browser-profile", str(tmp_path / "profile"))
    monkeypatch.setenv("DISPLAY", ":headless-fixture-only")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    backend = PlaywrightInstitutionalBrowser(selected.institutional_browser_url)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            starts = list(workers.map(lambda _: connection.operate(selected, "start"), range(2)))
        assert sorted(item["status"] for item in starts) == ["reused", "started"], json.dumps(starts, indent=2)
        first = starts[0]
        unit = first["unit"]
        pid = connection._unit_state(unit)["MainPID"]
        assert connection.operate(selected, "start")["status"] == "reused"
        assert connection._unit_state(unit)["MainPID"] == pid
        context = backend._connect()
        context.add_cookies([{"name": "synthetic-fixture", "value": "persisted", "domain": "fixture.invalid", "path": "/", "expires": time.time() + 3600}])
        backend.close()
        assert connection.operate(selected, "status")["status"] == "ready"
        assert connection.operate(selected, "stop")["status"] == "stopped"
        restarted = connection.operate(selected, "start")
        assert restarted["status"] == "started", restarted
        assert connection._unit_state(unit)["MainPID"] != pid
        assert any(c["name"] == "synthetic-fixture" and c["value"] == "persisted" for c in backend._connect().cookies())
        backend.close()
    finally:
        backend.close()
        stopped = connection.operate(selected, "stop")
        assert stopped["status"] in {"stopped", "not_running"}, stopped
    assert not connection._occupied(selected.institutional_browser_url)
