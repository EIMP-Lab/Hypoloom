from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import textwrap
import copy
from pathlib import Path

from hypoloom.control_protocol import ControlRequest
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost


REPOSITORY = Path(__file__).resolve().parents[2]
LAUNCHER = (
    REPOSITORY
    / ".claude"
    / "skills"
    / "autoresearch"
    / "scripts"
    / "launch_monitor.py"
)


def _create_monitorable_run(state_root: Path, run_id: str = "run-monitor-1") -> None:
    admission = admit_profile(
        REPOSITORY / "tests" / "fixtures" / "profiles" / "component-demo",
        run_id=run_id,
    )
    assert admission.admitted and admission.create_run_payload is not None
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(
                1,
                "create-run-monitor-1",
                0,
                "CREATE_RUN",
                copy.deepcopy(admission.create_run_payload),
            )
        )
        assert created.status == "accepted"
    finally:
        host.close()


def _run_launcher(
    *,
    working_directory: Path,
    state_root: Path,
    environment: dict[str, str],
    interval: str = "2",
    extra_arguments: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(LAUNCHER),
            "--python",
            sys.executable,
            "--working-directory",
            str(working_directory),
            "--state-root",
            str(state_root),
            "--run-id",
            "run-monitor-1",
            "--interval",
            interval,
            *extra_arguments,
        ),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_launcher_outside_tmux_prints_one_complete_external_command(
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "working directory"
    working_directory.mkdir()
    state_root = tmp_path / "state root"
    environment = dict(os.environ)
    environment.pop("TMUX", None)

    completed = _run_launcher(
        working_directory=working_directory,
        state_root=state_root,
        environment=environment,
        interval="1.25",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert len(completed.stdout.splitlines()) == 1
    change_directory, monitor = completed.stdout.rstrip("\n").split(" && ", 1)
    assert shlex.split(change_directory) == [
        "cd",
        "--",
        str(working_directory.resolve()),
    ]
    assert shlex.split(monitor) == [
        "env",
        "PYTHONDONTWRITEBYTECODE=1",
        "HYPOLOOM_RUNTIME_FAMILY=hypoloom",
        os.path.abspath(sys.executable),
        "-m",
        "hypoloom.cli",
        "monitor",
        "--state-root",
        str(state_root.resolve()),
        "--run-id",
        "run-monitor-1",
        "--interval",
        "1.25",
    ]
    assert "--json" not in monitor
    assert not state_root.exists()


def test_launcher_inside_tmux_starts_existing_tui_in_new_window(
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "repository"
    working_directory.mkdir()
    state_root = tmp_path / "untouched-state-root"
    _create_monitorable_run(state_root)
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    trace = tmp_path / "tmux-argv.json"
    fake_tmux = binary_directory / "tmux"
    fake_tmux.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            import sys
            from pathlib import Path

            Path(os.environ["FAKE_TMUX_TRACE"]).write_text(
                json.dumps(sys.argv[1:]), encoding="utf-8"
            )
            print("@7")
            """
        ),
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{binary_directory}:{os.environ['PATH']}",
        "TMUX": "/tmp/tmux-1000/default,123,0",
        "FAKE_TMUX_TRACE": str(trace),
    }

    completed = _run_launcher(
        working_directory=working_directory,
        state_root=state_root,
        environment=environment,
        interval="3",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout == "monitor TUI started in tmux window @7\n"
    arguments = json.loads(trace.read_text(encoding="utf-8"))
    assert arguments[:-1] == [
        "new-window",
        "-P",
        "-F",
        "#{window_id}",
        "-n",
        "hypoloom-monitor",
        "-c",
        str(working_directory.resolve()),
    ]
    monitor = shlex.split(arguments[-1])
    assert monitor == [
        "exec",
        "env",
        "PYTHONDONTWRITEBYTECODE=1",
        "HYPOLOOM_RUNTIME_FAMILY=hypoloom",
        os.path.abspath(sys.executable),
        "-m",
        "hypoloom.cli",
        "monitor",
        "--state-root",
        str(state_root.resolve()),
        "--run-id",
        "run-monitor-1",
        "--interval",
        "3",
    ]
    assert "--json" not in monitor
    assert "-d" not in arguments
    assert (state_root / "state.sqlite").is_file()


def test_launcher_forwards_nondefault_web_controls(tmp_path: Path) -> None:
    working_directory = tmp_path / "working directory"
    working_directory.mkdir()
    state_root = tmp_path / "state root"
    environment = dict(os.environ)
    environment.pop("TMUX", None)

    completed = _run_launcher(
        working_directory=working_directory,
        state_root=state_root,
        environment=environment,
        extra_arguments=("--web-port", "43210", "--no-web"),
    )

    assert completed.returncode == 0, completed.stderr
    _, monitor = completed.stdout.rstrip("\n").split(" && ", 1)
    assert shlex.split(monitor)[-3:] == ["--web-port", "43210", "--no-web"]
    assert not state_root.exists()


def test_launcher_inside_tmux_preflights_snapshot_before_opening_window(
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "repository"
    working_directory.mkdir()
    state_root = tmp_path / "missing-state-root"
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    trace = tmp_path / "tmux-called"
    fake_tmux = binary_directory / "tmux"
    fake_tmux.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(trace)!r}).touch()\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{binary_directory}:{os.environ['PATH']}",
        "TMUX": "/tmp/tmux-1000/default,123,0",
    }

    completed = _run_launcher(
        working_directory=working_directory,
        state_root=state_root,
        environment=environment,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == (
        "monitor launcher: monitor snapshot unavailable; use --json for details\n"
    )
    assert not trace.exists()
    assert not state_root.exists()


def test_launcher_rejects_invalid_interval_without_touching_state(
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "repository"
    working_directory.mkdir()
    state_root = tmp_path / "untouched-state-root"
    environment = dict(os.environ)
    environment.pop("TMUX", None)

    completed = _run_launcher(
        working_directory=working_directory,
        state_root=state_root,
        environment=environment,
        interval="0",
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "monitor launcher: interval_invalid\n"
    assert not state_root.exists()


def test_launcher_rejects_invalid_web_port_without_touching_state(
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "working directory"
    working_directory.mkdir()
    state_root = tmp_path / "untouched-state-root"
    environment = dict(os.environ)
    environment.pop("TMUX", None)

    completed = _run_launcher(
        working_directory=working_directory,
        state_root=state_root,
        environment=environment,
        extra_arguments=("--web-port", "65536"),
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "monitor launcher: web_port_invalid\n"
    assert not state_root.exists()


def test_launcher_rejects_relative_python_without_touching_state(
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "repository"
    working_directory.mkdir()
    state_root = tmp_path / "untouched-state-root"
    environment = dict(os.environ)
    environment.pop("TMUX", None)

    completed = subprocess.run(
        (
            sys.executable,
            str(LAUNCHER),
            "--python",
            "python",
            "--working-directory",
            str(working_directory),
            "--state-root",
            str(state_root),
            "--run-id",
            "run-monitor-1",
        ),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "monitor launcher: python_invalid\n"
    assert not state_root.exists()
