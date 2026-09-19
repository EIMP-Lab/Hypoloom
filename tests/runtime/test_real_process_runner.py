from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hypoloom.domain import AttemptTerminalReason
from hypoloom.runner import (
    LocalRunner,
    ProcessObservation,
    ProcessRequest,
    process_group_exists,
)


def request(tmp_path: Path, code: str, *, timeout: float = 3) -> ProcessRequest:
    return ProcessRequest(
        attempt_id="attempt-1",
        attempt_ordinal=1,
        execution_id="execution-1",
        argv=(sys.executable, "-c", code),
        cwd=str(tmp_path),
        env={"PYTHONUNBUFFERED": "1"},
        timeout_seconds=timeout,
        terminate_grace_seconds=0.15,
        kill_grace_seconds=1,
        stdout_path="logs/stdout.log",
        stderr_path="logs/stderr.log",
    )


def test_real_success_reports_pid_pgid_and_log_paths(tmp_path: Path) -> None:
    observation = LocalRunner().run(
        request(tmp_path, "from pathlib import Path; Path('done').write_text('yes'); print('ok')")
    )
    assert observation.terminal_reason is AttemptTerminalReason.SUCCEEDED
    assert observation.returncode == 0
    assert observation.pid > 0
    assert observation.process_group_id == observation.pid
    assert (tmp_path / "done").read_text(encoding="utf-8") == "yes"
    assert (tmp_path / "logs/stdout.log").read_text(encoding="utf-8") == "ok\n"
    assert not process_group_exists(observation.process_group_id)


def test_timeout_kills_process_group_and_descendant(tmp_path: Path) -> None:
    code = """
import json, os, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
open('pids.json', 'w').write(json.dumps({'parent': os.getpid(), 'child': child.pid}))
time.sleep(60)
"""
    observation = LocalRunner().run(request(tmp_path, code, timeout=0.25))
    pids = json.loads((tmp_path / "pids.json").read_text(encoding="utf-8"))
    assert observation.terminal_reason is AttemptTerminalReason.TIMED_OUT
    assert _await_not_live(tuple(pids.values()))


def test_explicit_cancel_has_one_cancelled_observation(tmp_path: Path) -> None:
    runner = LocalRunner()
    handle = runner.start(request(tmp_path, "import time; time.sleep(60)"))
    assert runner.cancel(handle)
    observation = runner.wait(handle)
    assert observation.terminal_reason is AttemptTerminalReason.CANCELLED
    assert not process_group_exists(observation.process_group_id)


def test_cancel_grace_override_lets_sigterm_handler_checkpoint(tmp_path: Path) -> None:
    code = """
import signal, time
from pathlib import Path
def handler(_sig, _frame):
    Path('checkpoint').write_text('saved')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, handler)
Path('ready').write_text('armed')
time.sleep(60)
"""
    runner = LocalRunner()
    handle = runner.start(request(tmp_path, code))
    deadline = time.monotonic() + 5
    while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.cancel(handle, terminate_grace_seconds=5.0)
    observation = runner.wait(handle)
    assert observation.terminal_reason is AttemptTerminalReason.CANCELLED
    assert (tmp_path / "checkpoint").read_text(encoding="utf-8") == "saved"
    assert not process_group_exists(observation.process_group_id)


def test_cancel_grace_override_rejects_non_positive_or_non_numeric() -> None:
    runner = LocalRunner()
    with pytest.raises(ValueError, match="terminate_grace_seconds must be numeric"):
        runner.cancel("missing-attempt", terminate_grace_seconds="30")
    with pytest.raises(ValueError, match="terminate_grace_seconds must be finite and positive"):
        runner.cancel("missing-attempt", terminate_grace_seconds=0)


def test_long_cancel_grace_returns_promptly_once_the_group_exits(tmp_path: Path) -> None:
    runner = LocalRunner()
    handle = runner.start(request(tmp_path, "import time; time.sleep(60)"))
    started = time.monotonic()
    assert runner.cancel(handle, terminate_grace_seconds=30.0)
    elapsed = time.monotonic() - started
    observation = runner.wait(handle)
    assert observation.terminal_reason is AttemptTerminalReason.CANCELLED
    assert elapsed < 10
    assert not process_group_exists(observation.process_group_id)


def test_hard_killed_runner_parent_guardian_cleans_entire_descendant_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "owned.json"
    payload = """
import json, os, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
open('owned.json', 'w').write(json.dumps({'payload': os.getpid(), 'descendant': child.pid}))
time.sleep(60)
"""
    worker = f"""
import sys
from hypoloom.runner import LocalRunner, ProcessRequest
request = ProcessRequest(
    attempt_id='attempt-parent-death', attempt_ordinal=1, execution_id='execution-parent-death',
    argv=(sys.executable, '-c', {payload!r}), cwd={str(tmp_path)!r},
    env={{'PYTHONUNBUFFERED':'1'}}, timeout_seconds=60,
    terminate_grace_seconds=.1, kill_grace_seconds=1)
LocalRunner().run(request)
"""
    environment = {"PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    parent = subprocess.Popen(
        [sys.executable, "-c", worker],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        pids: dict[str, int] | None = None
        while time.monotonic() < deadline:
            try:
                pids = json.loads(pid_file.read_text(encoding="utf-8"))
                break
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.01)
        assert pids is not None
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=3)
        assert _await_not_live(tuple(pids.values()), timeout=5)
    finally:
        if parent.poll() is None:
            os.kill(parent.pid, signal.SIGKILL)
            parent.wait(timeout=3)


def test_runner_has_no_database_import_or_state_path() -> None:
    source = Path(__file__).resolve().parents[2] / "hypoloom" / "runner.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "sqlite3" not in imports
    assert "state_store" not in imports


def test_process_observation_rejects_impossible_success_contract() -> None:
    with pytest.raises(ValueError, match="requires returncode 0"):
        ProcessObservation(
            attempt_id="attempt-1",
            attempt_ordinal=1,
            execution_id="execution-1",
            terminal_reason=AttemptTerminalReason.SUCCEEDED,
            returncode=17,
            pid=123,
            process_group_id=123,
            started_at=4.0,
            ended_at=5.0,
            stdout_path=None,
            stderr_path=None,
        )


def _await_not_live(pids: tuple[int, ...], timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_pid_live(pid) for pid in pids):
            return True
        time.sleep(0.02)
    return not any(_pid_live(pid) for pid in pids)


def _pid_live(pid: int) -> bool:
    try:
        stat_fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    except OSError:
        return False
    return len(stat_fields) < 3 or stat_fields[2] != "Z"
