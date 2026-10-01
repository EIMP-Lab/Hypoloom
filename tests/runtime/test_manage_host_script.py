from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
MANAGE_HOST = (
    REPOSITORY / ".claude" / "skills" / "autoresearch" / "scripts" / "manage_host.py"
)


@pytest.mark.parametrize("mode", ["local", "client", "server", "existing", None])
def test_desktop_environment_only_follows_explicit_local_browser(monkeypatch, mode):
    name = "hypoloom_test_desktop_environment"
    spec = importlib.util.spec_from_file_location(name, MANAGE_HOST)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        monkeypatch.setenv("DISPLAY", ":fixture")
        monkeypatch.setenv("XAUTHORITY", "/private/desktop auth")
        monkeypatch.setenv("UNRELATED_SENTINEL_SECRET", "must-not-pass")
        selected = module._desktop_environment(["--institutional-browser-mode", mode] if mode else [])
        assert "UNRELATED_SENTINEL_SECRET" not in selected
        if mode == "local":
            assert selected["DISPLAY"] == ":fixture" and selected["XAUTHORITY"] == "/private/desktop auth"
        else: assert selected == {}
    finally: sys.modules.pop(name, None)
RESULT_KEYS = {"status", "supervisor", "unit", "state_root", "control_socket"}


def _write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    path.chmod(0o755)


@dataclass(frozen=True)
class FakeSystemd:
    root: Path
    binary_directory: Path
    trace: Path
    units: Path
    ready_host: Path
    waiting_host: Path
    stale_socket_host: Path
    host_environment: Path
    systemd_run_mode: Path

    def environment(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": f"{self.binary_directory}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "XDG_RUNTIME_DIR": str(self.root / "runtime"),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/fake/user/bus",
            "LANG": "C.UTF-8",
            "UNRELATED_SENTINEL_SECRET": "unrelated-secret-must-not-reach-host",
            **extra,
        }

    def records(self) -> list[dict[str, object]]:
        if not self.trace.exists():
            return []
        return [
            json.loads(line)
            for line in self.trace.read_text(encoding="utf-8").splitlines()
        ]

    def expected_unit(self, state_root: Path, transport: str) -> str:
        escaped = str(state_root.resolve()).strip("/").replace("/", "-")
        if transport == "environment":
            return f"hypoloom-{escaped}.scope"
        return f"hypoloom@{escaped}.service"


@pytest.fixture
def short_state_parent() -> Iterator[Path]:
    # A pytest --basetemp plus its descriptive test leaf can exceed Linux's
    # AF_UNIX pathname limit. Keep only fake state/socket paths short; scripts,
    # captured environments and systemd traces stay under the test's tmp_path.
    with tempfile.TemporaryDirectory(prefix="pmh-", dir="/tmp") as directory:
        yield Path(directory)


@pytest.fixture
def fake_systemd(tmp_path: Path, short_state_parent: Path) -> FakeSystemd:
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    trace = tmp_path / "trace.jsonl"
    units = tmp_path / "units"
    units.mkdir()
    host_environment = tmp_path / "host-environment.json"
    systemd_run_mode = tmp_path / "systemd-run-mode"
    (short_state_parent / "home").mkdir()
    (short_state_parent / "runtime").mkdir()

    dispatcher = binary_directory / "fake-systemd"
    _write_executable(
        dispatcher,
        f"""\
        #!{sys.executable}
        import json
        import os
        import signal
        import subprocess
        import sys
        import time
        from pathlib import Path

        TRACE = Path({str(trace)!r})
        UNITS = Path({str(units)!r})
        SYSTEMD_RUN_MODE = Path({str(systemd_run_mode)!r})

        def record(tool, arguments):
            payload = {{
                "tool": tool,
                "argv": arguments,
                "environment": {{
                    name: os.environ.get(name)
                    for name in (
                        "ANTHROPIC_API_KEY",
                        "ANTHROPIC_BASE_URL",
                        "UNRELATED_SENTINEL_SECRET",
                        "HYPOLOOM_RUNTIME_FAMILY",
                        "PYTHONDONTWRITEBYTECODE",
                    )
                }},
                "environment_keys": sorted(os.environ),
                "pid": os.getpid(),
            }}
            with TRACE.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, sort_keys=True) + "\\n")

        def unit_path(unit):
            return UNITS / unit.replace("\\\\", "_")

        tool = Path(sys.argv[0]).name
        arguments = sys.argv[1:]
        record(tool, arguments)

        if tool == "systemd-escape":
            escaped = arguments[-1].strip("/").replace("/", "-")
            templates = [value.split("=", 1)[1] for value in arguments
                         if value.startswith("--template=")]
            print(templates[0].replace("@.", f"@{{escaped}}.")
                  if templates else escaped)
            raise SystemExit(0)

        if tool == "systemctl":
            command = arguments[1]
            if command == "show":
                unit = arguments[2]
                path = unit_path(unit)
                if unit.endswith(".scope") and not path.exists():
                    print("Unknown object", file=sys.stderr)
                    raise SystemExit(1)
                if path.exists():
                    print("LoadState=loaded")
                    print("ActiveState=active")
                else:
                    print("LoadState=not-found")
                    print("ActiveState=inactive")
                raise SystemExit(0)
            if command == "list-units":
                unit = arguments[-1]
                path = unit_path(unit)
                if (SYSTEMD_RUN_MODE.exists()
                        and SYSTEMD_RUN_MODE.read_text(encoding="utf-8") == "query-failure"):
                    print("Failed to connect to user manager", file=sys.stderr)
                    raise SystemExit(1)
                if path.exists():
                    print(f"{{unit}} loaded active running Fake transient unit")
                raise SystemExit(0)
            if command == "stop":
                unit = arguments[2]
                path = unit_path(unit)
                if path.exists():
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        os.kill(int(payload["pid"]), signal.SIGTERM)
                    except (KeyError, OSError, ValueError, json.JSONDecodeError):
                        pass
                    path.unlink(missing_ok=True)
                elif (SYSTEMD_RUN_MODE.exists()
                      and SYSTEMD_RUN_MODE.read_text(encoding="utf-8") == "never-register"):
                    raise SystemExit(5)
                raise SystemExit(0)
            raise SystemExit(5)

        if tool == "systemd-run":
            unit = next(value.split("=", 1)[1] for value in arguments
                        if value.startswith("--unit="))
            working_directory = next(
                value.split("=", 1)[1] for value in arguments
                if value.startswith("--working-directory=")
            )
            marker = arguments.index("--")
            host_argv = arguments[marker + 1:]
            path = unit_path(unit)
            if "--scope" in arguments:
                if (SYSTEMD_RUN_MODE.exists()
                        and SYSTEMD_RUN_MODE.read_text(encoding="utf-8") == "never-register"):
                    time.sleep(20)
                path.write_text(json.dumps({{"pid": os.getpid()}}), encoding="utf-8")
                os.chdir(working_directory)
                os.execve(host_argv[0], host_argv, dict(os.environ))
            child = subprocess.Popen(
                host_argv,
                cwd=working_directory,
                env=dict(os.environ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            path.write_text(json.dumps({{"pid": child.pid}}), encoding="utf-8")
            raise SystemExit(0)

        raise SystemExit(5)
        """,
    )
    for name in ("systemd-escape", "systemd-run", "systemctl"):
        (binary_directory / name).symlink_to(dispatcher.name)

    ready_host = tmp_path / "fake-python-ready"
    _write_executable(
        ready_host,
        f"""\
        #!{sys.executable}
        import json
        import os
        import socket
        import sys
        import time
        from pathlib import Path

        sys.path.insert(0, {str(REPOSITORY)!r})
        from hypoloom.control_protocol import (
            ControlRequest,
            ControlResponse,
            receive_message,
            send_message,
        )

        arguments = sys.argv[1:]
        state_root = Path(arguments[arguments.index("--state-root") + 1])
        Path({str(host_environment)!r}).write_text(json.dumps({{
            "api_key": os.environ.get("ANTHROPIC_API_KEY"),
            "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
            "unrelated": os.environ.get("UNRELATED_SENTINEL_SECRET"),
            "runtime_family": os.environ.get("HYPOLOOM_RUNTIME_FAMILY"),
            "dont_write_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"),
        }}, sort_keys=True), encoding="utf-8")
        state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        control.bind(str(state_root / "control.sock"))
        os.chmod(state_root / "control.sock", 0o600)
        control.listen(4)
        control.settimeout(0.1)
        print("fake Host diagnostic output", flush=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                peer, _ = control.accept()
            except TimeoutError:
                continue
            with peer:
                request = ControlRequest.from_mapping(receive_message(peer))
                response = ControlResponse.failure(
                    request.request_id, "rejected", "not_found", request.payload["run_id"]
                )
                send_message(peer, response.to_mapping())
        """,
    )
    waiting_host = tmp_path / "fake-python-waiting"
    _write_executable(
        waiting_host,
        f"""\
        #!{sys.executable}
        import json
        import os
        import time
        from pathlib import Path

        Path({str(host_environment)!r}).write_text(json.dumps({{
            "api_key": os.environ.get("ANTHROPIC_API_KEY"),
            "unrelated": os.environ.get("UNRELATED_SENTINEL_SECRET"),
        }}, sort_keys=True), encoding="utf-8")
        time.sleep(20)
        """,
    )
    stale_socket_host = tmp_path / "fake-python-stale-socket"
    _write_executable(
        stale_socket_host,
        f"""\
        #!{sys.executable}
        import os
        import socket
        import sys
        import time
        from pathlib import Path

        arguments = sys.argv[1:]
        state_root = Path(arguments[arguments.index("--state-root") + 1])
        state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        control.bind(str(state_root / "control.sock"))
        os.chmod(state_root / "control.sock", 0o600)
        control.close()
        time.sleep(20)
        """,
    )
    return FakeSystemd(
        root=short_state_parent,
        binary_directory=binary_directory,
        trace=trace,
        units=units,
        ready_host=ready_host,
        waiting_host=waiting_host,
        stale_socket_host=stale_socket_host,
        host_environment=host_environment,
        systemd_run_mode=systemd_run_mode,
    )


def _run(
    fake: FakeSystemd,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(MANAGE_HOST), *arguments),
        cwd=REPOSITORY,
        env=fake.environment(**(environment or {})),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _host_argv(
    executable: Path,
    state_root: Path,
    *,
    adapter: str,
    extra: tuple[str, ...] = (),
) -> tuple[str, ...]:
    return (
        str(executable),
        "-m",
        "hypoloom.cli",
        "serve",
        "--state-root",
        str(state_root),
        "--agent-adapter",
        adapter,
        "--exit-when-terminal",
        *extra,
    )


def _start(
    fake: FakeSystemd,
    state_root: Path,
    transport: str,
    host_argv: tuple[str, ...],
    *,
    timeout: str = "2",
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run(
        fake,
        "start",
        "--state-root",
        str(state_root),
        "--working-directory",
        str(REPOSITORY),
        "--ready-timeout",
        timeout,
        "--auth-transport",
        transport,
        "--",
        *host_argv,
        environment=environment,
    )


def _assert_allowlisted_result(
    completed: subprocess.CompletedProcess[str], status: str
) -> dict[str, str]:
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert set(result) == RESULT_KEYS
    assert result["status"] == status
    assert result["supervisor"] == "user-systemd"
    return result


def _stop(fake: FakeSystemd, state_root: Path, transport: str) -> dict[str, str]:
    completed = _run(
        fake,
        "stop",
        "--state-root",
        str(state_root),
        "--auth-transport",
        transport,
    )
    assert completed.returncode == 0, completed.stdout
    return _assert_allowlisted_result(completed, "stopped")


def test_settings_transport_starts_bounded_user_service_and_redacts_host_argv(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "settings-state"
    settings_secret = "/private/settings-secret-must-not-be-output.json"
    model_secret = "private-model-must-not-be-output[1m]"
    api_secret = "ambient-api-key-must-not-reach-settings-service"
    host_argv = _host_argv(
        fake_systemd.ready_host,
        state_root,
        adapter="claude",
        extra=(
            "--claude-executable",
            "/private/claude.exe",
            "--claude-settings",
            settings_secret,
            "--claude-model",
            model_secret,
        ),
    )

    completed = _start(
        fake_systemd,
        state_root,
        "settings",
        host_argv,
        environment={"ANTHROPIC_API_KEY": api_secret, "ANTHROPIC_BASE_URL": "https://ambient-must-not-pass.invalid"},
    )

    assert completed.returncode == 0, completed.stdout
    result = _assert_allowlisted_result(completed, "ready")
    expected_unit = fake_systemd.expected_unit(state_root, "settings")
    assert result["unit"] == expected_unit
    assert result["state_root"] == str(state_root.resolve())
    assert result["control_socket"] == str(state_root.resolve() / "control.sock")
    rendered = completed.stdout + completed.stderr
    for forbidden in (settings_secret, model_secret, api_secret, "/private/claude.exe"):
        assert forbidden not in rendered

    systemd_run = next(
        record for record in fake_systemd.records() if record["tool"] == "systemd-run"
    )
    arguments = systemd_run["argv"]
    assert "--scope" not in arguments
    assert "--no-block" in arguments
    assert "--service-type=exec" in arguments
    assert "--property=Restart=on-failure" in arguments
    assert "--property=RestartPreventExitStatus=2 78" in arguments
    assert "--property=StartLimitIntervalSec=30s" in arguments
    assert "--property=StartLimitBurst=3" in arguments
    assert "--property=StartLimitIntervalSec=infinity" not in arguments
    assert "--property=UMask=0077" in arguments
    assert "--property=KillMode=mixed" in arguments
    assert "--property=TimeoutStopSec=30s" in arguments
    assert arguments[arguments.index("--") + 1 :] == list(host_argv)
    assert systemd_run["environment"]["ANTHROPIC_API_KEY"] is None
    assert systemd_run["environment"]["ANTHROPIC_BASE_URL"] is None
    assert systemd_run["environment"]["UNRELATED_SENTINEL_SECRET"] is None
    assert systemd_run["environment"]["HYPOLOOM_RUNTIME_FAMILY"] == "hypoloom"
    assert systemd_run["environment"]["PYTHONDONTWRITEBYTECODE"] == "1"

    stopped = _stop(fake_systemd, state_root, "settings")
    assert stopped["unit"] == expected_unit


def test_none_transport_keeps_fake_host_on_managed_service(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "fake-state"
    host_argv = _host_argv(
        fake_systemd.ready_host, state_root, adapter="fake"
    )

    completed = _start(fake_systemd, state_root, "none", host_argv)

    assert completed.returncode == 0, completed.stdout
    result = _assert_allowlisted_result(completed, "ready")
    assert result["unit"] == fake_systemd.expected_unit(state_root, "none")
    run_record = next(
        record for record in fake_systemd.records() if record["tool"] == "systemd-run"
    )
    assert "--scope" not in run_record["argv"]
    _stop(fake_systemd, state_root, "none")


def test_reviewed_pool_and_retrieval_round_trip_as_one_literal_host_argv(fake_systemd: FakeSystemd) -> None:
    state_root = fake_systemd.root / "pool-state"
    pool_socket = fake_systemd.root / "operator pool" / "capacity.sock"
    pool_options = ("--gpu-pool-socket", str(pool_socket), "--gpu-pool-namespace", "private-root-a",
                    "--gpu-pool-server-uid", str(os.getuid()))
    retrieval_options = ("--literature-adapter", "openalex", "--openalex-api-key-file", "/private/openalex key",
                         "--web-search-fallback", "openalex", "--literature-pdf-fallback", "unpaywall",
                         "--unpaywall-email-file", "/private/contact mail", "--literature-timeout", "12",
                         "--literature-max-pdf-pages", "80")
    host_argv = _host_argv(fake_systemd.ready_host, state_root, adapter="fake", extra=pool_options + retrieval_options)
    completed = _start(fake_systemd, state_root, "none", host_argv)
    try:
        assert completed.returncode == 0, completed.stdout
        _assert_allowlisted_result(completed, "ready")
        launched = [record for record in fake_systemd.records() if record["tool"] == "systemd-run"]
        assert len(launched) == 1
        arguments = launched[0]["argv"]
        assert arguments[arguments.index("--") + 1:] == list(host_argv)
        assert not pool_socket.parent.exists(), "managed Host must not deploy or create its pool endpoint"
        assert "--allow-gpu" not in arguments
    finally:
        _stop(fake_systemd, state_root, "none")


@pytest.mark.parametrize("base_url", [None, "https://approved-gateway.invalid"])
def test_environment_transport_detaches_scope_with_only_required_secret(
    fake_systemd: FakeSystemd,
    base_url,
) -> None:
    state_root = fake_systemd.root / "environment-state"
    api_secret = "environment-api-key-must-not-be-output"
    model_secret = "environment-model-must-not-be-output"
    host_argv = _host_argv(
        fake_systemd.ready_host,
        state_root,
        adapter="claude",
        extra=(
            "--claude-executable",
            "/private/claude.exe",
            "--claude-model",
            model_secret,
        ),
    )

    completed = _start(
        fake_systemd,
        state_root,
        "environment",
        host_argv,
        environment={"ANTHROPIC_API_KEY": api_secret,
                     **({"ANTHROPIC_BASE_URL": base_url} if base_url else {})},
    )

    assert completed.returncode == 0, completed.stdout
    result = _assert_allowlisted_result(completed, "ready")
    expected_unit = fake_systemd.expected_unit(state_root, "environment")
    assert result["unit"] == expected_unit
    assert result["unit"].startswith("hypoloom-")
    assert "@" not in result["unit"]
    rendered = completed.stdout + completed.stderr
    assert api_secret not in rendered
    assert model_secret not in rendered

    systemd_run = next(
        record for record in fake_systemd.records() if record["tool"] == "systemd-run"
    )
    arguments = systemd_run["argv"]
    assert "--scope" in arguments
    assert "--no-block" not in arguments
    assert arguments[arguments.index("--") + 1 :] == list(host_argv)
    assert api_secret not in arguments
    assert systemd_run["environment"]["ANTHROPIC_API_KEY"] == api_secret
    assert systemd_run["environment"]["ANTHROPIC_BASE_URL"] == base_url
    assert systemd_run["environment"]["UNRELATED_SENTINEL_SECRET"] is None
    assert "UNRELATED_SENTINEL_SECRET" not in systemd_run["environment_keys"]
    assert any(
        record["tool"] == "systemd-escape"
        and record["argv"] == ["--path", str(state_root.resolve())]
        for record in fake_systemd.records()
    )
    systemctl_records = [
        record for record in fake_systemd.records() if record["tool"] == "systemctl"
    ]
    assert any(record["argv"][1] == "list-units" for record in systemctl_records)
    assert all(record["argv"][1] != "show" for record in systemctl_records)

    host_environment = json.loads(
        fake_systemd.host_environment.read_text(encoding="utf-8")
    )
    assert host_environment == {
        "api_key": api_secret,
        "base_url": base_url,
        "dont_write_bytecode": "1",
        "runtime_family": "hypoloom",
        "unrelated": None,
    }
    diagnostic_log = (
        state_root.parent / f".{state_root.name}.hypoloom-host.log"
    )
    assert diagnostic_log.is_file()
    assert diagnostic_log.stat().st_mode & 0o777 == 0o600
    assert "non-authority" in diagnostic_log.read_text(encoding="utf-8")
    _stop(fake_systemd, state_root, "environment")


def test_environment_transport_creates_missing_parent_before_diagnostic_log(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "m" / "p" / "s"
    assert not state_root.parent.exists()
    host_argv = _host_argv(
        fake_systemd.ready_host,
        state_root,
        adapter="claude",
        extra=(
            "--claude-executable",
            "/private/claude.exe",
            "--claude-model",
            "private-model",
        ),
    )

    completed = _start(
        fake_systemd,
        state_root,
        "environment",
        host_argv,
        environment={"ANTHROPIC_API_KEY": "private-api-key"},
    )

    assert completed.returncode == 0, completed.stdout
    _assert_allowlisted_result(completed, "ready")
    assert state_root.parent.is_dir()
    assert state_root.parent.stat().st_mode & 0o777 == 0o700
    assert state_root.is_dir()
    _stop(fake_systemd, state_root, "environment")


def test_unregistered_scope_timeout_reaps_its_exact_detached_launcher(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "never-registered-state"
    fake_systemd.systemd_run_mode.write_text("never-register", encoding="utf-8")
    host_argv = _host_argv(
        fake_systemd.ready_host,
        state_root,
        adapter="claude",
        extra=(
            "--claude-executable",
            "/private/claude.exe",
            "--claude-model",
            "private-model",
        ),
    )

    completed = _start(
        fake_systemd,
        state_root,
        "environment",
        host_argv,
        timeout="0.15",
        environment={"ANTHROPIC_API_KEY": "private-api-key"},
    )

    assert completed.returncode == 2
    result = _assert_allowlisted_result(completed, "host_ready_timeout")
    expected_unit = fake_systemd.expected_unit(state_root, "environment")
    assert result["unit"] == expected_unit
    records = fake_systemd.records()
    run_record = next(record for record in records if record["tool"] == "systemd-run")
    with pytest.raises(ProcessLookupError):
        os.kill(int(run_record["pid"]), 0)
    assert not (fake_systemd.units / expected_unit).exists()
    assert [
        record["argv"][2]
        for record in records
        if record["tool"] == "systemctl" and record["argv"][1] == "stop"
    ] == [expected_unit, expected_unit]


def test_readiness_timeout_stops_only_the_newly_derived_unit(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "timeout-state"
    host_argv = _host_argv(
        fake_systemd.waiting_host, state_root, adapter="fake"
    )

    completed = _start(
        fake_systemd, state_root, "none", host_argv, timeout="0.15"
    )

    assert completed.returncode == 2
    result = _assert_allowlisted_result(completed, "host_ready_timeout")
    expected_unit = fake_systemd.expected_unit(state_root, "none")
    assert result["unit"] == expected_unit
    stop_records = [
        record
        for record in fake_systemd.records()
        if record["tool"] == "systemctl" and record["argv"][1] == "stop"
    ]
    assert [record["argv"][2] for record in stop_records] == [expected_unit]


def test_stale_unix_socket_with_active_unit_is_not_ready(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "stale-socket-state"
    host_argv = _host_argv(
        fake_systemd.stale_socket_host, state_root, adapter="fake"
    )

    completed = _start(
        fake_systemd, state_root, "none", host_argv, timeout="0.2"
    )

    assert completed.returncode == 2
    result = _assert_allowlisted_result(completed, "host_ready_timeout")
    expected_unit = fake_systemd.expected_unit(state_root, "none")
    assert result["unit"] == expected_unit
    assert (state_root / "control.sock").is_socket()
    assert [
        record["argv"][2]
        for record in fake_systemd.records()
        if record["tool"] == "systemctl" and record["argv"][1] == "stop"
    ] == [expected_unit]


@pytest.mark.parametrize(
    "transport,argv_builder",
    [
        (
            "none",
            lambda fake, root: _host_argv(
                fake.ready_host,
                root,
                adapter="fake",
                extra=("--claude-model", "forbidden-model"),
            ),
        ),
        (
            "none",
            lambda fake, root: _host_argv(
                fake.ready_host, root, adapter="claude"
            ),
        ),
        (
            "settings",
            lambda fake, root: _host_argv(
                fake.ready_host, root, adapter="claude"
            ),
        ),
        (
            "environment",
            lambda fake, root: _host_argv(
                fake.ready_host,
                root,
                adapter="claude",
                extra=("--claude-settings", "/private/settings.json"),
            ),
        ),
        (
            "none",
            lambda fake, root: (
                "/bin/sh",
                "-m",
                "hypoloom.cli",
                "serve",
                "--state-root",
                str(root),
                "--agent-adapter",
                "fake",
            ),
        ),
        (
            "none",
            lambda fake, root: _host_argv(
                fake.ready_host, root.parent / "different-root", adapter="fake"
            ),
        ),
        (
            "none",
            lambda fake, root: _host_argv(
                fake.ready_host,
                root,
                adapter="fake",
                extra=("--allow-gpu",),
            ),
        ),
        (
            "none",
            lambda fake, root: _host_argv(
                fake.ready_host,
                root,
                adapter="fake",
                extra=("--allow-gpu=true",),
            ),
        ),
        (
            "none",
            lambda fake, root: _host_argv(
                fake.ready_host,
                root,
                adapter="fake",
                extra=("--cpu-only",),
            ),
        ),
        (
            "none",
            lambda fake, root: (
                str(fake.ready_host),
                "-m",
                "hypoloom.cli",
                "serve",
                "--state-root",
                str(root),
                "--agent-adapter",
                "fake",
            ),
        ),
    ],
)
def test_start_rejects_non_host_or_auth_mismatched_argv_before_systemd(
    fake_systemd: FakeSystemd,
    transport: str,
    argv_builder: object,
) -> None:
    state_root = fake_systemd.root / "rejected-state"
    host_argv = argv_builder(fake_systemd, state_root)  # type: ignore[operator]

    completed = _start(
        fake_systemd,
        state_root,
        transport,
        host_argv,
        environment={"ANTHROPIC_API_KEY": "must-not-be-output"},
    )

    assert completed.returncode == 2
    _assert_allowlisted_result(completed, "invalid_host_argv")
    assert fake_systemd.records() == []
    assert not state_root.exists()
    assert "must-not-be-output" not in completed.stdout + completed.stderr


def test_start_refuses_existing_root_without_contacting_systemd(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "existing-state"
    state_root.mkdir()
    host_argv = _host_argv(
        fake_systemd.ready_host, state_root, adapter="fake"
    )

    completed = _start(fake_systemd, state_root, "none", host_argv)

    assert completed.returncode == 2
    _assert_allowlisted_result(completed, "state_root_exists")
    assert fake_systemd.records() == []


def test_unit_list_manager_error_fails_closed_before_launch(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "manager-error-state"
    fake_systemd.systemd_run_mode.write_text("query-failure", encoding="utf-8")
    host_argv = _host_argv(
        fake_systemd.ready_host, state_root, adapter="fake"
    )

    completed = _start(fake_systemd, state_root, "none", host_argv)

    assert completed.returncode == 2
    _assert_allowlisted_result(completed, "supervisor_query_failed")
    assert not state_root.exists()
    records = fake_systemd.records()
    assert any(
        record["tool"] == "systemctl" and record["argv"][1] == "list-units"
        for record in records
    )
    assert all(record["tool"] != "systemd-run" for record in records)


def test_atomic_state_root_create_refuses_concurrent_creator(tmp_path: Path) -> None:
    module_name = "hypoloom_test_manage_host"
    specification = importlib.util.spec_from_file_location(module_name, MANAGE_HOST)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
        state_root = tmp_path / "parent" / "state"
        context = module._path_context(str(state_root), "none")
        module._prepare_state_parent(context)

        state_root.mkdir(mode=0o700)

        with pytest.raises(module.ManagedHostError) as caught:
            module._create_fresh_state_root(context)
        assert caught.value.status == "state_root_exists"
        assert state_root.is_dir()
    finally:
        sys.modules.pop(module_name, None)


def test_stop_recomputes_unit_and_rejects_caller_supplied_target(
    fake_systemd: FakeSystemd,
) -> None:
    state_root = fake_systemd.root / "exact-stop-state"
    malicious_unit = "unrelated-user-unit.service"

    rejected = _run(
        fake_systemd,
        "stop",
        "--state-root",
        str(state_root),
        "--auth-transport",
        "none",
        "--unit",
        malicious_unit,
    )

    assert rejected.returncode == 2
    _assert_allowlisted_result(rejected, "invalid_arguments")
    assert fake_systemd.records() == []
    assert malicious_unit not in rejected.stdout + rejected.stderr

    stopped = _stop(fake_systemd, state_root, "none")
    expected_unit = fake_systemd.expected_unit(state_root, "none")
    assert stopped["unit"] == expected_unit
    records = fake_systemd.records()
    assert any(
        record["tool"] == "systemd-escape"
        and record["argv"]
        == [
            "--path",
            "--template=hypoloom@.service",
            str(state_root.resolve()),
        ]
        for record in records
    )
    assert [
        record["argv"]
        for record in records
        if record["tool"] == "systemctl" and record["argv"][1] == "stop"
    ] == [["--user", "stop", expected_unit]]
