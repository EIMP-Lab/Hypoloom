"""Launch the existing read-only Textual monitor outside Claude's captured PTY.

The helper owns no run state.  Inside tmux it places the existing monitor CLI
in a new window; otherwise it prints one complete command for an external
interactive terminal.  JSON monitoring deliberately bypasses this helper.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Sequence


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_TMUX_TARGET = re.compile(r"@[0-9]+\Z")
_TMUX_TIMEOUT_SECONDS = 10.0


class MonitorLaunchError(RuntimeError):
    """A fixed, non-sensitive launcher failure."""


class LauncherArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # noqa: ARG002 - argparse API
        raise MonitorLaunchError("invalid_arguments")


def _absolute_path(raw: str, *, existing_directory: bool = False) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise MonitorLaunchError("path_not_absolute")
    try:
        resolved = candidate.resolve(strict=existing_directory)
    except (OSError, RuntimeError) as exc:
        raise MonitorLaunchError("path_invalid") from exc
    if existing_directory and not resolved.is_dir():
        raise MonitorLaunchError("working_directory_invalid")
    return resolved


def _control_python(raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise MonitorLaunchError("python_invalid")
    executable = Path(os.path.abspath(raw))
    try:
        metadata = executable.stat()
    except OSError as exc:
        raise MonitorLaunchError("python_invalid") from exc
    if not stat.S_ISREG(metadata.st_mode) or not os.access(executable, os.X_OK):
        raise MonitorLaunchError("python_invalid")
    return executable


def _interval_text(value: float) -> str:
    if not math.isfinite(value) or value <= 0:
        raise MonitorLaunchError("interval_invalid")
    return format(value, ".15g")


def _web_port_text(value: int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not 0 <= value <= 65535:
        raise MonitorLaunchError("web_port_invalid")
    return str(value)


def _monitor_argv(
    *,
    python: Path,
    state_root: Path,
    run_id: str,
    interval: str,
    web_port: str | None,
    web_enabled: bool,
) -> tuple[str, ...]:
    command = (
        "env",
        "PYTHONDONTWRITEBYTECODE=1",
        "HYPOLOOM_RUNTIME_FAMILY=hypoloom",
        str(python),
        "-m",
        "hypoloom.cli",
        "monitor",
        "--state-root",
        str(state_root),
        "--run-id",
        run_id,
        "--interval",
        interval,
    )
    if web_port is not None:
        command += ("--web-port", web_port)
    if not web_enabled:
        command += ("--no-web",)
    return command


def _external_command(working_directory: Path, monitor_argv: Sequence[str]) -> str:
    return (
        f"cd -- {shlex.quote(str(working_directory))} && "
        f"{shlex.join(tuple(monitor_argv))}"
    )


def _tmux_preflight(
    *, python: Path, working_directory: Path, monitor_argv: Sequence[str]
) -> None:
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HYPOLOOM_RUNTIME_FAMILY": "hypoloom",
    }
    checks = (
        (
            (str(python), "-c", "import hypoloom.tui"),
            "TUI unavailable; install 'hypoloom[monitor]' or use --json",
        ),
        (
            (str(python), "-c", "import sys; from hypoloom.monitor import validate_monitor_source; "
             "validate_monitor_source(sys.argv[1], sys.argv[2])",
             monitor_argv[monitor_argv.index("--state-root") + 1], monitor_argv[monitor_argv.index("--run-id") + 1]),
            "monitor snapshot unavailable; use --json for details",
        ),
    )
    for command, failure in checks:
        try:
            completed = subprocess.run(
                command,
                check=False,
                cwd=working_directory,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_TMUX_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MonitorLaunchError(failure) from exc
        if completed.returncode != 0:
            raise MonitorLaunchError(failure)


def launch(
    *,
    python: str,
    working_directory: str,
    state_root: str,
    run_id: str,
    interval: float,
    web_port: int | None = None,
    web_enabled: bool = True,
) -> int:
    if _SAFE_RUN_ID.fullmatch(run_id) is None:
        raise MonitorLaunchError("run_id_invalid")
    selected_python = _control_python(python)
    selected_working_directory = _absolute_path(
        working_directory, existing_directory=True
    )
    selected_state_root = _absolute_path(state_root, existing_directory=False)
    selected_interval = _interval_text(interval)
    selected_web_port = _web_port_text(web_port)
    monitor_argv = _monitor_argv(
        python=selected_python,
        state_root=selected_state_root,
        run_id=run_id,
        interval=selected_interval,
        web_port=selected_web_port,
        web_enabled=web_enabled,
    )

    if not os.environ.get("TMUX"):
        print(_external_command(selected_working_directory, monitor_argv))
        return 0

    tmux = shutil.which("tmux")
    if tmux is None:
        raise MonitorLaunchError("tmux_not_found")
    _tmux_preflight(
        python=selected_python,
        working_directory=selected_working_directory,
        monitor_argv=monitor_argv,
    )
    tmux_command = (
        tmux,
        "new-window",
        "-P",
        "-F",
        "#{window_id}",
        "-n",
        "hypoloom-monitor",
        "-c",
        str(selected_working_directory),
        f"exec {shlex.join(monitor_argv)}",
    )
    try:
        completed = subprocess.run(
            tmux_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MonitorLaunchError("tmux_launch_failed") from exc
    target = completed.stdout.strip()
    if completed.returncode != 0 or _TMUX_TARGET.fullmatch(target) is None:
        raise MonitorLaunchError("tmux_launch_failed")
    print(f"monitor TUI started in tmux window {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = LauncherArgumentParser(
        description="Launch the read-only Textual monitor in an external PTY."
    )
    parser.add_argument("--python", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument(
        "--web-port",
        type=int,
        help="TUI Web port on 127.0.0.1 (0 lets the OS choose)",
    )
    parser.add_argument(
        "--no-web",
        action="store_false",
        dest="web_enabled",
        help="do not start the TUI-owned loopback Web observer",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
        return launch(
            python=arguments.python,
            working_directory=arguments.working_directory,
            state_root=arguments.state_root,
            run_id=arguments.run_id,
            interval=arguments.interval,
            web_port=arguments.web_port,
            web_enabled=arguments.web_enabled,
        )
    except MonitorLaunchError as exc:
        print(f"monitor launcher: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
