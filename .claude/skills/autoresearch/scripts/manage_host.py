"""Start or stop one fresh-root Hypoloom Host through the user systemd manager.

This helper deliberately has no durable authority of its own.  The systemd
unit is derived from the absolute state-root path, and Host readiness is the
existing mode-0600 ``control.sock`` created by the Hypoloom runtime.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


_REPOSITORY = Path(__file__).resolve().parents[4]
if str(_REPOSITORY) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY))
sys.dont_write_bytecode = True
_control_protocol = importlib.import_module("hypoloom.control_protocol")
ControlRequest = _control_protocol.ControlRequest
request_over_socket = _control_protocol.request_over_socket

_SUPERVISOR = "user-systemd"
_SERVICE_TEMPLATE = "hypoloom@.service"
_SCOPE_PREFIX = "hypoloom-"
_MAX_READY_TIMEOUT_SECONDS = 300.0
_COMMAND_TIMEOUT_SECONDS = 10.0
_POLL_SECONDS = 0.05
_LAUNCHER_TERM_SECONDS = 1.0
_LAUNCHER_KILL_SECONDS = 1.0
_ACTIVE_STATES = frozenset({"active", "activating", "reloading"})
_READINESS_REQUEST_ID = "hypoloom-managed-host-readiness"
_READINESS_RUN_ID = "hypoloom-managed-host-readiness-probe"
_READINESS_REQUEST = ControlRequest.from_mapping(
    {
        "schema_version": 1,
        "request_id": _READINESS_REQUEST_ID,
        "expected_revision": None,
        "command": "STATUS",
        "payload": {"run_id": _READINESS_RUN_ID},
    }
)
_SHELL_NAMES = frozenset(
    {"ash", "bash", "csh", "dash", "env", "fish", "ksh", "sh", "tcsh", "zsh"}
)
_CLAUDE_ONLY_OPTIONS = frozenset(
    {
        "--claude-executable",
        "--claude-settings",
        "--claude-model",
        "--claude-bwrap",
        "--agent-max-output-bytes",
        "--agent-max-tokens",
        "--agent-max-turns",
        "--agent-wall-seconds",
        "--agent-max-cost-usd",
    }
)
_CLIENT_ONLY_OPTIONS = frozenset({"--allow-gpu", "--cpu-only"})
_MANAGED_ONLY_OPTIONS = frozenset({"--exit-when-terminal"})
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


class ManagedHostError(RuntimeError):
    """A fixed, non-sensitive helper failure."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status
        self.context: Context | None = None


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep invalid input from being interpolated into argparse diagnostics."""

    def error(self, message: str) -> None:  # noqa: ARG002 - argparse API
        raise ManagedHostError("invalid_arguments")


@dataclass(frozen=True)
class Context:
    state_root: Path
    control_socket: Path
    auth_transport: str
    unit: str = ""

    def result(self, status_value: str) -> dict[str, str]:
        return {
            "status": status_value,
            "supervisor": _SUPERVISOR,
            "unit": self.unit,
            "state_root": str(self.state_root),
            "control_socket": str(self.control_socket),
        }


@dataclass(frozen=True)
class Tools:
    systemd_escape: str
    systemd_run: str
    systemctl: str


def _path_context(raw_state_root: str, auth_transport: str) -> Context:
    candidate = Path(raw_state_root)
    if not candidate.is_absolute():
        raise ManagedHostError("state_root_not_absolute")
    try:
        state_root = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ManagedHostError("state_root_invalid") from exc
    return Context(
        state_root=state_root,
        control_socket=state_root / "control.sock",
        auth_transport=auth_transport,
    )


def _resolve_working_directory(raw_working_directory: str) -> Path:
    candidate = Path(raw_working_directory)
    if not candidate.is_absolute():
        raise ManagedHostError("working_directory_not_absolute")
    try:
        working_directory = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManagedHostError("working_directory_invalid") from exc
    if not working_directory.is_dir():
        raise ManagedHostError("working_directory_invalid")
    return working_directory


def _validate_ready_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0 or value > _MAX_READY_TIMEOUT_SECONDS:
        raise ManagedHostError("ready_timeout_invalid")
    return value


def _validate_host_argv(
    host_argv: Sequence[str], state_root: Path, auth_transport: str
) -> tuple[str, ...]:
    values = tuple(host_argv)
    if values[:1] == ("--",):
        values = values[1:]
    if len(values) < 6:
        raise ManagedHostError("invalid_host_argv")

    executable = Path(values[0])
    if not executable.is_absolute():
        raise ManagedHostError("invalid_host_argv")
    try:
        metadata = executable.stat()
        resolved_name = executable.resolve(strict=True).name.casefold()
    except (OSError, RuntimeError) as exc:
        raise ManagedHostError("invalid_host_argv") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not os.access(executable, os.X_OK)
        or resolved_name in _SHELL_NAMES
    ):
        raise ManagedHostError("invalid_host_argv")

    if values[1:4] != ("-m", "hypoloom.cli", "serve"):
        raise ManagedHostError("invalid_host_argv")
    if any(
        value in _CLIENT_ONLY_OPTIONS
        or any(value.startswith(f"{option}=") for option in _CLIENT_ONLY_OPTIONS)
        for value in values
    ):
        raise ManagedHostError("invalid_host_argv")
    if any(value.startswith("--exit-when-terminal=") for value in values) or any(
        values.count(option) != 1 for option in _MANAGED_ONLY_OPTIONS
    ):
        raise ManagedHostError("invalid_host_argv")
    if values.count("--state-root") != 1 or any(
        value.startswith("--state-root=") for value in values
    ):
        raise ManagedHostError("invalid_host_argv")
    state_index = values.index("--state-root")
    if state_index + 1 >= len(values):
        raise ManagedHostError("invalid_host_argv")
    command_state_root = Path(values[state_index + 1])
    if not command_state_root.is_absolute():
        raise ManagedHostError("invalid_host_argv")
    try:
        command_state_root = command_state_root.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ManagedHostError("invalid_host_argv") from exc
    if command_state_root != state_root:
        raise ManagedHostError("invalid_host_argv")

    if values.count("--agent-adapter") != 1 or any(
        value.startswith("--agent-adapter=") for value in values
    ):
        raise ManagedHostError("invalid_host_argv")
    adapter_index = values.index("--agent-adapter")
    if adapter_index + 1 >= len(values):
        raise ManagedHostError("invalid_host_argv")
    adapter = values[adapter_index + 1]

    settings_count = values.count("--claude-settings")
    if any(value.startswith("--claude-settings=") for value in values):
        raise ManagedHostError("invalid_host_argv")
    if auth_transport == "none":
        if adapter != "fake" or settings_count:
            raise ManagedHostError("invalid_host_argv")
        if any(
            value in _CLAUDE_ONLY_OPTIONS
            or any(value.startswith(f"{option}=") for option in _CLAUDE_ONLY_OPTIONS)
            for value in values
        ):
            raise ManagedHostError("invalid_host_argv")
    elif auth_transport == "settings":
        if adapter != "claude":
            raise ManagedHostError("invalid_host_argv")
        if settings_count != 1:
            raise ManagedHostError("invalid_host_argv")
        settings_index = values.index("--claude-settings")
        if settings_index + 1 >= len(values) or not Path(
            values[settings_index + 1]
        ).is_absolute():
            raise ManagedHostError("invalid_host_argv")
    else:
        if adapter != "claude" or settings_count:
            raise ManagedHostError("invalid_host_argv")
    return values


def _minimal_environment(*, include_api_key: bool) -> dict[str, str]:
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
    if include_api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key or not api_key.strip():
            raise ManagedHostError("environment_auth_unavailable")
        environment["ANTHROPIC_API_KEY"] = api_key
        if base_url := os.environ.get("ANTHROPIC_BASE_URL"):
            environment["ANTHROPIC_BASE_URL"] = base_url
    return environment


def _desktop_environment(host_argv: Sequence[str]) -> dict[str, str]:
    selected = list(host_argv)
    if "--institutional-browser-mode" not in selected:
        return {}
    index = selected.index("--institutional-browser-mode")
    if selected[index + 1:index + 2] != ["local"]:
        return {}
    return {name: os.environ[name] for name in (
        "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE")
        if os.environ.get(name)}


def _find_tools() -> Tools:
    if not sys.platform.startswith("linux"):
        raise ManagedHostError("linux_user_systemd_required")
    resolved = {
        name: shutil.which(name) for name in ("systemd-escape", "systemd-run", "systemctl")
    }
    if any(value is None for value in resolved.values()):
        raise ManagedHostError("user_systemd_tools_unavailable")
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
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManagedHostError("supervisor_command_failed") from exc


def _derive_unit(
    context: Context, tools: Tools, environment: Mapping[str, str]
) -> Context:
    service = context.auth_transport in {"none", "settings"}
    escape_arguments = (
        (
            tools.systemd_escape,
            "--path",
            f"--template={_SERVICE_TEMPLATE}",
            str(context.state_root),
        )
        if service
        else (tools.systemd_escape, "--path", str(context.state_root))
    )
    completed = _quiet_run(
        escape_arguments,
        environment=environment,
    )
    if completed.returncode != 0:
        raise ManagedHostError("unit_name_failed")
    escaped = completed.stdout.strip()
    unit = escaped if service else f"{_SCOPE_PREFIX}{escaped}.scope"
    expected_prefix = "hypoloom@" if service else _SCOPE_PREFIX
    suffix = ".service" if service else ".scope"
    if (
        not escaped
        or not unit.startswith(expected_prefix)
        or not unit.endswith(suffix)
        or (not service and "@" in unit)
        or len(unit) > 255
        or "/" in unit
        or not re.fullmatch(r"[A-Za-z0-9@_.:\\\\-]+", unit)
    ):
        raise ManagedHostError("unit_name_failed")
    return Context(
        state_root=context.state_root,
        control_socket=context.control_socket,
        auth_transport=context.auth_transport,
        unit=unit,
    )


def _unit_properties(
    context: Context, tools: Tools, environment: Mapping[str, str]
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
            context.unit,
        ),
        environment=environment,
    )
    if completed.returncode != 0:
        raise ManagedHostError("supervisor_query_failed")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {"LoadState": "not-found", "ActiveState": "inactive"}
    if len(lines) != 1:
        raise ManagedHostError("supervisor_query_failed")
    fields = lines[0].split(maxsplit=4)
    if len(fields) < 4 or fields[0] != context.unit:
        raise ManagedHostError("supervisor_query_failed")
    return {"LoadState": fields[1], "ActiveState": fields[2]}


def _unit_is_absent(
    context: Context, tools: Tools, environment: Mapping[str, str]
) -> bool:
    return _unit_properties(context, tools, environment)["LoadState"] == "not-found"


def _socket_state(path: Path) -> str:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        raise ManagedHostError("control_socket_invalid") from exc
    return "socket" if stat.S_ISSOCK(metadata.st_mode) else "invalid"


def _control_socket_is_ready(path: Path, timeout: float) -> bool:
    try:
        response = request_over_socket(path, _READINESS_REQUEST, timeout=timeout)
    except (ConnectionError, TimeoutError, OSError, ValueError):
        return False
    return bool(
        response.request_id == _READINESS_REQUEST_ID
        and response.status == "rejected"
        and response.revision is None
        and response.result is None
        and response.error is not None
        and response.error.get("code") == "not_found"
    )


def _stop_unit(
    context: Context, tools: Tools, environment: Mapping[str, str]
) -> bool:
    completed = _quiet_run(
        (tools.systemctl, "--user", "stop", context.unit),
        environment=environment,
    )
    return completed.returncode == 0


def _launch_service(
    context: Context,
    tools: Tools,
    working_directory: Path,
    host_argv: Sequence[str],
    environment: Mapping[str, str],
) -> None:
    completed = _quiet_run(
        (
            tools.systemd_run,
            "--user",
            f"--unit={context.unit}",
            "--collect",
            "--no-block",
            "--service-type=exec",
            f"--working-directory={working_directory}",
            "--setenv=HYPOLOOM_RUNTIME_FAMILY=hypoloom",
            "--setenv=PYTHONDONTWRITEBYTECODE=1",
            "--setenv=PYTHONNOUSERSITE=1",
            "--setenv=PYTHONSAFEPATH=1",
            f"--setenv=PYTHONPATH={context.state_root / 'bundle'}{os.pathsep}{working_directory}",
            *("--setenv=" + name + "=" + value for name, value in _desktop_environment(host_argv).items()),
            "--property=Restart=on-failure",
            "--property=RestartPreventExitStatus=2 78",
            "--property=RestartSec=1s",
            "--property=StartLimitIntervalSec=30s",
            "--property=StartLimitBurst=3",
            "--property=UMask=0077",
            "--property=KillMode=mixed",
            "--property=TimeoutStopSec=30s",
            "--",
            *host_argv,
        ),
        environment=environment,
    )
    if completed.returncode != 0:
        raise ManagedHostError("host_launch_failed")


def _diagnostic_log_descriptor(state_root: Path) -> int:
    log_path = state_root.parent / f".{state_root.name}.hypoloom-host.log"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(log_path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("diagnostic log is not regular")
        os.fchmod(descriptor, 0o600)
        os.write(
            descriptor,
            b"hypoloom Host diagnostic log (non-authority)\n",
        )
    except OSError as exc:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise ManagedHostError("diagnostic_log_unavailable") from exc
    return descriptor


def _launch_scope(
    context: Context,
    tools: Tools,
    working_directory: Path,
    host_argv: Sequence[str],
    environment: Mapping[str, str],
) -> subprocess.Popen[bytes]:
    descriptor = _diagnostic_log_descriptor(context.state_root)
    try:
        process = subprocess.Popen(
            (
                tools.systemd_run,
                "--user",
                "--scope",
                f"--unit={context.unit}",
                "--collect",
                f"--working-directory={working_directory}",
                "--quiet",
                "--",
                *host_argv,
            ),
            env={**environment, **_desktop_environment(host_argv), "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1",
                "PYTHONPATH": f"{context.state_root / 'bundle'}{os.pathsep}{working_directory}"},
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise ManagedHostError("host_launch_failed") from exc
    finally:
        os.close(descriptor)
    return process


def _wait_until_ready(
    context: Context,
    tools: Tools,
    environment: Mapping[str, str],
    ready_timeout: float,
    scope_launcher: subprocess.Popen[bytes] | None,
) -> None:
    deadline = time.monotonic() + ready_timeout
    unit_observed = False
    while True:
        if scope_launcher is not None and scope_launcher.poll() is not None:
            raise ManagedHostError("host_process_failed")

        properties = _unit_properties(context, tools, environment)
        load_state = properties["LoadState"]
        active_state = properties["ActiveState"]
        if load_state != "not-found":
            unit_observed = True
        if unit_observed and (
            load_state != "loaded" or active_state not in _ACTIVE_STATES
        ):
            raise ManagedHostError("host_process_failed")

        socket_state = _socket_state(context.control_socket)
        if socket_state == "invalid":
            raise ManagedHostError("control_socket_invalid")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ManagedHostError("host_ready_timeout")
        if (
            socket_state == "socket"
            and unit_observed
            and _control_socket_is_ready(
                context.control_socket, min(0.2, remaining)
            )
        ):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ManagedHostError("host_ready_timeout")
        time.sleep(min(_POLL_SECONDS, remaining))


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ManagedHostError("state_root_invalid") from exc
    return True


def _prepare_state_parent(context: Context) -> None:
    parent = context.state_root.parent
    parent_existed = _lexists(parent)
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not parent_existed:
            parent.chmod(0o700)
        metadata = parent.stat()
    except OSError as exc:
        raise ManagedHostError("state_parent_unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ManagedHostError("state_parent_unavailable")
    if _lexists(context.state_root) or _lexists(context.control_socket):
        raise ManagedHostError("state_root_exists")


def _create_fresh_state_root(context: Context) -> None:
    try:
        context.state_root.mkdir(mode=0o700, exist_ok=False)
        context.state_root.chmod(0o700)
        metadata = context.state_root.stat(follow_symlinks=False)
    except FileExistsError as exc:
        raise ManagedHostError("state_root_exists") from exc
    except OSError as exc:
        raise ManagedHostError("state_root_unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ManagedHostError("state_root_unavailable")


def _stop_scope_launcher(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=_LAUNCHER_TERM_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=_LAUNCHER_KILL_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def start(args: argparse.Namespace, raw_arguments: Sequence[str]) -> Context:
    context = _path_context(args.state_root, args.auth_transport)
    if not args.host_argv or args.host_argv[0] != "--" or "--" not in raw_arguments:
        raise ManagedHostError("invalid_host_argv")
    working_directory = _resolve_working_directory(args.working_directory)
    ready_timeout = _validate_ready_timeout(args.ready_timeout)
    host_argv = _validate_host_argv(
        args.host_argv, context.state_root, context.auth_transport
    )
    if _lexists(context.state_root) or _lexists(context.control_socket):
        raise ManagedHostError("state_root_exists")

    tools = _find_tools()
    supervisor_environment = _minimal_environment(include_api_key=False)
    context = _derive_unit(context, tools, supervisor_environment)
    try:
        if not _unit_is_absent(context, tools, supervisor_environment):
            raise ManagedHostError("unit_exists")

        scope_environment = None
        if context.auth_transport == "environment":
            scope_environment = _minimal_environment(include_api_key=True)
        _prepare_state_parent(context)
        _create_fresh_state_root(context)

        created = False
        ready = False
        scope_launcher = None
        try:
            created = True
            if context.auth_transport in {"none", "settings"}:
                _launch_service(
                    context,
                    tools,
                    working_directory,
                    host_argv,
                    supervisor_environment,
                )
            else:
                assert scope_environment is not None
                scope_launcher = _launch_scope(
                    context,
                    tools,
                    working_directory,
                    host_argv,
                    scope_environment,
                )
            _wait_until_ready(
                context,
                tools,
                supervisor_environment,
                ready_timeout,
                scope_launcher,
            )
            ready = True
            return context
        finally:
            if created and not ready:
                try:
                    _stop_unit(context, tools, supervisor_environment)
                except ManagedHostError:
                    pass
                if scope_launcher is not None:
                    _stop_scope_launcher(scope_launcher)
                    try:
                        _stop_unit(context, tools, supervisor_environment)
                    except ManagedHostError:
                        pass
    except ManagedHostError as exc:
        exc.context = context
        raise


def stop(args: argparse.Namespace) -> Context:
    context = _path_context(args.state_root, args.auth_transport)
    tools = _find_tools()
    supervisor_environment = _minimal_environment(include_api_key=False)
    context = _derive_unit(context, tools, supervisor_environment)
    try:
        if not _stop_unit(context, tools, supervisor_environment):
            raise ManagedHostError("host_stop_failed")
    except ManagedHostError as exc:
        exc.context = context
        raise
    return context


def recover(args: argparse.Namespace) -> Context:
    """Start only the recorded frozen Host's reviewed cancellation operation."""
    from hypoloom.pool_run_recovery import read_target, recovery_launch, TERMINAL
    from hypoloom.gpu_pool_lifecycle import _hold_controller
    root = Path(args.state_root)
    ready_timeout = _validate_ready_timeout(args.ready_timeout)
    try:
        with ExitStack() as locks:
            _hold_controller(root, locks)
            target = read_target(root, args.run_id, args.expected_revision)
            host_argv, working_directory, transport = recovery_launch(target)
        context = _path_context(str(root), transport)
        tools = _find_tools()
        environment = _minimal_environment(include_api_key=False)
        context = _derive_unit(context, tools, environment)
        if target["state"] in TERMINAL:
            return context
        if not _unit_is_absent(context, tools, environment):
            raise ManagedHostError("unit_exists")
        host_argv += ["--cancel-run-id", args.run_id, "--cancel-expected-revision", str(args.expected_revision)]
        host_argv = _validate_host_argv(host_argv, root, transport)
        _launch_service(context, tools, Path(working_directory), host_argv, environment)
        try:
            _wait_until_ready(context, tools, environment, ready_timeout, None)
        except ManagedHostError:
            # A short cancellation may finish before readiness is observed. An
            # uncertain launch must be re-observed, never stopped or relaunched
            # here: the reviewed CANCEL could already have been accepted.
            target = read_target(root, args.run_id, args.expected_revision, allow_pending=True)
            if not target["cancel_accepted"]:
                raise ManagedHostError("cancellation_start_uncertain") from None
        return context
    except (OSError, ValueError) as exc:
        raise ManagedHostError("cancellation_recovery_unavailable") from exc


def retire(args: argparse.Namespace) -> Context:
    """Run the reviewed, isolated legacy closure; never resume its research."""
    from hypoloom.pool_run_recovery import read_target, recovery_launch, TERMINAL
    from hypoloom.gpu_pool_lifecycle import _hold_controller
    from hypoloom import legacy_run_closure
    root = Path(args.state_root)
    ready_timeout = _validate_ready_timeout(args.ready_timeout)
    try:
        with ExitStack() as locks:
            _hold_controller(root, locks)
            target = read_target(root, args.run_id, args.expected_revision, retire=True)
            if target["state"] not in TERMINAL:
                try:
                    recovery_launch(target)
                except ValueError as exc:
                    if str(exc) != "original_recovery_launch_unavailable":
                        raise
                else:
                    raise ValueError("legacy_closure_not_required")
                legacy_run_closure.inspect_target(root, args.run_id, args.expected_revision)
        context = _path_context(str(root), "none")
        tools = _find_tools()
        environment = _minimal_environment(include_api_key=False)
        context = _derive_unit(context, tools, environment)
        if target["state"] in TERMINAL:
            return context
        if not _unit_is_absent(context, tools, environment):
            raise ManagedHostError("unit_exists")
        worker = Path(legacy_run_closure.__file__).resolve()
        command = [sys.executable, "-I", "-B", str(worker), "execute", "--state-root", str(root),
                   "--run-id", args.run_id, "--expected-revision", str(args.expected_revision)]
        _launch_service(context, tools, worker.parent.parent, command, environment)
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            target = read_target(root, args.run_id, args.expected_revision, allow_pending=True, retire=True)
            if target["cancel_accepted"] or target["state"] in TERMINAL:
                return context
            if _unit_is_absent(context, tools, environment):
                raise ManagedHostError("legacy_closure_start_failed")
            time.sleep(.1)
        raise ManagedHostError("legacy_closure_start_uncertain")
    except (OSError, ValueError) as exc:
        raise ManagedHostError("legacy_closure_unavailable") from exc


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", add_help=False)
    start_parser.add_argument("--state-root", required=True)
    start_parser.add_argument("--working-directory", required=True)
    start_parser.add_argument("--ready-timeout", required=True, type=float)
    start_parser.add_argument(
        "--auth-transport",
        required=True,
        choices=("none", "settings", "environment"),
    )
    start_parser.add_argument("host_argv", nargs=argparse.REMAINDER)

    stop_parser = subparsers.add_parser("stop", add_help=False)
    stop_parser.add_argument("--state-root", required=True)
    stop_parser.add_argument(
        "--auth-transport",
        required=True,
        choices=("none", "settings", "environment"),
    )
    for command in ("recover", "retire"):
        recovery = subparsers.add_parser(command, add_help=False)
        recovery.add_argument("--state-root", required=True)
        recovery.add_argument("--run-id", required=True)
        recovery.add_argument("--expected-revision", required=True, type=int)
        recovery.add_argument("--ready-timeout", type=float, default=10)
    return parser


def _fallback_context(raw_arguments: Sequence[str]) -> Context:
    state_root = ""
    auth_transport = "settings"
    option_arguments = raw_arguments
    if "--" in option_arguments:
        option_arguments = option_arguments[: option_arguments.index("--")]
    for index, value in enumerate(option_arguments):
        if value == "--state-root" and index + 1 < len(option_arguments):
            state_root = option_arguments[index + 1]
        elif value == "--auth-transport" and index + 1 < len(option_arguments):
            if option_arguments[index + 1] in {"none", "settings", "environment"}:
                auth_transport = option_arguments[index + 1]
    try:
        return _path_context(state_root, auth_transport)
    except ManagedHostError:
        return Context(
            state_root=Path(state_root),
            control_socket=Path(state_root) / "control.sock" if state_root else Path(""),
            auth_transport=auth_transport,
        )


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = tuple(sys.argv[1:] if argv is None else argv)
    context = _fallback_context(raw_arguments)
    try:
        args = build_parser().parse_args(raw_arguments)
        if args.command == "start":
            context = start(args, raw_arguments)
            status_value = "ready"
        elif args.command == "recover":
            context = recover(args)
            status_value = "cancellation_pending"
        elif args.command == "retire":
            context = retire(args)
            status_value = "cancellation_pending"
        else:
            context = stop(args)
            status_value = "stopped"
    except ManagedHostError as exc:
        if exc.context is not None:
            context = exc.context
        print(json.dumps(context.result(exc.status), sort_keys=True, separators=(",", ":")))
        return 2
    except (Exception, KeyboardInterrupt):
        print(
            json.dumps(
                context.result("internal_error"), sort_keys=True, separators=(",", ":")
            )
        )
        return 2
    print(json.dumps(context.result(status_value), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
