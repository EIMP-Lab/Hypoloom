"""Detect or create the node-local project GPU pool through the user systemd manager.

This helper deliberately has no durable authority of its own.  Detection is
read-only; creation is bounded to a reviewed pool root beneath the
repository's ``.local/gpu-pools/`` and derives its systemd unit from the
absolute pool-root path. Replacement requires an exact reviewed action and
a quiescent project scope; ordinary ensure retains its no-replacement contract.
"""

from __future__ import annotations

import argparse
import fcntl
from contextvars import ContextVar
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import closing, ExitStack, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import quote


from .gpu_pool import PoolError
from .gpu_pool_client import PoolClient, validate_pool_snapshot
from .compute_resources import validate_gpu_uuid
from .agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION as CURRENT_ACI
from .gpu_pool_lifecycle import directory_lock, inspect_dependencies

_SUPERVISOR = "user-systemd"
_SERVICE_TEMPLATE = "hypoloom-gpu-pool@.service"
_POOLS_DIRECTORY = Path(".local") / "gpu-pools"
_DEFAULT_POOL_NAME = "shared"
_SOCKET_NAME = "control.sock"
_MAX_READY_TIMEOUT_SECONDS = 60.0
_COMMAND_TIMEOUT_SECONDS = 10.0
_POLL_SECONDS = 0.05
_PROBE_TIMEOUT_SECONDS = 2.0
_DEADLINE = ContextVar("managed_pool_deadline", default=None)
_ACTIVE_STATES = frozenset({"active", "activating", "reloading"})
_LOCALE_VARIABLES = (
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_ADDRESS",
    "LC_COLLATE",
    "LC_CTYPE",
    "LC_IDENTIFICATION",
    "LC_MEASUREMENT",
    "LC_MESSAGES",
    "LC_MONETARY",
    "LC_NAME",
    "LC_NUMERIC",
    "LC_PAPER",
    "LC_TELEPHONE",
    "LC_TIME",
)


class ManagedPoolError(RuntimeError):
    """A fixed, non-sensitive helper failure."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status
        self.context: PoolPlan | None = None
        self.effects: dict = {}


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep invalid input from being interpolated into argparse diagnostics."""

    def error(self, message: str) -> None:  # noqa: ARG002 - argparse API
        raise ManagedPoolError("invalid_arguments")


@dataclass(frozen=True)
class PoolPlan:
    repository: Path
    pool_root: Path
    socket_path: Path
    members: tuple[tuple[int, tuple[str, ...]], ...]
    reserve_mib: int
    unit: str = ""

    def result(self, status_value: str) -> dict[str, object]:
        return {
            "status": status_value,
            "supervisor": _SUPERVISOR,
            "unit": self.unit,
            "pool_root": str(self.pool_root),
            "socket_path": str(self.socket_path),
            "members": {str(uid): list(devices) for uid, devices in self.members},
            "reserve_mib": self.reserve_mib,
        }


@dataclass(frozen=True)
class Tools:
    systemd_escape: str
    systemd_run: str
    systemctl: str


def _resolve_repository(raw_repository: str) -> Path:
    candidate = Path(raw_repository)
    if not candidate.is_absolute():
        raise ManagedPoolError("repository_invalid")
    try:
        repository = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManagedPoolError("repository_invalid") from exc
    if not repository.is_dir():
        raise ManagedPoolError("repository_invalid")
    return repository


def _pools_directory(repository: Path) -> Path:
    return repository / _POOLS_DIRECTORY


def _pool_plan(
    repository: Path,
    raw_pool_root: str,
    raw_socket_path: str,
    raw_members: Sequence[str],
    reserve_mib: int,
) -> PoolPlan:
    pool_root_candidate = Path(raw_pool_root)
    socket_candidate = Path(raw_socket_path)
    if not pool_root_candidate.is_absolute() or not socket_candidate.is_absolute():
        raise ManagedPoolError("pool_root_invalid")
    try:
        pool_root = pool_root_candidate.resolve(strict=False)
        socket_path = socket_candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ManagedPoolError("pool_root_invalid") from exc
    if pool_root.parent != _pools_directory(repository) or not pool_root.name:
        raise ManagedPoolError("foreign_pool_root")
    if socket_path.parent != pool_root or socket_path.name != _SOCKET_NAME:
        raise ManagedPoolError("socket_path_invalid")
    if reserve_mib < 0:
        raise ManagedPoolError("reserve_invalid")
    members: dict[int, tuple[str, ...]] = {}
    for raw_member in raw_members:
        uid_text, separator, devices_text = raw_member.partition("=")
        if not separator or re.fullmatch(r"[0-9]+", uid_text) is None:
            raise ManagedPoolError("member_invalid")
        uid = int(uid_text)
        if uid in members:
            raise ManagedPoolError("member_invalid")
        if uid != os.geteuid():
            raise ManagedPoolError("member_uid_mismatch")
        devices = tuple(devices_text.split(","))
        try:
            for device in devices:
                validate_gpu_uuid(device)
        except ValueError as exc:
            raise ManagedPoolError("member_invalid") from exc
        if len(devices) != len(set(devices)):
            raise ManagedPoolError("member_invalid")
        members[uid] = tuple(sorted(devices))
    if not members:
        raise ManagedPoolError("member_invalid")
    return PoolPlan(
        repository=repository,
        pool_root=pool_root,
        socket_path=socket_path,
        members=tuple(sorted(members.items())),
        reserve_mib=reserve_mib,
    )


def _validate_ready_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0 or value > _MAX_READY_TIMEOUT_SECONDS:
        raise ManagedPoolError("ready_timeout_invalid")
    return value


def _minimal_environment() -> dict[str, str]:
    environment: dict[str, str] = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HYPOLOOM_RUNTIME_FAMILY": "hypoloom",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in (
        "HOME",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        *_LOCALE_VARIABLES,
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _find_tools() -> Tools:
    if not sys.platform.startswith("linux"):
        raise ManagedPoolError("linux_user_systemd_required")
    resolved = {
        name: shutil.which(name) for name in ("systemd-escape", "systemd-run", "systemctl")
    }
    if any(value is None for value in resolved.values()):
        raise ManagedPoolError("user_systemd_tools_unavailable")
    return Tools(
        systemd_escape=str(resolved["systemd-escape"]),
        systemd_run=str(resolved["systemd-run"]),
        systemctl=str(resolved["systemctl"]),
    )


def _quiet_run(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            tuple(arguments),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=_remaining(_COMMAND_TIMEOUT_SECONDS),
        )
    except subprocess.TimeoutExpired as exc:
        deadline = _DEADLINE.get()
        status = "pool_ready_timeout" if deadline is not None and time.monotonic() >= deadline else "supervisor_command_failed"
        raise ManagedPoolError(status) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManagedPoolError("supervisor_command_failed") from exc


def _remaining(limit: float) -> float:
    deadline = _DEADLINE.get()
    remaining = limit if deadline is None else min(limit, deadline - time.monotonic())
    if remaining <= 0:
        raise ManagedPoolError("pool_ready_timeout")
    return remaining


def _derive_unit(plan: PoolPlan, tools: Tools, environment: Mapping[str, str]) -> PoolPlan:
    completed = _quiet_run(
        (
            tools.systemd_escape,
            "--path",
            f"--template={_SERVICE_TEMPLATE}",
            str(plan.pool_root),
        ),
        environment=environment,
    )
    if completed.returncode != 0:
        raise ManagedPoolError("unit_name_failed")
    unit = completed.stdout.strip()
    if (
        not unit
        or not unit.startswith("hypoloom-gpu-pool@")
        or not unit.endswith(".service")
        or len(unit) > 255
        or "/" in unit
        or not re.fullmatch(r"[A-Za-z0-9@_.:\\-]+", unit)
    ):
        raise ManagedPoolError("unit_name_failed")
    return replace(plan, unit=unit)


def _unit_properties(
    plan: PoolPlan, tools: Tools, environment: Mapping[str, str]
) -> dict[str, str]:
    completed = _quiet_run(
        (
            tools.systemctl,
            "--user",
            "list-units",
            "--all",
            "--full",
            "--plain",
            "--no-legend",
            "--no-pager",
            plan.unit,
        ),
        environment=environment,
    )
    if completed.returncode != 0:
        raise ManagedPoolError("supervisor_query_failed")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {"LoadState": "not-found", "ActiveState": "inactive"}
    if len(lines) != 1:
        raise ManagedPoolError("supervisor_query_failed")
    fields = lines[0].split(maxsplit=4)
    if len(fields) < 4 or fields[0] != plan.unit:
        raise ManagedPoolError("supervisor_query_failed")
    return {"LoadState": fields[1], "ActiveState": fields[2]}


def _stop_unit(plan: PoolPlan, tools: Tools, environment: Mapping[str, str]) -> bool:
    completed = _quiet_run(
        (tools.systemctl, "--user", "stop", plan.unit),
        environment=environment,
    )
    return completed.returncode == 0


def _socket_state(path: Path) -> str:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "invalid"
    return "socket" if stat.S_ISSOCK(metadata.st_mode) else "invalid"


def _probe_snapshot(socket_path: Path, timeout: float) -> tuple[str, dict | None]:
    try:
        result = PoolClient(socket_path, timeout_seconds=_remaining(timeout),
                            expected_server_uid=os.geteuid()).request("snapshot", {})
    except PoolError as exc:
        return ("unauthorized", None) if exc.code in {"unauthorized", "unauthorized_server"} else ("unavailable", None)
    except (OSError, ValueError):
        return ("unavailable", None)
    if not isinstance(result.get("devices"), list):
        return ("unavailable", None)
    return ("live", result)


def _snapshot_uuids(snapshot: dict) -> tuple[str, ...] | None:
    devices = snapshot.get("devices")
    if not isinstance(devices, list):
        return None
    uuids: list[str] = []
    for device in devices:
        if not isinstance(device, dict) or not isinstance(device.get("gpu_uuid"), str):
            return None
        uuids.append(device["gpu_uuid"])
    return tuple(sorted(uuids))


def _endpoint_entry(socket_path: Path, pool_root: Path | None) -> dict[str, object]:
    entry: dict[str, object] = {"socket_path": str(socket_path)}
    if pool_root is not None:
        entry["pool_root"] = str(pool_root)
    state = _socket_state(socket_path)
    served: tuple[str, ...] | None = None
    if state == "socket":
        probe, snapshot = _probe_snapshot(socket_path, _PROBE_TIMEOUT_SECONDS)
        if probe == "live" and snapshot is not None:
            served = _snapshot_uuids(snapshot)
            try:
                validate_pool_snapshot(snapshot, aci_version=CURRENT_ACI)
            except PoolError as exc:
                entry["compatibility"] = {"status": exc.code, "reason": str(exc)}
            else:
                entry["compatibility"] = {"status": "compatible"}
        elif probe == "unauthorized":
            state = "unauthorized"
    entry["state"] = "live" if served is not None else ("stale" if state == "socket" else state)
    entry["devices"] = list(served or ())
    return entry


def _recall_bindings(repository: Path) -> tuple[list[dict[str, object]], int, int]:
    state_directory = repository / "state"
    try:
        databases = (
            sorted(state_directory.glob("*/state.sqlite")) if state_directory.is_dir() else []
        )
    except OSError:
        databases = []
    bindings: list[dict[str, object]] = []
    unreadable = 0
    for database in databases:
        try:
            with closing(
                sqlite3.connect(f"file:{quote(str(database))}?mode=ro", uri=True, timeout=0.25)
            ) as db:
                deadline = time.monotonic() + 1
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_runs'").fetchone():
                    rows = db.execute("SELECT t.run_id,t.payload_json,t.created_at FROM workflow_runs r "
                        "JOIN workflow_transitions t ON t.workflow_id=r.run_id AND t.revision=1 "
                        "WHERE t.workflow_kind='RUN' AND t.state='CREATED'").fetchall()
                else:
                    rows = db.execute("SELECT run_id,payload_json,created_at FROM workflow_transitions "
                        "WHERE workflow_kind='RUN' AND state='CREATED'").fetchall()
        except (sqlite3.Error, OSError):
            unreadable += 1
            continue
        for run_id, payload_json, created_at in rows:
            try:
                payload = json.loads(payload_json)
            except (ValueError, TypeError):
                continue
            gpu_pool = payload.get("gpu_pool") if isinstance(payload, dict) else None
            if not isinstance(gpu_pool, dict):
                continue
            socket_path = gpu_pool.get("socket_path")
            namespace = gpu_pool.get("namespace")
            server_uid = gpu_pool.get("server_uid")
            if (
                not isinstance(socket_path, str)
                or not socket_path
                or not isinstance(namespace, str)
                or not namespace
                or type(server_uid) is not int
                or server_uid < 0
                or not isinstance(run_id, str)
                or not run_id
                or not isinstance(created_at, str)
            ):
                continue
            bindings.append(
                {
                    "run_id": run_id,
                    "created_at": created_at,
                    "socket_path": socket_path,
                    "namespace": namespace,
                    "server_uid": server_uid,
                }
            )
    bindings.sort(
        key=lambda item: (str(item["created_at"]), str(item["run_id"])), reverse=True
    )
    unique: list[dict[str, object]] = []
    seen: set[tuple[object, object, object]] = set()
    endpoints = {}
    for binding in bindings:
        key = (binding["socket_path"], binding["namespace"], binding["server_uid"])
        if key in seen:
            continue
        seen.add(key)
        candidate = Path(str(binding["socket_path"]))
        if candidate not in endpoints:
            endpoints[candidate] = _endpoint_entry(candidate, None) if candidate.is_absolute() else {
                "socket_path": str(candidate), "state": "invalid", "devices": []}
        endpoint = endpoints[candidate]
        unique.append({**binding, "state": endpoint["state"], "devices": endpoint["devices"]})
    return unique, len(databases), unreadable


def status(args: argparse.Namespace) -> dict[str, object]:
    repository = _resolve_repository(args.repository)
    pools_directory = _pools_directory(repository)
    default_root = pools_directory / _DEFAULT_POOL_NAME
    try:
        siblings = (
            sorted(
                (
                    child
                    for child in pools_directory.iterdir()
                    if child.is_dir() and child != default_root
                ),
                key=lambda path: path.name,
            )
            if pools_directory.is_dir()
            else []
        )
    except OSError:
        siblings = []
    pools = [
        _endpoint_entry(root / _SOCKET_NAME, root) for root in (default_root, *siblings)
    ]
    recalled, scanned, unreadable = _recall_bindings(repository)
    return {
        "status": "ok",
        "supervisor": _SUPERVISOR,
        "repository": str(repository),
        "pools_directory": str(pools_directory),
        "project_pools": pools,
        "recalled": recalled,
        "state_roots_scanned": scanned,
        "state_roots_unreadable": unreadable,
    }


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ManagedPoolError("pool_root_unavailable") from exc
    return True


def _prepare_pool_root(plan: PoolPlan) -> None:
    pools_directory = _pools_directory(plan.repository)
    try:
        pools_existed = _lexists(pools_directory)
        pools_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not pools_existed:
            pools_directory.chmod(0o700)
        root_existed = _lexists(plan.pool_root)
        plan.pool_root.mkdir(mode=0o700, exist_ok=True)
        if not root_existed:
            plan.pool_root.chmod(0o700)
        metadata = plan.pool_root.stat()
    except OSError as exc:
        raise ManagedPoolError("pool_root_unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ManagedPoolError("pool_root_unavailable")


def _launch_pool(plan: PoolPlan, tools: Tools, environment: Mapping[str, str]) -> None:
    executable = Path(sys.executable)
    if not executable.is_absolute():
        raise ManagedPoolError("pool_launch_failed")
    serve_argv = [
        str(executable),
        "-B",
        "-m",
        "hypoloom.cli",
        "gpu-pool-serve",
        "--pool-root",
        str(plan.pool_root),
        "--socket-path",
        str(plan.socket_path),
    ]
    for uid, devices in plan.members:
        serve_argv += ["--member", f"{uid}={','.join(devices)}"]
    serve_argv += ["--reserve-mib", str(plan.reserve_mib)]
    completed = _quiet_run(
        (
            tools.systemd_run,
            "--user",
            f"--unit={plan.unit}",
            "--collect",
            "--no-block",
            "--service-type=exec",
            f"--working-directory={plan.repository}",
            "--setenv=HYPOLOOM_RUNTIME_FAMILY=hypoloom",
            "--setenv=PYTHONDONTWRITEBYTECODE=1",
            "--setenv=PYTHONNOUSERSITE=1",
            "--setenv=PYTHONSAFEPATH=1",
            f"--setenv=PYTHONPATH={plan.repository}",
            "--property=Restart=on-failure",
            "--property=RestartPreventExitStatus=2 78",
            "--property=RestartSec=1s",
            "--property=StartLimitIntervalSec=30s",
            "--property=StartLimitBurst=3",
            "--property=UMask=0077",
            "--property=KillMode=mixed",
            "--property=TimeoutStopSec=30s",
            "--",
            *serve_argv,
        ),
        environment=environment,
    )
    if completed.returncode != 0:
        raise ManagedPoolError("pool_launch_failed")


def _wait_until_ready(
    plan: PoolPlan,
    tools: Tools,
    environment: Mapping[str, str],
    ready_timeout: float,
) -> dict:
    deadline = time.monotonic() + ready_timeout
    unit_observed = False
    while True:
        properties = _unit_properties(plan, tools, environment)
        load_state = properties["LoadState"]
        active_state = properties["ActiveState"]
        if load_state != "not-found":
            unit_observed = True
        if unit_observed and (
            load_state != "loaded" or active_state not in _ACTIVE_STATES
        ):
            raise ManagedPoolError("pool_process_failed")
        if _socket_state(plan.socket_path) == "invalid":
            raise ManagedPoolError("socket_path_invalid")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ManagedPoolError("pool_ready_timeout")
        if unit_observed and _socket_state(plan.socket_path) == "socket":
            probe, snapshot = _probe_snapshot(
                plan.socket_path, min(_PROBE_TIMEOUT_SECONDS, remaining)
            )
            if probe == "live" and snapshot is not None:
                return snapshot
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ManagedPoolError("pool_ready_timeout")
        time.sleep(min(_POLL_SECONDS, remaining))


def _configuration(plan: PoolPlan, tools: Tools, environment: Mapping[str, str]) -> PoolPlan | None:
    """Read only the unit's executable configuration; never return its environment.

    Live units use /proc argv (unambiguous even with spaces). For a stopped unit,
    accept only systemd's simple, round-trippable argv representation.
    """
    pid_result = _quiet_run((tools.systemctl, "--user", "show", plan.unit,
                            "--property=MainPID", "--value"), environment=environment)
    try:
        pid = int(pid_result.stdout.strip()) if pid_result.returncode == 0 else 0
        if pid > 1:
            process = Path("/proc") / str(pid)
            if process.stat().st_uid != os.geteuid() or (process / "cwd").resolve() != plan.repository:
                return None
            argv = (process / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
        else:
            result = _quiet_run((tools.systemctl, "--user", "show", plan.unit,
                                "--property=ExecStart", "--value"), environment=environment)
            match = re.fullmatch(r"\{ path=[^;]+ ; argv\[\]=(.*?) ; ignore_errors=.*\}\s*", result.stdout)
            if result.returncode or match is None or "\\" in match[1] or '"' in match[1] or "'" in match[1]:
                return None
            argv = match[1].split()
        module = argv.index("-m")
        if argv[module:module + 3] != ["-m", "hypoloom.cli", "gpu-pool-serve"]:
            return None
        parser = JsonArgumentParser(add_help=False)
        parser.add_argument("--pool-root", required=True)
        parser.add_argument("--socket-path", required=True)
        parser.add_argument("--member", action="append", required=True)
        parser.add_argument("--reserve-mib", type=int, default=0)
        values = parser.parse_args(argv[module + 3:])
        original = _pool_plan(plan.repository, values.pool_root, values.socket_path, values.member, values.reserve_mib)
        if original.pool_root != plan.pool_root or original.socket_path != plan.socket_path:
            return None
        return replace(original, unit=plan.unit)
    except (OSError, UnicodeError, ValueError, ManagedPoolError):
        return None


def _no_conflicts(plan: PoolPlan) -> None:
    intended = {device for _, devices in plan.members for device in devices}
    candidates = set(_pools_directory(plan.repository).glob("*/control.sock"))
    candidates.update(path.parent / "control.sock" for path in _pools_directory(plan.repository).glob("*/pool.sqlite3"))
    for candidate in sorted(candidates):
        if candidate == plan.socket_path:
            continue
        probe, other = _probe_snapshot(candidate, _PROBE_TIMEOUT_SECONDS)
        devices = None if other is None else _snapshot_uuids(other)
        if probe != "live" or devices is None or intended.intersection(devices):
            raise ManagedPoolError("pool_conflict")


def _observe(plan: PoolPlan, tools: Tools, environment: Mapping[str, str], *,
             reconfigure: bool = False, locks: ExitStack | None = None) -> tuple[dict, PoolPlan]:
    properties = _unit_properties(plan, tools, environment)
    active = properties["LoadState"] == "loaded" and properties["ActiveState"] in _ACTIVE_STATES
    socket_state = _socket_state(plan.socket_path)
    if socket_state == "invalid":
        raise ManagedPoolError("socket_path_invalid")
    probe, snapshot = _probe_snapshot(plan.socket_path, _PROBE_TIMEOUT_SECONDS) if socket_state == "socket" else ("absent", None)
    if probe == "unauthorized":
        raise ManagedPoolError("unauthorized")
    served = None if snapshot is None else _snapshot_uuids(snapshot)
    if served is not None and not active:
        raise ManagedPoolError("service_busy")
    if active and served is None:
        raise ManagedPoolError("pool_unavailable")
    intended = tuple(sorted(device for _, devices in plan.members for device in devices))
    compatible = False
    failure = None
    if snapshot is not None:
        try:
            validate_pool_snapshot(snapshot, aci_version=CURRENT_ACI, allowed_gpu_uuids=intended)
            compatible = True
        except PoolError as exc:
            failure = exc.code
    if compatible and not reconfigure:
        reserves = {d.get("pool_reserve_memory_mib") for d in snapshot["devices"]}
        reserve = next(iter(reserves)) if len(reserves) == 1 else None
        return {**plan.result("ok"), "action": "reuse", "start_ready": True,
                "prepare_available": True, "devices": list(served),
                "members": {str(os.geteuid()): list(served)},
                "reserve_mib": reserve if type(reserve) is int else None,
                "configuration_action": "unchanged"}, plan
    if plan.pool_root != _pools_directory(plan.repository) / _DEFAULT_POOL_NAME:
        raise ManagedPoolError("managed_pool_requires_shared_root")
    durable = (plan.pool_root / "pool.sqlite3").exists()
    previous = _configuration(plan, tools, environment) if properties["LoadState"] == "loaded" else None
    if compatible and previous is not None and previous.members == plan.members and previous.reserve_mib == plan.reserve_mib:
        return {**previous.result("ok"), "action": "reuse", "start_ready": True,
                "prepare_available": True, "devices": list(served), "configuration_action": "unchanged"}, previous
    target = plan
    if active:
        if previous is None:
            raise ManagedPoolError("pool_configuration_unknown")
        if not reconfigure and set(intended).issubset(device for _, devices in previous.members for device in devices):
            action, target = "upgrade", previous
        else:
            action = "reconfigure"
    elif durable:
        action = "restore" if previous is not None and not reconfigure else "reconfigure"
        if action == "restore": target = previous
    else:
        action = "create"
    _no_conflicts(target)
    deps = inspect_dependencies(plan.repository, plan.socket_path, locks=locks, timeout=_remaining(8))
    return {**target.result("ok"), "action": action, "start_ready": False,
            "prepare_available": deps["quiescent"], "dependencies": deps,
            "previous_configuration": None if previous is None else {
                "members": previous.result("ok")["members"], "reserve_mib": previous.reserve_mib},
            "reason": "dependencies_active_or_unknown" if not deps["quiescent"] else failure,
            "devices": list(served or ()), "active_service": active}, target


def _arguments_plan(args):
    repository = _resolve_repository(args.repository)
    plan = _pool_plan(repository, args.pool_root, args.socket_path, args.member, args.reserve_mib)
    tools, environment = _find_tools(), _minimal_environment()
    return _derive_unit(plan, tools, environment), tools, environment


def review(args: argparse.Namespace) -> dict:
    """Read-only proposal; current readiness and preparability are separate."""
    token = _DEADLINE.set(time.monotonic() + _validate_ready_timeout(args.ready_timeout))
    plan = None
    try:
        plan, tools, environment = _arguments_plan(args)
        coordination = directory_lock(plan.pool_root, exclusive=False, timeout=_remaining(2)) if plan.pool_root.exists() else nullcontext()
        with coordination:
            result, _ = _observe(plan, tools, environment, reconfigure=getattr(args, "reviewed_action", None) == "reconfigure")
        return result
    except (ManagedPoolError, PoolError) as exc:
        return {**(plan.result("blocked") if plan else {"status": "blocked"}), "action": None, "start_ready": False,
                "prepare_available": False, "reason": exc.status if isinstance(exc, ManagedPoolError) else exc.code}
    finally:
        _DEADLINE.reset(token)


def _service_unowned(plan: PoolPlan) -> bool:
    path = plan.pool_root / "pool-service.lock"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            return False
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        return True
    finally:
        os.close(fd)


def ensure(args: argparse.Namespace) -> tuple[PoolPlan, str, tuple[str, ...]]:
    timeout = _validate_ready_timeout(args.ready_timeout)
    token = _DEADLINE.set(time.monotonic() + timeout)
    plan = None
    effects = {}
    try:
        plan, tools, environment = _arguments_plan(args)
        approved = getattr(args, "reviewed_action", None)
        # Retain the original no-replacement interface for existing callers.
        if approved is None:
            properties = _unit_properties(plan, tools, environment)
            active = properties["LoadState"] == "loaded" and properties["ActiveState"] in _ACTIVE_STATES
            if active:
                if not plan.pool_root.is_dir():
                    raise ManagedPoolError("pool_conflict")
                with directory_lock(plan.pool_root, exclusive=False, timeout=_remaining(2)):
                    probe, snapshot = _probe_snapshot(plan.socket_path, _PROBE_TIMEOUT_SECONDS)
                    intended = [d for _, ds in plan.members for d in ds]
                    served = None if snapshot is None else _snapshot_uuids(snapshot)
                    if served is None or not set(intended).issubset(served):
                        raise ManagedPoolError("pool_conflict")
                    validate_pool_snapshot(snapshot, aci_version=CURRENT_ACI, allowed_gpu_uuids=intended)
                    return plan, "reused", served
            if (plan.pool_root / "pool.sqlite3").exists():
                raise ManagedPoolError("pool_recovery_required")
            approved = "create"
        preview, target = _observe(plan, tools, environment, reconfigure=approved == "reconfigure")
        if not preview["prepare_available"]:
            raise ManagedPoolError("dependencies_active_or_unknown")
        if preview["action"] != "reuse" and preview["action"] != approved:
            raise ManagedPoolError("pool_plan_changed")
        if preview["action"] != "reuse" and (target.members != plan.members or target.reserve_mib != plan.reserve_mib):
            raise ManagedPoolError("pool_configuration_changed")
        if not plan.pool_root.exists():
            if approved != "create": raise ManagedPoolError("pool_plan_changed")
            _prepare_pool_root(plan)
            effects["pool_directory_created"] = True
        with directory_lock(plan.pool_root, exclusive=True, timeout=_remaining(2)), ExitStack() as locks:
            current, target = _observe(plan, tools, environment, reconfigure=approved == "reconfigure", locks=locks)
            if current["action"] == "reuse":
                return plan, "reused", tuple(current["devices"])
            if current["action"] != approved or not current["prepare_available"]:
                raise ManagedPoolError("pool_plan_changed")
            if target.members != plan.members or target.reserve_mib != plan.reserve_mib:
                raise ManagedPoolError("pool_configuration_changed")
            if current.get("active_service"):
                effects["previous_service_stop_requested"] = True
                if not _stop_unit(plan, tools, environment):
                    raise ManagedPoolError("pool_stop_failed")
            while True:
                properties = _unit_properties(plan, tools, environment)
                probe, _ = _probe_snapshot(plan.socket_path, min(0.2, _remaining(0.2))) if _socket_state(plan.socket_path) == "socket" else ("absent", None)
                if properties["ActiveState"] not in _ACTIVE_STATES and _service_unowned(plan) and probe != "live":
                    break
                time.sleep(min(_POLL_SECONDS, _remaining(_POLL_SECONDS)))
            # A final DB check catches old clients' commitments before stop.
            deps = inspect_dependencies(plan.repository, plan.socket_path, controllers_held=True, timeout=_remaining(8))
            if not deps["quiescent"]:
                raise ManagedPoolError("dependencies_active_or_unknown")
            try:
                effects["new_service_start_requested"] = True
                _launch_pool(plan, tools, environment)
                snapshot = _wait_until_ready(plan, tools, environment, _remaining(timeout))
                devices = validate_pool_snapshot(snapshot, aci_version=CURRENT_ACI,
                    allowed_gpu_uuids=[d for _, ds in plan.members for d in ds])
                if set(devices) != {d for _, ds in plan.members for d in ds}:
                    raise ManagedPoolError("pool_verification_failed")
            except BaseException:
                cleanup_token = _DEADLINE.set(time.monotonic() + _COMMAND_TIMEOUT_SECONDS)
                try:
                    effects["failed_service_stop_acknowledged"] = _stop_unit(plan, tools, environment)
                except ManagedPoolError:
                    effects["failed_service_stop_acknowledged"] = False
                finally:
                    _DEADLINE.reset(cleanup_token)
                raise
            return plan, {"create": "ready", "upgrade": "upgraded", "restore": "restored", "reconfigure": "reconfigured"}[approved], devices
    except PoolError as exc:
        error = ManagedPoolError(exc.code); error.context = plan; error.effects = effects
        raise error from exc
    except ManagedPoolError as exc:
        exc.context = plan
        exc.effects = effects
        raise
    finally:
        _DEADLINE.reset(token)


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", add_help=False)
    status_parser.add_argument("--repository", required=True)

    ensure_parser = subparsers.add_parser("ensure", aliases=["plan"], add_help=False)
    ensure_parser.add_argument("--repository", required=True)
    ensure_parser.add_argument("--pool-root", required=True)
    ensure_parser.add_argument("--socket-path", required=True)
    ensure_parser.add_argument(
        "--member", action="append", required=True, metavar="UID=GPU_UUID[,GPU_UUID...]"
    )
    ensure_parser.add_argument("--reserve-mib", type=int, default=0)
    ensure_parser.add_argument("--ready-timeout", type=float, default=30.0)
    ensure_parser.add_argument("--reviewed-action", choices=("create", "reuse", "restore", "upgrade", "reconfigure"))
    return parser


def _fallback_plan(raw_arguments: Sequence[str]) -> PoolPlan | None:
    values: dict[str, str] = {}
    for index, value in enumerate(raw_arguments):
        if value in {"--repository", "--pool-root", "--socket-path"} and index + 1 < len(
            raw_arguments
        ):
            values[value] = raw_arguments[index + 1]
    try:
        return PoolPlan(
            repository=Path(values.get("--repository", "")),
            pool_root=Path(values.get("--pool-root", "")),
            socket_path=Path(values.get("--socket-path", "")),
            members=(),
            reserve_mib=0,
        )
    except (OSError, RuntimeError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = tuple(sys.argv[1:] if argv is None else argv)
    context = _fallback_plan(raw_arguments)
    try:
        args = build_parser().parse_args(raw_arguments)
        if args.command == "status":
            result: dict[str, object] = status(args)
        elif args.command == "plan":
            result = review(args)
        else:
            plan, status_value, devices = ensure(args)
            result = plan.result(status_value)
            result["devices"] = list(devices)
    except ManagedPoolError as exc:
        if exc.context is not None:
            context = exc.context
        payload = (
            context.result(exc.status)
            if context is not None
            else {"status": exc.status, "supervisor": _SUPERVISOR}
        )
        if exc.effects:
            payload["effects"] = exc.effects
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 2
    except (Exception, KeyboardInterrupt):
        payload = (
            context.result("internal_error")
            if context is not None
            else {"status": "internal_error", "supervisor": _SUPERVISOR}
        )
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
