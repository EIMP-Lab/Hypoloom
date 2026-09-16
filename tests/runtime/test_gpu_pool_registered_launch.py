"""Real pool/Runner/bwrap boundaries with synthetic GPU capacity, never CUDA.

The crash owner is a launch-callback simulator, not WorkflowHost. Raw SQLite
rows, workload artifacts and OS group absence independently count the effects.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hypoloom.gpu_pool import GpuPool, PoolError
from hypoloom.oracle import audit_gpu_pool
from hypoloom.runner import LocalRunner, ProcessRequest, process_group_exists

import test_gpu_pool_process as pool_process_module
from test_gpu_pool_process import _PoolProcess, _intent, _key, _rows, _serve


@pytest.fixture
def service(tmp_path):
    pool = _PoolProcess(tmp_path / "pool", tmp_path / "pool.sock", 12000)
    try:
        pool.start()
        yield pool
    finally:
        pool.close()


def _await(predicate, message):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, message
        time.sleep(0.01)


def _request(tmp_path, *, wait=False):
    output = tmp_path / "output"
    output.mkdir()
    bwrap = shutil.which("bwrap")
    assert bwrap is not None, "registered pool launch acceptance requires real bubblewrap"
    code = f"""
import time
from pathlib import Path
assert not list(Path('/dev').glob('nvidia*'))
Path('/output/executed').write_text(str(sum(range(10000))))
deadline = time.monotonic() + 15
while {wait!r} and not Path('/output/finish').exists():
    assert time.monotonic() < deadline, 'test completion deadline expired'
    time.sleep(.01)
print('registered-pool-cpu-complete', flush=True)
"""
    argv = [bwrap, "--unshare-all", "--die-with-parent", "--new-session",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for runtime in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(runtime).exists():
            argv.extend(("--ro-bind", runtime, runtime))
    argv.extend(("--bind", str(output), "/output", "--chdir", "/output",
                 "/usr/bin/python3", "-B", "-u", "-c", code))
    return ProcessRequest(
        attempt_id="attempt-1", attempt_ordinal=1, execution_id="execution-1",
        argv=tuple(argv), cwd=str(tmp_path),
        env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
        timeout_seconds=20, terminate_grace_seconds=0.1, kill_grace_seconds=1,
        stdout_path="cpu.stdout", stderr_path="cpu.stderr",
    )


def _activate(service, pgid):
    result = service.client.request("activate", {
        "key": _key("root-alpha"), "process_group_id": pgid,
    })
    if result["state"] != "BOUND" or result["process_group_id"] != pgid:
        raise PoolError("resource_wait", result["reason"] or "activation did not bind")
    return result


def _closed(service, count):
    stored = _rows(service)
    assert len(stored) == count
    assert {row["state"] for row in stored} == {"RELEASED"}
    assert all(row["process_group_id"] is None or not process_group_exists(row["process_group_id"])
               for row in stored)
    report = audit_gpu_pool(service.root)
    assert report["ok"], report
    assert report["counts"] == {"QUEUED": 0, "RESERVED": 0, "LAUNCHING": 0,
                                "BOUND": 0, "RELEASED": count, "requests": count}


def test_registered_bwrap_cpu_retains_binding_across_service_sigkill_restart(service, tmp_path):
    assert service.client.request("reserve", _intent("root-alpha"))["state"] == "RESERVED"
    request = _request(tmp_path, wait=True)
    registered = []

    def register(pgid):
        assert process_group_exists(pgid)
        assert not (tmp_path / "output/executed").exists()
        registered.append(pgid)
        bound = _activate(service, pgid)
        assert bound["gpu_uuids"] == _intent("root-alpha")["allowed_gpu_uuids"]
        assert [row["process_group_id"] for row in _rows(service)] == [pgid]
        assert not (tmp_path / "output/executed").exists()

    runner = LocalRunner()
    handle = runner.start(request, before_exec=register)
    try:
        assert registered == [handle.pgid]
        _await(lambda: (tmp_path / "output/executed").exists(), "bwrap CPU did not execute")
        assert (tmp_path / "output/executed").read_text() == "49995000"
        assert process_group_exists(handle.pgid)
        assert audit_gpu_pool(service.root)["ok"]
        service.kill()
        service.start()
        retained = service.client.request("release", {"key": _key("root-alpha")})
        assert retained["state"] == "BOUND"
        assert retained["process_group_id"] == handle.pgid
        assert retained["reason"] == "process_present"
        assert service.client.request("reserve", _intent("root-beta"))["state"] == "QUEUED"
        (tmp_path / "output/finish").write_text("finish")
        observation = runner.wait(handle)
        assert observation.returncode == 0, (tmp_path / "cpu.stderr").read_text()
        assert (tmp_path / "cpu.stdout").read_text() == "registered-pool-cpu-complete\n"
        assert not process_group_exists(handle.pgid)
        assert service.client.request("release", {"key": _key("root-alpha")})["state"] == "RELEASED"
        assert service.client.request("status", {"key": _key("root-beta")})["state"] == "RESERVED"
        assert service.client.request("release", {"key": _key("root-beta")})["state"] == "RELEASED"
        _closed(service, 2)
    finally:
        runner.cleanup(handle)
        assert not process_group_exists(handle.pgid)


@pytest.mark.parametrize("failure", ["callback", "capacity_wait"])
def test_failed_registration_never_executes_workload_and_can_withdraw(service, tmp_path, failure):
    assert service.client.request("reserve", _intent("root-alpha"))["state"] == "RESERVED"
    if failure == "capacity_wait":
        service.kill()
        service.free_memory_mib = 8500
        service.start()
    groups = []

    def register(pgid):
        groups.append(pgid)
        assert process_group_exists(pgid)
        assert not (tmp_path / "output/executed").exists()
        if failure == "callback":
            raise PoolError("registration_failed", "injected registration failure")
        _activate(service, pgid)

    with pytest.raises(PoolError) as error:
        LocalRunner().start(_request(tmp_path), before_exec=register)
    assert error.value.code == ("registration_failed" if failure == "callback" else "resource_wait")
    assert len(groups) == 1 and not process_group_exists(groups[0])
    assert not (tmp_path / "output/executed").exists()
    stored = _rows(service)
    assert [(row["state"], row["process_group_id"]) for row in stored] == [("RESERVED", None)]
    if failure == "capacity_wait":
        assert stored[0]["reason"] == "capacity_unavailable"
    assert service.client.request("release", {"key": _key("root-alpha")})["state"] == "RELEASED"
    _closed(service, 1)


def _serve_losing_activation_reply(root, socket_path, free_memory_mib):
    original = GpuPool.handle

    def handle(pool, uid, operation, arguments):
        result = original(pool, uid, operation, arguments)
        if operation == "activate":
            assert result["state"] == "BOUND"
            os._exit(73)  # The committed transaction survives; the Unix reply does not.
        return result

    GpuPool.handle = handle
    _serve(root, socket_path, free_memory_mib)


def test_lost_committed_activation_reply_aborts_child_then_reconciles_same_key(
    service, tmp_path, monkeypatch,
):
    assert service.client.request("reserve", _intent("root-alpha"))["state"] == "RESERVED"
    service.kill()
    with monkeypatch.context() as patch:
        patch.setattr(pool_process_module, "_serve", _serve_losing_activation_reply)
        service.start()
    groups = []

    def register(pgid):
        groups.append(pgid)
        _activate(service, pgid)

    with pytest.raises(PoolError) as error:
        LocalRunner().start(_request(tmp_path), before_exec=register)
    assert error.value.code in {"invalid_response", "unavailable"}
    service.process.join(timeout=3)
    assert service.process.exitcode == 73
    assert len(groups) == 1 and not process_group_exists(groups[0])
    assert not (tmp_path / "output/executed").exists()
    assert [(row["state"], row["process_group_id"]) for row in _rows(service)] == [("BOUND", groups[0])]
    service.start()
    assert service.client.request("status", {"key": _key("root-alpha")})["process_group_id"] == groups[0]
    assert service.client.request("release", {"key": _key("root-alpha")})["state"] == "RELEASED"
    _closed(service, 1)


@pytest.mark.parametrize("crash_at", ["before_activation", "after_activation"])
def test_owner_exit_around_activation_prevents_exec_and_reconciles(service, tmp_path, crash_at):
    assert service.client.request("reserve", _intent("root-alpha"))["state"] == "RESERVED"
    request = _request(tmp_path)
    script = f"""
import json, os, subprocess
from pathlib import Path
from hypoloom import runner
from hypoloom.gpu_pool_client import PoolClient
popen = subprocess.Popen
spawned = []
def capture(*args, **kwargs):
    process = popen(*args, **kwargs)
    spawned.append(process.pid)
    Path('spawned.json').write_text(json.dumps(spawned))
    return process
runner.subprocess.Popen = capture
def register(pgid):
    Path('registered').write_text(str(pgid))
    assert not Path('output/executed').exists()
    if {crash_at!r} == 'before_activation':
        os._exit(74)
    bound = PoolClient({str(service.socket_path)!r}).request('activate', {{
        'key': {_key('root-alpha')!r}, 'process_group_id': pgid,
    }})
    assert bound['state'] == 'BOUND' and bound['process_group_id'] == pgid
    assert not Path('output/executed').exists()
    os._exit(75)
runner.LocalRunner().start(runner.ProcessRequest.from_dict({request.to_dict()!r}), before_exec=register)
raise AssertionError('owner unexpectedly returned from callback')
"""
    environment = {"PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    with (tmp_path / "owner.stderr").open("wb") as stderr:
        owner = subprocess.Popen([sys.executable, "-B", "-c", script], cwd=tmp_path,
                                 env=environment, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
        try:
            assert owner.wait(timeout=8) == (74 if crash_at == "before_activation" else 75), (
                tmp_path / "owner.stderr").read_text()
            pgid = int((tmp_path / "registered").read_text())
            spawned = json.loads((tmp_path / "spawned.json").read_text())
            assert len(spawned) == 2 and spawned[0] == pgid
            _await(lambda: all(not process_group_exists(group) for group in spawned),
                   "held child or guardian survived owner exit")
            assert not process_group_exists(owner.pid)
            assert not (tmp_path / "output/executed").exists()
            expected = ("RESERVED", None) if crash_at == "before_activation" else ("BOUND", pgid)
            assert [(row["state"], row["process_group_id"]) for row in _rows(service)] == [expected]
            assert service.client.request("release", {"key": _key("root-alpha")})["state"] == "RELEASED"
            _closed(service, 1)
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=3)
            spawned_path = tmp_path / "spawned.json"
            if spawned_path.exists():
                for group in json.loads(spawned_path.read_text()):
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
