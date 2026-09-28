from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
MANAGE_POOL = (
    REPOSITORY / ".claude" / "skills" / "autoresearch" / "scripts" / "manage_gpu_pool.py"
)
GPU_ALPHA = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
GPU_BETA = "GPU-ffffffff-0000-1111-2222-333333333333"
GPU_FOREIGN = "GPU-99999999-8888-7777-6666-555555555555"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    path.chmod(0o755)


@dataclass(frozen=True)
class FakeSystemd:
    root: Path
    repository: Path
    binary_directory: Path
    trace: Path
    units: Path
    pool_environment: Path
    payload_override: Path
    devices_override: Path
    fake_pool_server: Path
    fake_pool_waiting: Path

    def environment(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": f"{self.binary_directory}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "XDG_RUNTIME_DIR": str(self.root / "runtime"),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/fake/user/bus",
            "LANG": "C.UTF-8",
            "UNRELATED_SENTINEL_SECRET": "unrelated-secret-must-not-reach-pool",
            **extra,
        }

    def records(self, tool: str | None = None) -> list[dict[str, object]]:
        if not self.trace.exists():
            return []
        entries = [
            json.loads(line)
            for line in self.trace.read_text(encoding="utf-8").splitlines()
        ]
        return [entry for entry in entries if tool is None or entry["tool"] == tool]

    def expected_unit(self, pool_root: Path) -> str:
        escaped = str(pool_root).strip("/").replace("/", "-")
        return f"hypoloom-gpu-pool@{escaped}.service"

    def register_unit(self, unit: str, pid: int = 0) -> None:
        (self.units / unit.replace("\\\\", "_")).write_text(
            json.dumps({"pid": pid}), encoding="utf-8"
        )

    def pool_root(self, name: str = "shared") -> Path:
        return self.repository / ".local" / "gpu-pools" / name

    def cleanup(self) -> None:
        if not self.units.exists():
            return
        for unit_file in self.units.iterdir():
            try:
                pid = int(json.loads(unit_file.read_text(encoding="utf-8"))["pid"])
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                continue
            if pid > 1:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass


@pytest.fixture
def fake_systemd(tmp_path: Path) -> Iterator[FakeSystemd]:
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    trace = tmp_path / "trace.jsonl"
    units = tmp_path / "units"
    units.mkdir()
    pool_environment = tmp_path / "pool-environment.json"
    payload_override = tmp_path / "payload-override"
    devices_override = tmp_path / "devices-override.json"
    # Keep the fake repository short: the pool socket is an AF_UNIX pathname.
    short_root = Path(tempfile.mkdtemp(prefix="pmg-", dir="/tmp"))
    repository = short_root / "repo"
    repository.mkdir()
    (short_root / "home").mkdir()
    (short_root / "runtime").mkdir()

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
        from pathlib import Path

        TRACE = Path({str(trace)!r})
        UNITS = Path({str(units)!r})
        PAYLOAD_OVERRIDE = Path({str(payload_override)!r})

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
            if command == "list-units":
                unit = arguments[-1]
                if unit_path(unit).exists():
                    print(f"{{unit}} loaded active running Fake transient unit")
                raise SystemExit(0)
            if command == "stop":
                unit = arguments[2]
                path = unit_path(unit)
                if path.exists():
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        if int(payload["pid"]) > 1:
                            os.kill(int(payload["pid"]), signal.SIGTERM)
                    except (KeyError, OSError, ValueError, json.JSONDecodeError):
                        pass
                    path.unlink(missing_ok=True)
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
            serve_argv = arguments[marker + 1:]
            payload = serve_argv
            if PAYLOAD_OVERRIDE.exists():
                payload = [PAYLOAD_OVERRIDE.read_text(encoding="utf-8").strip(),
                           *serve_argv]
            child = subprocess.Popen(
                payload,
                cwd=working_directory,
                env=dict(os.environ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            unit_path(unit).write_text(json.dumps({{"pid": child.pid}}), encoding="utf-8")
            raise SystemExit(0)

        raise SystemExit(5)
        """,
    )
    for name in ("systemd-escape", "systemd-run", "systemctl"):
        (binary_directory / name).symlink_to(dispatcher.name)

    fake_pool_server = tmp_path / "fake-pool-server"
    _write_executable(
        fake_pool_server,
        f"""\
        #!{sys.executable}
        import json
        import os
        import socket
        import sys
        import time
        from pathlib import Path

        arguments = sys.argv[1:]

        def option(name):
            return arguments[arguments.index(name) + 1]

        pool_root = Path(option("--pool-root"))
        socket_path = Path(option("--socket-path"))
        devices = option("--member").split("=", 1)[1].split(",")
        override = Path({str(devices_override)!r})
        if override.exists():
            devices = json.loads(override.read_text(encoding="utf-8"))
        Path({str(pool_environment)!r}).write_text(json.dumps({{
            "api_key": os.environ.get("ANTHROPIC_API_KEY"),
            "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
            "unrelated": os.environ.get("UNRELATED_SENTINEL_SECRET"),
            "runtime_family": os.environ.get("HYPOLOOM_RUNTIME_FAMILY"),
            "dont_write_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"),
        }}, sort_keys=True), encoding="utf-8")
        if not pool_root.is_dir():
            raise SystemExit(3)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(4)
        server.settimeout(0.1)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                peer, _ = server.accept()
            except TimeoutError:
                continue
            with peer:
                raw = b""
                peer.settimeout(2)
                try:
                    while b"\\n" not in raw:
                        part = peer.recv(4096)
                        if not part:
                            raise SystemExit(4)
                        raw += part
                    result = {{"devices": [{{"gpu_uuid": device}} for device in devices],
                               "capabilities": [] if (pool_root / "legacy-fixture").exists() else ["resource_replanning/1"]}}
                    peer.sendall(json.dumps({{"ok": True, "result": result}}).encode() + b"\\n")
                except OSError:
                    pass
        """,
    )
    fake_pool_waiting = tmp_path / "fake-pool-waiting"
    _write_executable(
        fake_pool_waiting,
        f"""\
        #!{sys.executable}
        import time

        time.sleep(20)
        """,
    )
    fake = FakeSystemd(
        root=short_root,
        repository=repository,
        binary_directory=binary_directory,
        trace=trace,
        units=units,
        pool_environment=pool_environment,
        payload_override=payload_override,
        devices_override=devices_override,
        fake_pool_server=fake_pool_server,
        fake_pool_waiting=fake_pool_waiting,
    )
    try:
        yield fake
    finally:
        fake.cleanup()


def _run(
    fake: FakeSystemd,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(MANAGE_POOL), *arguments),
        cwd=REPOSITORY,
        env=fake.environment(**(environment or {})),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _parsed(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert completed.stderr == "", completed.stderr
    return json.loads(completed.stdout)


@contextmanager
def _direct_pool_server(
    fake: FakeSystemd, pool_root: Path, member: str
) -> Iterator[subprocess.Popen[bytes]]:
    pool_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    process = subprocess.Popen(
        (
            str(fake.fake_pool_server),
            "--pool-root",
            str(pool_root),
            "--socket-path",
            str(pool_root / "control.sock"),
            "--member",
            member,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while not (pool_root / "control.sock").exists():
        if process.poll() is not None or time.monotonic() > deadline:
            process.kill()
            raise AssertionError("fake pool server did not bind its socket")
        time.sleep(0.02)
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _create_run_database(
    repository: Path,
    run_id: str,
    created_at: str,
    gpu_pool: dict[str, object] | None,
) -> Path:
    state_root = repository / "state" / run_id
    state_root.mkdir(parents=True)
    payload: dict[str, object] = {"execution": {}}
    if gpu_pool is not None:
        payload["gpu_pool"] = gpu_pool
    database = state_root / "state.sqlite"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE workflow_transitions ("
            "workflow_id TEXT, revision INTEGER, run_id TEXT, workflow_kind TEXT, "
            "state TEXT, payload_json TEXT, created_at TEXT)"
        )
        db.execute(
            "INSERT INTO workflow_transitions VALUES (?, 0, ?, 'RUN', 'CREATED', ?, ?)",
            (run_id, run_id, json.dumps(payload), created_at),
        )
    return database


def _ensure_arguments(fake: FakeSystemd, member: str, reserve_mib: int = 4096) -> tuple[str, ...]:
    pool_root = fake.pool_root()
    return (
        "ensure",
        "--repository",
        str(fake.repository),
        "--pool-root",
        str(pool_root),
        "--socket-path",
        str(pool_root / "control.sock"),
        "--member",
        member,
        "--reserve-mib",
        str(reserve_mib),
    )


def test_status_reports_absent_project_pool_and_recalls_only_pooled_runs(
    fake_systemd: FakeSystemd,
) -> None:
    binding = {
        "socket_path": "/foreign/project/gpu-pools/shared-x4/control.sock",
        "namespace": "topic-202609170117",
        "server_uid": os.geteuid(),
    }
    pooled = _create_run_database(
        fake_systemd.repository, "topic-202609170117", "2026-09-17T01:39:45.816Z", binding
    )
    _create_run_database(
        fake_systemd.repository, "topic-202609160508", "2026-09-16T05:08:00.000Z", None
    )
    before = pooled.read_bytes()

    completed = _run(fake_systemd, "status", "--repository", str(fake_systemd.repository))

    assert completed.returncode == 0, completed.stdout
    result = _parsed(completed)
    assert result["status"] == "ok"
    assert result["supervisor"] == "user-systemd"
    pools = result["project_pools"]
    assert isinstance(pools, list) and len(pools) == 1
    assert pools[0]["pool_root"] == str(fake_systemd.pool_root())
    assert pools[0]["socket_path"] == str(fake_systemd.pool_root() / "control.sock")
    assert pools[0]["state"] == "absent"
    assert pools[0]["devices"] == []
    recalled = result["recalled"]
    assert isinstance(recalled, list) and len(recalled) == 1
    entry = recalled[0]
    assert entry["run_id"] == "topic-202609170117"
    assert entry["socket_path"] == binding["socket_path"]
    assert entry["namespace"] == "topic-202609170117"
    assert entry["server_uid"] == binding["server_uid"]
    assert entry["state"] == "absent"
    assert result["state_roots_scanned"] == 2
    assert result["state_roots_unreadable"] == 0
    assert pooled.read_bytes() == before


def test_status_recalls_newest_first_and_dedupes_identical_bindings(
    fake_systemd: FakeSystemd,
) -> None:
    older = {
        "socket_path": "/foreign/pools/a/control.sock",
        "namespace": "topic-a",
        "server_uid": os.geteuid(),
    }
    newer = {
        "socket_path": "/foreign/pools/b/control.sock",
        "namespace": "topic-b",
        "server_uid": os.geteuid(),
    }
    _create_run_database(fake_systemd.repository, "topic-202609150849", "2026-09-15T00:59:00.000Z", older)
    _create_run_database(fake_systemd.repository, "topic-202609161513", "2026-09-16T16:21:00.000Z", newer)
    _create_run_database(fake_systemd.repository, "topic-202609170100", "2026-09-17T01:00:00.000Z", newer)

    completed = _run(fake_systemd, "status", "--repository", str(fake_systemd.repository))

    assert completed.returncode == 0
    recalled = _parsed(completed)["recalled"]
    assert [entry["run_id"] for entry in recalled] == [
        "topic-202609170100",
        "topic-202609150849",
    ]


def test_status_detects_live_project_pool(fake_systemd: FakeSystemd) -> None:
    pool_root = fake_systemd.pool_root()
    member = f"{os.geteuid()}={GPU_ALPHA},{GPU_BETA}"
    with _direct_pool_server(fake_systemd, pool_root, member):
        completed = _run(
            fake_systemd, "status", "--repository", str(fake_systemd.repository)
        )
    assert completed.returncode == 0
    pools = _parsed(completed)["project_pools"]
    assert pools[0]["state"] == "live"
    assert pools[0]["devices"] == sorted([GPU_ALPHA, GPU_BETA])


def test_status_marks_unanswered_socket_stale(fake_systemd: FakeSystemd) -> None:
    pool_root = fake_systemd.pool_root()
    pool_root.mkdir(mode=0o700, parents=True)
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.bind(str(pool_root / "control.sock"))
    probe.close()

    completed = _run(fake_systemd, "status", "--repository", str(fake_systemd.repository))

    assert completed.returncode == 0
    assert _parsed(completed)["project_pools"][0]["state"] == "stale"


def test_status_rejects_relative_repository(fake_systemd: FakeSystemd) -> None:
    completed = _run(fake_systemd, "status", "--repository", "relative/repo")

    assert completed.returncode == 2
    assert _parsed(completed)["status"] == "repository_invalid"


def test_ensure_creates_reviewed_pool_service(fake_systemd: FakeSystemd) -> None:
    fake_systemd.payload_override.write_text(
        str(fake_systemd.fake_pool_server), encoding="utf-8"
    )
    member = f"{os.geteuid()}={GPU_ALPHA},{GPU_BETA}"

    completed = _run(
        fake_systemd,
        *_ensure_arguments(fake_systemd, member),
        environment={"ANTHROPIC_API_KEY": "sentinel-must-not-leak"},
    )

    assert completed.returncode == 0, completed.stdout
    result = _parsed(completed)
    assert result["status"] == "ready"
    assert result["supervisor"] == "user-systemd"
    assert result["unit"] == fake_systemd.expected_unit(fake_systemd.pool_root())
    assert result["pool_root"] == str(fake_systemd.pool_root())
    assert result["socket_path"] == str(fake_systemd.pool_root() / "control.sock")
    assert result["members"] == {str(os.geteuid()): [GPU_ALPHA, GPU_BETA]}
    assert result["reserve_mib"] == 4096
    assert result["devices"] == sorted([GPU_ALPHA, GPU_BETA])

    runs = fake_systemd.records("systemd-run")
    assert len(runs) == 1
    argv = runs[0]["argv"]
    assert f"--unit={result['unit']}" in argv
    assert "--property=Restart=on-failure" in argv
    assert "--property=RestartPreventExitStatus=2 78" in argv
    assert "--property=UMask=0077" in argv
    assert f"--setenv=PYTHONPATH={fake_systemd.repository}" in argv
    marker = argv.index("--")
    serve_argv = argv[marker + 1:]
    assert serve_argv[1:5] == ["-B", "-m", "hypoloom.cli", "gpu-pool-serve"]
    assert serve_argv.count("--member") == 1
    assert member in serve_argv
    assert str(fake_systemd.pool_root()) in serve_argv
    for record in fake_systemd.records():
        assert record["environment"]["ANTHROPIC_API_KEY"] is None
        assert record["environment"]["UNRELATED_SENTINEL_SECRET"] is None
    captured = json.loads(fake_systemd.pool_environment.read_text(encoding="utf-8"))
    assert captured["api_key"] is None
    assert captured["unrelated"] is None
    assert captured["runtime_family"] == "hypoloom"
    metadata = os.stat(fake_systemd.pool_root())
    assert metadata.st_mode & 0o777 == 0o700


def test_ensure_reuses_matching_live_pool_without_relaunch(
    fake_systemd: FakeSystemd,
) -> None:
    fake_systemd.payload_override.write_text(
        str(fake_systemd.fake_pool_server), encoding="utf-8"
    )
    member = f"{os.geteuid()}={GPU_ALPHA}"
    first = _run(fake_systemd, *_ensure_arguments(fake_systemd, member))
    assert first.returncode == 0

    second = _run(fake_systemd, *_ensure_arguments(fake_systemd, member))

    assert second.returncode == 0
    assert _parsed(second)["status"] == "reused"
    assert len(fake_systemd.records("systemd-run")) == 1


def test_ensure_refuses_foreign_pool_root(fake_systemd: FakeSystemd) -> None:
    foreign = fake_systemd.root / "elsewhere" / "shared"
    completed = _run(
        fake_systemd,
        "ensure",
        "--repository",
        str(fake_systemd.repository),
        "--pool-root",
        str(foreign),
        "--socket-path",
        str(foreign / "control.sock"),
        "--member",
        f"{os.geteuid()}={GPU_ALPHA}",
    )

    assert completed.returncode == 2
    assert _parsed(completed)["status"] == "foreign_pool_root"
    assert fake_systemd.records() == []


def test_ensure_refuses_busy_socket_without_managed_unit(
    fake_systemd: FakeSystemd,
) -> None:
    pool_root = fake_systemd.pool_root()
    member = f"{os.geteuid()}={GPU_ALPHA}"
    with _direct_pool_server(fake_systemd, pool_root, member):
        completed = _run(fake_systemd, *_ensure_arguments(fake_systemd, member))

    assert completed.returncode == 2
    assert _parsed(completed)["status"] == "service_busy"
    assert fake_systemd.records("systemd-run") == []


def test_ensure_refuses_active_unit_without_service(fake_systemd: FakeSystemd) -> None:
    unit = fake_systemd.expected_unit(fake_systemd.pool_root())
    fake_systemd.register_unit(unit)

    completed = _run(
        fake_systemd, *_ensure_arguments(fake_systemd, f"{os.geteuid()}={GPU_ALPHA}")
    )

    assert completed.returncode == 2
    assert _parsed(completed)["status"] == "pool_conflict"
    assert fake_systemd.records("systemd-run") == []


def test_ensure_refuses_device_mismatch_on_active_unit(
    fake_systemd: FakeSystemd,
) -> None:
    pool_root = fake_systemd.pool_root()
    fake_systemd.devices_override.write_text(json.dumps([GPU_FOREIGN]), encoding="utf-8")
    unit = fake_systemd.expected_unit(pool_root)
    with _direct_pool_server(fake_systemd, pool_root, f"{os.geteuid()}={GPU_ALPHA}"):
        fake_systemd.register_unit(unit)
        completed = _run(
            fake_systemd, *_ensure_arguments(fake_systemd, f"{os.geteuid()}={GPU_ALPHA}")
        )

    assert completed.returncode == 2
    assert _parsed(completed)["status"] == "pool_conflict"
    assert fake_systemd.records("systemd-run") == []


def test_ensure_ready_timeout_stops_new_unit(fake_systemd: FakeSystemd) -> None:
    fake_systemd.payload_override.write_text(
        str(fake_systemd.fake_pool_waiting), encoding="utf-8"
    )

    started = time.monotonic()
    completed = _run(
        fake_systemd,
        *_ensure_arguments(fake_systemd, f"{os.geteuid()}={GPU_ALPHA}"),
        "--ready-timeout",
        "1.0",
    )
    elapsed = time.monotonic() - started

    assert completed.returncode == 2
    assert _parsed(completed)["status"] == "pool_ready_timeout"
    assert elapsed < 10
    stops = [record for record in fake_systemd.records("systemctl") if "stop" in record["argv"]]
    assert len(stops) == 1


def test_ensure_validates_member_uid_uuid_and_reserve(fake_systemd: FakeSystemd) -> None:
    foreign_uid = _run(
        fake_systemd, *_ensure_arguments(fake_systemd, f"{os.geteuid() + 1}={GPU_ALPHA}")
    )
    assert foreign_uid.returncode == 2
    assert _parsed(foreign_uid)["status"] == "member_uid_mismatch"

    invalid_uuid = _run(
        fake_systemd, *_ensure_arguments(fake_systemd, f"{os.geteuid()}=not-a-uuid")
    )
    assert invalid_uuid.returncode == 2
    assert _parsed(invalid_uuid)["status"] == "member_invalid"

    negative_reserve = _run(
        fake_systemd, *_ensure_arguments(fake_systemd, f"{os.geteuid()}={GPU_ALPHA}", -1)
    )
    assert negative_reserve.returncode == 2
    assert _parsed(negative_reserve)["status"] == "reserve_invalid"
    assert fake_systemd.records() == []


def test_ensure_reuses_selected_subset_and_refuses_legacy_without_stopping(fake_systemd):
    fake_systemd.payload_override.write_text(str(fake_systemd.fake_pool_server))
    both = f"{os.geteuid()}={GPU_ALPHA},{GPU_BETA}"
    assert _run(fake_systemd, *_ensure_arguments(fake_systemd, both)).returncode == 0
    subset = _run(fake_systemd, *_ensure_arguments(fake_systemd, f"{os.geteuid()}={GPU_BETA}"))
    assert subset.returncode == 0 and _parsed(subset)["status"] == "reused"
    (fake_systemd.pool_root() / "legacy-fixture").touch()
    rejected = _run(fake_systemd, *_ensure_arguments(fake_systemd, both))
    assert rejected.returncode != 0
    assert _parsed(rejected)["status"] == "incompatible_pool"
    assert len(fake_systemd.records("systemd-run")) == 1
    assert not any("stop" in row["argv"] for row in fake_systemd.records("systemctl"))


def test_ensure_does_not_reconfigure_a_stopped_durable_pool(fake_systemd):
    root = fake_systemd.repository / ".local/gpu-pools/shared"
    root.mkdir(parents=True)
    (root / "pool.sqlite3").write_bytes(b"existing owner, not inspected as empty")
    result = _parsed(_run(fake_systemd, *_ensure_arguments(fake_systemd, f"{os.geteuid()}={GPU_ALPHA}")))
    assert result["status"] == "pool_recovery_required"
    assert (root / "pool.sqlite3").read_bytes() == b"existing owner, not inspected as empty"
