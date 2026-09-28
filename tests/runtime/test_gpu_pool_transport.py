"""Unix framing and ownership transport; admission belongs to the pool core."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
from pathlib import Path

import pytest

from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom.gpu_pool import CapacitySample, GpuPool, PoolError
from hypoloom.gpu_pool_client import MAX_MESSAGE_BYTES, PoolClient
from hypoloom.gpu_pool_server import PoolServer


class StubPool:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(exist_ok=True)
        self.calls: list[tuple[int, str, dict]] = []
        self.failure: Exception | None = None

    def handle(self, uid: int, operation: str, arguments: dict) -> dict:
        self.calls.append((uid, operation, arguments))
        if self.failure:
            raise self.failure
        return {"operation": operation, "arguments": arguments}


def exchange(path: Path, payload: bytes, *, shutdown: bool = False) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(path))
        client.sendall(payload)
        if shutdown:
            client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while b"\n" not in response:
            part = client.recv(4096)
            if not part:
                break
            response.extend(part)
        return json.loads(response)


def test_peer_identity_and_errors_are_forwarded_from_one_core(tmp_path: Path) -> None:
    pool = StubPool(tmp_path / "pool")
    path = tmp_path / "pool.sock"
    with PoolServer(pool, path):
        client = PoolClient(path, expected_server_uid=os.geteuid())
        assert client.request("snapshot", {}) == {"operation": "snapshot", "arguments": {}}
        assert pool.calls == [(os.geteuid(), "snapshot", {})]
        pool.failure = PoolError("forbidden", "membership required")
        with pytest.raises(PoolError, match="membership required") as error:
            client.request("reserve", {"allowed_uuids": ["GPU-a"]})
        assert error.value.code == "forbidden"
        pool.failure = RuntimeError("private/path/secret")
        with pytest.raises(PoolError) as error:
            client.request("status", {})
        assert error.value.code == "internal_error"
        assert "secret" not in str(error.value)
    assert not path.exists()


@pytest.mark.parametrize("payload", [
    b'{"operation":"snapshot","arguments":{},"uid":42}\n',
    b'{"operation":"snapshot","operation":"reserve","arguments":{}}\n',
    b'{"operation":"reserve","arguments":{"a":1,"a":2}}\n',
    b'{"operation":"reserve","arguments":{"a":NaN}}\n',
    b'{"operation":"reserve","arguments":{"a":1e999}}\n',
    b'{"operation":"snapshot","arguments":[]}\n',
    b'{"operation":true,"arguments":{}}\n',
    b'{"operation":"snapshot"}\n',
    b'{"operation":"snapshot","arguments":{}}\n{}\n',
    b'\xff\n',
])
def test_invalid_wire_input_never_reaches_core(tmp_path: Path, payload: bytes) -> None:
    pool = StubPool(tmp_path / "pool")
    with PoolServer(pool, tmp_path / "pool.sock") as server:
        assert exchange(server.socket_path, payload)["error"]["code"] == "invalid_request"
    assert pool.calls == []


def test_incomplete_and_oversize_input_never_reach_core(tmp_path: Path) -> None:
    pool = StubPool(tmp_path / "pool")
    with PoolServer(pool, tmp_path / "pool.sock") as server:
        assert exchange(server.socket_path, b'{"operation":"snapshot","arguments":{}}',
                        shutdown=True)["error"]["code"] == "invalid_request"
        assert exchange(server.socket_path, b" " * MAX_MESSAGE_BYTES + b"\n")["error"]["code"] == "invalid_request"
    assert pool.calls == []


def test_idle_connection_times_out_and_following_client_proceeds(tmp_path: Path) -> None:
    pool = StubPool(tmp_path / "pool")
    with PoolServer(pool, tmp_path / "pool.sock") as server:
        server.request_timeout_seconds = 0.05
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as idle:
            idle.settimeout(2)
            idle.connect(str(server.socket_path))
            reply = json.loads(idle.recv(4096))
            assert reply["error"]["code"] == "invalid_request"
        assert PoolClient(server.socket_path).request("status", {})["operation"] == "status"


def test_one_service_lock_per_pool_lives_until_close(tmp_path: Path) -> None:
    pool = StubPool(tmp_path / "pool")
    with PoolServer(pool, tmp_path / "first.sock"):
        with pytest.raises(PoolError) as error:
            PoolServer(pool, tmp_path / "second.sock")
        assert error.value.code == "service_busy"
        assert not (tmp_path / "second.sock").exists()
    with PoolServer(pool, tmp_path / "second.sock") as second:
        assert PoolClient(second.socket_path).request("status", {})["operation"] == "status"


def test_stale_owned_socket_recovers_but_active_other_pool_socket_does_not(tmp_path: Path) -> None:
    path = tmp_path / "pool.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        stale.bind(str(path))
    pool = StubPool(tmp_path / "first")
    other = StubPool(tmp_path / "second")
    with PoolServer(pool, path) as server:
        with pytest.raises(PoolError):
            PoolServer(other, path)
        assert PoolClient(server.socket_path).request("snapshot", {})["operation"] == "snapshot"


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_service_never_unlinks_non_socket_and_failed_init_releases_lock(tmp_path: Path, kind: str) -> None:
    pool = StubPool(tmp_path / "pool")
    path = tmp_path / "unsafe.sock"
    target = tmp_path / "private"
    target.write_text("keep")
    if kind == "file":
        path.write_text("keep")
    else:
        path.symlink_to(target)
    with pytest.raises(PoolError):
        PoolServer(pool, path)
    assert path.read_text() == "keep"
    assert target.read_text() == "keep"
    with PoolServer(pool, tmp_path / "safe.sock"):
        pass


def test_socket_parent_is_explicit_and_permissions_are_scoped(tmp_path: Path) -> None:
    pool = StubPool(tmp_path / "pool")
    with pytest.raises(PoolError):
        PoolServer(pool, tmp_path / "missing" / "pool.sock")
    assert not (tmp_path / "missing").exists()
    directory_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    with PoolServer(pool, tmp_path / "pool.sock", socket_mode=0o660, socket_gid=os.getegid()) as server:
        assert stat.S_IMODE(server.socket_path.stat().st_mode) == 0o660
        assert server.socket_path.stat().st_gid == os.getegid()
        assert stat.S_IMODE(tmp_path.stat().st_mode) == directory_mode


def test_close_preserves_replacement_path_and_interrupts_idle_client(tmp_path: Path) -> None:
    server = PoolServer(StubPool(tmp_path / "pool"), tmp_path / "pool.sock").start()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as idle:
        idle.connect(str(server.socket_path))
        server.socket_path.unlink()
        server.socket_path.write_text("replacement")
        server.close()
    assert server.socket_path.read_text() == "replacement"
    assert server.thread is not None and not server.thread.is_alive()
    server.close()


def test_unavailable_pool_creates_no_fallback(tmp_path: Path) -> None:
    with pytest.raises(PoolError) as error:
        PoolClient(tmp_path / "missing.sock").request("snapshot", {})
    assert error.value.code == "unavailable"
    assert list(tmp_path.iterdir()) == []


def test_client_refuses_wrong_service_uid_before_request(tmp_path: Path) -> None:
    pool = StubPool(tmp_path / "pool")
    with PoolServer(pool, tmp_path / "pool.sock") as server:
        with pytest.raises(PoolError) as error:
            PoolClient(server.socket_path, expected_server_uid=os.geteuid() + 1).request("snapshot", {})
        assert error.value.code == "unauthorized_server"
    assert pool.calls == []


@pytest.mark.parametrize("response", [
    b'{"ok":true,"result":{},"result":{"foreign":42}}\n',
    b'{"ok":true,"result":{"used":Infinity}}\n',
    b'{"ok":true,"result":{"used":1e999}}\n',
    b'{"ok":1,"result":{}}\n',
    b'{"ok":true,"result":[]}\n',
    b'{"ok":false,"error":{"code":1,"message":"bad"}}\n',
    b'{"ok":true,"result":{}}',
    b"x" * (MAX_MESSAGE_BYTES + 1),
])
def test_client_rejects_invalid_server_response(tmp_path: Path, response: bytes) -> None:
    path = tmp_path / "fake.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(1)

        def reply() -> None:
            with listener.accept()[0] as client:
                client.recv(MAX_MESSAGE_BYTES)
                try:
                    client.sendall(response)
                except BrokenPipeError:
                    pass

        worker = threading.Thread(target=reply)
        worker.start()
        try:
            with pytest.raises(PoolError) as error:
                PoolClient(path).request("snapshot", {})
            assert error.value.code == "invalid_response"
        finally:
            worker.join(timeout=2)
        assert not worker.is_alive()


def test_real_pool_rpc_reservation_replay_and_restart(tmp_path: Path) -> None:
    uuid = "GPU-11111111-1111-1111-1111-111111111111"
    sample = CapacitySample((GpuRuntimeObservation(uuid=uuid, total_memory_mib=16000,
        free_memory_mib=12000, used_memory_mib=4000, utilization_percent=75),))

    def make_pool() -> GpuPool:
        return GpuPool(tmp_path / "pool", members={os.geteuid(): [uuid]},
                       reserve_memory_mib=1000, sampler=lambda: sample)

    key = {"namespace": "root-a", "run_id": "run-1", "execution_id": "effect-1", "attempt_id": "attempt-1"}
    request = {"key": key, "allowed_gpu_uuids": [uuid], "gpu_count": 1,
               "workload_memory_mib": 6000, "reserve_memory_mib": 1000}
    path = tmp_path / "pool.sock"
    with PoolServer(make_pool(), path):
        client = PoolClient(path)
        first = client.request("reserve", request)
        assert first["state"] == "RESERVED" and first["gpu_uuids"] == [uuid]
        assert client.request("reserve", request) == first
        snapshot = client.request("snapshot", {})
        assert snapshot["devices"][0]["active_commitments"] == 1
        assert snapshot["devices"][0]["allocatable_memory_mib"] == 5000
        with pytest.raises(PoolError) as error:
            client.request("reserve", {**request, "workload_memory_mib": 7000})
        assert error.value.code == "intent_conflict"
        with pytest.raises(PoolError) as error:
            client.request("status", {"key": {**key, "uid": os.geteuid() + 1}})
        assert error.value.code == "invalid_argument"
    with PoolServer(make_pool(), path):
        client = PoolClient(path)
        assert client.request("status", {"key": key}) == first
        released = client.request("release", {"key": key})
        assert released["state"] == "RELEASED"
        assert client.request("reserve", request) == released


def test_real_pool_denies_unconfigured_peer(tmp_path: Path) -> None:
    uuid = "GPU-11111111-1111-1111-1111-111111111111"
    pool = GpuPool(tmp_path / "pool", members={os.geteuid() + 1: [uuid]},
                   reserve_memory_mib=0, sampler=lambda: CapacitySample(()))
    with PoolServer(pool, tmp_path / "pool.sock") as server:
        with pytest.raises(PoolError) as error:
            PoolClient(server.socket_path).request("snapshot", {})
        assert error.value.code == "unauthorized"


@pytest.mark.parametrize("arguments", [{"value": float("nan")}, {"value": "x" * MAX_MESSAGE_BYTES}])
def test_invalid_client_request_does_not_contact_core(tmp_path: Path, arguments: dict) -> None:
    pool = StubPool(tmp_path / "pool")
    with PoolServer(pool, tmp_path / "pool.sock") as server:
        with pytest.raises(PoolError) as error:
            PoolClient(server.socket_path).request("reserve", arguments)
        assert error.value.code == "invalid_request"
    assert pool.calls == []


def test_pool_preflight_checks_capabilities_and_membership_without_requiring_free_memory():
    from hypoloom.gpu_pool_client import validate_pool_snapshot
    from test_gpu_pool import A, B
    snapshot = {"devices": [{"gpu_uuid": A, "free_memory_mib": 0}, {"gpu_uuid": B}],
                "capabilities": ["resource_replanning/1"]}
    assert validate_pool_snapshot(snapshot, aci_version=28, allowed_gpu_uuids=(B,)) == tuple(sorted((A, B)))
    with pytest.raises(PoolError) as failure:
        validate_pool_snapshot({"devices": snapshot["devices"]}, aci_version=28)
    assert failure.value.code == "incompatible_pool"
    assert validate_pool_snapshot({"devices": snapshot["devices"]}, aci_version=26)
    with pytest.raises(PoolError) as failure:
        validate_pool_snapshot({**snapshot, "devices": snapshot["devices"][:1]}, aci_version=28, allowed_gpu_uuids=(B,))
    assert failure.value.code == "unauthorized"
    with pytest.raises(PoolError) as failure:
        validate_pool_snapshot({**snapshot, "devices": [snapshot["devices"][0]] * 2}, aci_version=28)
    assert failure.value.code == "invalid_response"
