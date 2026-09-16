"""GPU-P1 service boundaries with simulated capacity, never CUDA or WorkflowHost.

The service, clients, SQLite persistence and Runner/bubblewrap job are real.
Device samples are deliberately synthetic and supply no own-process VRAM credit.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import signal
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom.gpu_pool import CapacitySample, GpuPool, PoolError
from hypoloom.gpu_pool_client import PoolClient
from hypoloom.gpu_pool_server import serve_pool
from hypoloom.runner import LocalRunner, ProcessRequest, process_group_exists


GPU_UUID = "GPU-11111111-2222-3333-4444-555555555555"
_CONTEXT = multiprocessing.get_context("fork")


def _key(namespace: str) -> dict[str, str]:
    # Independent roots deliberately collide in every pre-existing effect ID.
    return {
        "namespace": namespace,
        "run_id": "run-1",
        "execution_id": "execution-1",
        "attempt_id": "attempt-1",
    }


def _intent(namespace: str) -> dict[str, Any]:
    return {
        "key": _key(namespace),
        "allowed_gpu_uuids": [GPU_UUID],
        "gpu_count": 1,
        "workload_memory_mib": 8000,
        "reserve_memory_mib": 1000,
    }


def _serve(root: Path, socket_path: Path, free_memory_mib: int) -> None:
    observation = GpuRuntimeObservation(
        uuid=GPU_UUID,
        total_memory_mib=32000,
        free_memory_mib=free_memory_mib,
        used_memory_mib=32000 - free_memory_mib,
        utilization_percent=70,
    )
    pool = GpuPool(
        root,
        members={os.getuid(): [GPU_UUID]},
        reserve_memory_mib=1000,
        sampler=lambda: CapacitySample(devices=(observation,)),
    )
    serve_pool(pool, socket_path)


class _PoolProcess:
    def __init__(self, root: Path, socket_path: Path, free_memory_mib: int) -> None:
        self.root = root
        self.socket_path = socket_path
        self.free_memory_mib = free_memory_mib
        self.process: Any = None

    @property
    def client(self) -> PoolClient:
        return PoolClient(self.socket_path, timeout_seconds=0.5)

    def start(self) -> None:
        assert self.process is None or not self.process.is_alive()
        self.process = _CONTEXT.Process(
            target=_serve,
            args=(self.root, self.socket_path, self.free_memory_mib),
            name="gpu-p1-pool-service",
        )
        self.process.start()
        deadline = time.monotonic() + 5
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            assert self.process.is_alive(), f"pool exited {self.process.exitcode}"
            try:
                self.client.request("snapshot", {})
                return
            except PoolError as error:
                last_error = error
                time.sleep(0.01)
        pytest.fail(f"pool did not become reachable: {last_error}")

    def kill(self) -> None:
        assert self.process is not None and self.process.is_alive()
        self.process.kill()
        self.process.join(timeout=3)
        assert self.process.exitcode == -signal.SIGKILL

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=3)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=3)
        assert not self.process.is_alive()


@pytest.fixture
def pool_process(tmp_path: Path):
    services: list[_PoolProcess] = []

    def start(free_memory_mib: int = 12000) -> _PoolProcess:
        assert not services, "each fixture has one shared capacity authority"
        service = _PoolProcess(tmp_path / "pool", tmp_path / "p.sock", free_memory_mib)
        services.append(service)
        service.start()
        return service

    yield start
    for service in services:
        service.close()


def _reserve_client(socket_path: Path, namespace: str, release_start: Any, reply: Any) -> None:
    try:
        reply.send({"ready_pid": os.getpid()})
        assert release_start.wait(timeout=5), "client start barrier timed out"
        result = PoolClient(socket_path).request("reserve", _intent(namespace))
        reply.send(result)
    finally:
        reply.close()


def _competing_reserves(socket_path: Path) -> list[dict[str, Any]]:
    release_start = _CONTEXT.Event()
    clients = []
    channels = []
    try:
        for namespace in ("state-root-alpha", "state-root-beta"):
            receive, send = _CONTEXT.Pipe(duplex=False)
            process = _CONTEXT.Process(
                target=_reserve_client,
                args=(socket_path, namespace, release_start, send),
                name=f"gpu-p1-client-{namespace}",
            )
            process.start()
            send.close()
            clients.append(process)
            channels.append(receive)
        ready = []
        for channel in channels:
            assert channel.poll(5), "client did not reach the start barrier"
            ready.append(channel.recv()["ready_pid"])
        assert len(set(ready)) == 2 and os.getpid() not in ready
        release_start.set()
        results = []
        for channel in channels:
            assert channel.poll(5), "reserve RPC did not finish"
            results.append(channel.recv())
        for process in clients:
            process.join(timeout=3)
            assert process.exitcode == 0
        return results
    finally:
        for process in clients:
            if process.is_alive():
                process.kill()
            process.join(timeout=3)
            assert not process.is_alive()
        for channel in channels:
            channel.close()


def _rows(service: _PoolProcess) -> list[dict[str, Any]]:
    # The independent count reads durable rows, not a second admission reducer.
    with sqlite3.connect(f"file:{service.root / 'pool.sqlite3'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY request_seq")]


@pytest.mark.parametrize("free_memory_mib, reserved", [(12000, 1), (24000, 2)])
def test_competing_namespaces_share_one_capacity_authority(
    pool_process, free_memory_mib: int, reserved: int,
) -> None:
    service = pool_process(free_memory_mib)
    results = _competing_reserves(service.socket_path)
    states = Counter(result["state"] for result in results)
    assert states["RESERVED"] == reserved
    assert states["QUEUED"] == 2 - reserved
    rows = _rows(service)
    assert len(rows) == 2
    assert {row["uid"] for row in rows} == {os.getuid()}
    assert {row["namespace"] for row in rows} == {"state-root-alpha", "state-root-beta"}
    assert {(row["run_id"], row["execution_id"], row["attempt_id"]) for row in rows} == {
        ("run-1", "execution-1", "attempt-1"),
    }
    assert sum(json.loads(row["grant_json"]) == [GPU_UUID] for row in rows) == reserved
    for namespace in ("state-root-alpha", "state-root-beta"):
        assert service.client.request("release", {"key": _key(namespace)})["state"] == "RELEASED"
    assert {row["state"] for row in _rows(service)} == {"RELEASED"}


def test_absent_service_fails_without_creating_private_fallback(tmp_path: Path) -> None:
    missing = tmp_path / "unconfigured-pool" / "pool.sock"
    with pytest.raises(PoolError) as caught:
        PoolClient(missing, timeout_seconds=0.1).request("reserve", _intent("root-alpha"))
    assert caught.value.code == "unavailable"
    assert list(tmp_path.iterdir()) == []


def test_killed_service_preserves_reservation_and_same_key_replay(pool_process) -> None:
    service = pool_process()
    original = service.client.request("reserve", _intent("root-alpha"))
    assert original["state"] == "RESERVED"
    service.kill()
    service.start()
    assert service.client.request("reserve", _intent("root-alpha")) == original
    assert service.client.request("reserve", _intent("root-alpha")) == original
    queued = service.client.request("reserve", _intent("root-beta"))
    assert queued["state"] == "QUEUED"
    assert len(_rows(service)) == 2
    assert service.client.request("release", {"key": _key("root-alpha")})["state"] == "RELEASED"
    assert service.client.request("status", {"key": _key("root-beta")})["state"] == "RESERVED"
    assert service.client.request("reserve", _intent("root-alpha"))["state"] == "RELEASED"
    assert len(_rows(service)) == 2
    assert service.client.request("release", {"key": _key("root-beta")})["state"] == "RELEASED"


def test_client_process_exit_does_not_release_its_commitment(pool_process) -> None:
    service = pool_process()
    results = _competing_reserves(service.socket_path)
    reserved = next(result for result in results if result["state"] == "RESERVED")
    queued = next(result for result in results if result["state"] == "QUEUED")
    # Both requesting processes have already exited and closed their sockets.
    assert service.client.request("status", {"key": reserved["key"]})["state"] == "RESERVED"
    assert service.client.request("status", {"key": queued["key"]})["state"] == "QUEUED"
    assert service.client.request("release", {"key": reserved["key"]})["state"] == "RELEASED"
    assert service.client.request("status", {"key": queued["key"]})["state"] == "RESERVED"
    assert service.client.request("release", {"key": queued["key"]})["state"] == "RELEASED"


def test_unbound_launch_remains_held_after_restart(pool_process) -> None:
    service = pool_process()
    assert service.client.request("reserve", _intent("root-alpha"))["state"] == "RESERVED"
    launching = service.client.request("arm", {"key": _key("root-alpha")})
    assert launching["state"] == "LAUNCHING"
    assert launching["process_group_id"] is None
    service.kill()
    service.start()
    unresolved = service.client.request("release", {"key": _key("root-alpha")})
    assert unresolved["state"] == "LAUNCHING"
    assert unresolved["reason"] == "recovery_required"
    assert service.client.request("reserve", _intent("root-beta"))["state"] == "QUEUED"
    assert [(row["state"], row["process_group_id"]) for row in _rows(service)] == [
        ("LAUNCHING", None), ("QUEUED", None),
    ]
    # The unresolved row intentionally remains held; no process was spawned here.
    assert service.client.request("release", {"key": _key("root-beta")})["state"] == "RELEASED"


def test_real_runner_bwrap_group_holds_capacity_until_owned_job_exits(
    pool_process, tmp_path: Path,
) -> None:
    service = pool_process()
    bwrap = shutil.which("bwrap")
    assert bwrap is not None, "GPU-P1 process acceptance requires real bubblewrap"
    output = tmp_path / "cpu-output"
    output.mkdir()
    code = """
import json, time
from pathlib import Path
assert not list(Path('/dev').glob('nvidia*'))
print(json.dumps({'job': 'gpu-p1-owned-cpu', 'sum': sum(range(10000))}), flush=True)
deadline = time.monotonic() + 8
while not Path('/output/finish').exists():
    assert time.monotonic() < deadline, 'CPU test completion deadline expired'
    time.sleep(.01)
print('cpu-complete', flush=True)
"""
    argv = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--proc", "/proc",
            "--dev", "/dev", "--tmpfs", "/tmp"]
    for runtime in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(runtime).exists():
            argv.extend(("--ro-bind", runtime, runtime))
    argv.extend(("--bind", str(output), "/output", "--chdir", "/output",
                 "/usr/bin/python3", "-B", "-u", "-c", code))
    assert service.client.request("reserve", _intent("root-alpha"))["state"] == "RESERVED"
    assert service.client.request("arm", {"key": _key("root-alpha")})["state"] == "LAUNCHING"
    runner = LocalRunner()
    handle = runner.start(ProcessRequest(
        attempt_id="gpu-p1-owned-cpu", attempt_ordinal=1, execution_id="cpu-execution",
        argv=tuple(argv), cwd=str(tmp_path), env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=10, terminate_grace_seconds=0.1, kill_grace_seconds=1,
        stdout_path="cpu.stdout", stderr_path="cpu.stderr",
    ))
    try:
        deadline = time.monotonic() + 3
        stdout = tmp_path / "cpu.stdout"
        while "gpu-p1-owned-cpu" not in stdout.read_text(encoding="utf-8"):
            assert handle.returncode is None, (tmp_path / "cpu.stderr").read_text()
            assert time.monotonic() < deadline, "sandboxed CPU job did not start"
            time.sleep(0.01)
        assert process_group_exists(handle.process_group_id)
        bound = service.client.request("bind_process", {
            "key": _key("root-alpha"), "process_group_id": handle.process_group_id,
        })
        assert bound["state"] == "BOUND"
        assert bound["process_group_id"] == handle.process_group_id
        service.kill()
        service.start()
        refused = service.client.request("release", {"key": _key("root-alpha")})
        assert refused["state"] == "BOUND"
        assert refused["reason"] == "process_present"
        assert service.client.request("reserve", _intent("root-beta"))["state"] == "QUEUED"
        (output / "finish").write_text("finish\n", encoding="utf-8")
        observation = runner.wait(handle)
        assert observation.returncode == 0, (tmp_path / "cpu.stderr").read_text()
        assert "cpu-complete" in stdout.read_text(encoding="utf-8")
        assert not process_group_exists(handle.process_group_id)
        assert service.client.request("release", {"key": _key("root-alpha")})["state"] == "RELEASED"
        assert service.client.request("status", {"key": _key("root-beta")})["state"] == "RESERVED"
        assert service.client.request("release", {"key": _key("root-beta")})["state"] == "RELEASED"
        assert len(_rows(service)) == 2
        assert {row["state"] for row in _rows(service)} == {"RELEASED"}
    finally:
        runner.cancel(handle)
        runner.wait(handle)
        assert not process_group_exists(handle.process_group_id)
