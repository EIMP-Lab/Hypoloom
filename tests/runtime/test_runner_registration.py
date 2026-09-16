from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom import runner as runner_module
from hypoloom.domain import AttemptTerminalReason
from hypoloom.runner import LocalRunner, ProcessRequest, process_group_exists


def _request(tmp_path: Path, *, argv: tuple[str, ...] | None = None) -> ProcessRequest:
    return ProcessRequest(
        attempt_id="registered-attempt", attempt_ordinal=1, execution_id="execution",
        argv=argv or (sys.executable, "-B", "-c", "from pathlib import Path; Path('executed').write_text('yes')"),
        cwd=str(tmp_path), env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
        timeout_seconds=5, terminate_grace_seconds=0.1, kill_grace_seconds=1,
        stdout_path="stdout.log", stderr_path="stderr.log",
    )


def _await(predicate, *, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "process boundary did not settle"
        time.sleep(0.01)


def _live(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_callback_registers_existing_group_before_workload_executes(tmp_path: Path) -> None:
    registered: list[int] = []

    def register(pgid: int) -> None:
        assert process_group_exists(pgid)
        time.sleep(0.1)
        assert not (tmp_path / "executed").exists()
        registered.append(pgid)

    runner = LocalRunner()
    handle = runner.start(_request(tmp_path), before_exec=register)
    try:
        assert registered == [handle.pid] == [handle.process_group_id]
        assert handle._guardian.poll() is None
        observation = runner.wait(handle)
        assert observation.terminal_reason is AttemptTerminalReason.SUCCEEDED
        assert (tmp_path / "executed").read_text() == "yes"
        assert not process_group_exists(handle.pgid)
    finally:
        runner.cleanup(handle)


@pytest.mark.parametrize("failure", [RuntimeError("activation rejected"), KeyboardInterrupt()])
def test_callback_failure_aborts_workload_and_reaps_child_and_guardian(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException,
) -> None:
    spawned: list[subprocess.Popen] = []
    popen = subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(runner_module.subprocess, "Popen", capture)

    def register(pgid: int) -> None:
        assert pgid == spawned[0].pid
        assert len(spawned) == 2
        assert spawned[1].poll() is None
        time.sleep(0.1)
        assert not (tmp_path / "executed").exists()
        raise failure

    runner = LocalRunner()
    with pytest.raises(type(failure)) as caught:
        runner.start(_request(tmp_path), before_exec=register)
    assert caught.value is failure
    assert len(spawned) == 2
    assert all(process.poll() is not None for process in spawned)
    assert not process_group_exists(spawned[0].pid)
    assert not (tmp_path / "executed").exists()
    assert not runner._handles


def test_guardian_spawn_failure_does_not_register_or_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[subprocess.Popen] = []
    registered: list[int] = []
    popen = subprocess.Popen

    def capture(*args, **kwargs):
        if spawned:
            raise OSError("guardian unavailable")
        process = popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(runner_module.subprocess, "Popen", capture)
    with pytest.raises(OSError, match="guardian unavailable"):
        LocalRunner().start(_request(tmp_path), before_exec=registered.append)
    assert not registered
    assert spawned[0].poll() is not None
    assert not process_group_exists(spawned[0].pid)
    assert not (tmp_path / "executed").exists()


def test_registration_bootstrap_does_not_load_workload_python_customization(tmp_path: Path) -> None:
    custom = tmp_path / "python-customization"
    custom.mkdir()
    touched = tmp_path / "customization-executed"
    (custom / "sitecustomize.py").write_text(
        f"from pathlib import Path; Path({str(touched)!r}).write_text('loaded')\n"
    )
    request = replace(_request(tmp_path), env={"PYTHONPATH": str(custom), "PYTHONDONTWRITEBYTECODE": "1"})

    def register(pgid: int) -> None:
        assert process_group_exists(pgid)
        time.sleep(0.1)
        assert not touched.exists()

    runner = LocalRunner()
    handle = runner.start(request, before_exec=register)
    try:
        assert runner.wait(handle).returncode == 0
        assert touched.read_text() == "loaded"
    finally:
        runner.cleanup(handle)


@pytest.mark.parametrize("registered", [False, True])
def test_missing_executable_keeps_direct_launch_errors_and_registered_failures(
    tmp_path: Path, registered: bool,
) -> None:
    runner = LocalRunner()
    request = _request(tmp_path, argv=(str(tmp_path / "missing-executable"),))
    if not registered:
        with pytest.raises(FileNotFoundError):
            runner.start(request)
        return
    groups: list[int] = []
    handle = runner.start(request, before_exec=groups.append)
    try:
        observation = runner.wait(handle)
        assert observation.terminal_reason is AttemptTerminalReason.FAILED
        assert observation.returncode != 0
        assert groups == [handle.pgid]
        assert not process_group_exists(handle.pgid)
    finally:
        runner.cleanup(handle)


@pytest.mark.parametrize("crash_at", ["before_registration", "after_registration", "after_release"])
def test_owner_sigkill_at_launch_boundaries_leaves_no_live_workload(
    tmp_path: Path, crash_at: str,
) -> None:
    payload = """
import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'])
Path('descendant').write_text(str(child.pid))
Path('executed').write_text('yes')
time.sleep(30)
"""
    script = f"""
import json, os, signal, subprocess, sys, time
from pathlib import Path
from hypoloom import runner as module
mode = {crash_at!r}
popen = subprocess.Popen
spawned = []
def capture(*args, **kwargs):
    process = popen(*args, **kwargs)
    spawned.append(process.pid)
    Path('spawned.json').write_text(json.dumps(spawned))
    if mode == 'before_registration' and len(spawned) == 1:
        os.kill(os.getpid(), signal.SIGKILL)
    return process
module.subprocess.Popen = capture
def register(pgid):
    Path('registered').write_text(str(pgid))
    if mode == 'after_registration':
        os.kill(os.getpid(), signal.SIGKILL)
request = module.ProcessRequest(
    attempt_id='crash-attempt', attempt_ordinal=1, execution_id='execution',
    argv=(sys.executable, '-B', '-c', {payload!r}), cwd={str(tmp_path)!r},
    env={{'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}},
    timeout_seconds=30, terminate_grace_seconds=.1, kill_grace_seconds=1,
    stdout_path='stdout.log', stderr_path='stderr.log')
module.LocalRunner().start(request, before_exec=register)
deadline = time.monotonic() + 5
while not Path('executed').exists():
    assert time.monotonic() < deadline
    time.sleep(.01)
os.kill(os.getpid(), signal.SIGKILL)
"""
    environment = {
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
    }
    with (tmp_path / "owner.stderr").open("wb") as stderr:
        owner = subprocess.Popen(
            [sys.executable, "-B", "-c", script], cwd=tmp_path, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
            start_new_session=True,
        )
        try:
            assert owner.wait(timeout=8) == -signal.SIGKILL, (tmp_path / "owner.stderr").read_text()
            spawned = json.loads((tmp_path / "spawned.json").read_text())
            descendant = tmp_path / "descendant"
            owned_pids = spawned + ([int(descendant.read_text())] if descendant.exists() else [])
            _await(lambda: all(not _live(pid) for pid in owned_pids))
            _await(lambda: all(not process_group_exists(pgid) for pgid in spawned))
            assert (tmp_path / "registered").exists() == (crash_at != "before_registration")
            assert (tmp_path / "executed").exists() == (crash_at == "after_release")
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=3)
            spawned_path = tmp_path / "spawned.json"
            if spawned_path.exists():
                for pid in json.loads(spawned_path.read_text()):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


def test_registered_real_bwrap_cpu_runs_only_after_callback(tmp_path: Path) -> None:
    bwrap = shutil.which("bwrap")
    assert bwrap is not None, "Runner registration acceptance requires real bubblewrap"
    output = tmp_path / "output"
    output.mkdir()
    payload = """
from pathlib import Path
assert Path('/output/registered').read_text() == 'yes'
assert not list(Path('/dev').glob('nvidia*'))
Path('/output/executed').write_text(str(sum(range(10000))))
print('registered-cpu-complete', flush=True)
"""
    argv = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for runtime in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(runtime).exists():
            argv.extend(("--ro-bind", runtime, runtime))
    argv.extend(("--bind", str(output), "/output", "--chdir", "/output", "/usr/bin/python3", "-B", "-c", payload))

    def register(pgid: int) -> None:
        assert process_group_exists(pgid)
        time.sleep(0.1)
        assert not (output / "executed").exists()
        (output / "registered").write_text("yes")

    runner = LocalRunner()
    handle = runner.start(_request(tmp_path, argv=tuple(argv)), before_exec=register)
    try:
        observation = runner.wait(handle)
        assert observation.returncode == 0, (tmp_path / "stderr.log").read_text()
        assert (output / "executed").read_text() == "49995000"
        assert (tmp_path / "stdout.log").read_text() == "registered-cpu-complete\n"
        assert not process_group_exists(handle.pgid)
    finally:
        runner.cleanup(handle)
