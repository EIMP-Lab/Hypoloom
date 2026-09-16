from __future__ import annotations

import json
import os
import pytest

from hypoloom import cli, compute_resources


GPU = "GPU-11111111-1111-1111-1111-111111111111"
GPU_2 = "GPU-22222222-2222-2222-2222-222222222222"


def test_pool_memory_sampler_only_queries_explicit_devices(monkeypatch):
    seen = []

    def observe(uuid):
        seen.append(uuid)
        return compute_resources.GpuRuntimeObservation(
            uuid=uuid, total_memory_mib=16000, free_memory_mib=9000,
            used_memory_mib=7000, utilization_percent=95,
        )

    monkeypatch.setattr(compute_resources, "_nvml_runtime_observation", observe)
    sampler = compute_resources.observe_authorized_gpu_memory
    assert sampler(()) == ()
    samples = sampler((GPU_2, GPU))
    assert seen == [GPU, GPU_2]
    assert samples[0].utilization_percent == 95  # occupied is not forbidden
    assert samples[0].used_memory_mib == 7000  # device use, not own use


@pytest.mark.parametrize("devices", ["GPU", (GPU, GPU), ("/dev/nvidia0",), (None,)])
def test_pool_memory_sampler_validates_before_nvml(monkeypatch, devices):
    def observe(uuid):
        raise AssertionError("must not query NVML for invalid input")

    monkeypatch.setattr(compute_resources, "_nvml_runtime_observation", observe)
    with pytest.raises(ValueError):
        compute_resources.observe_authorized_gpu_memory(devices)


def test_pool_serve_wires_explicit_membership_without_runtime_or_deployment(tmp_path, monkeypatch):
    from hypoloom import gpu_pool_server

    seen = {}
    monkeypatch.setattr(compute_resources, "observe_authorized_gpu_memory", lambda devices: ())

    def serve(pool, socket_path, *, socket_gid=None):
        seen.update(pool=pool, socket=socket_path, gid=socket_gid)

    monkeypatch.setattr(gpu_pool_server, "serve_pool", serve)
    root = tmp_path / "pool"
    sock = tmp_path / "pool.sock"
    assert cli.main([
        "gpu-pool-serve", "--pool-root", str(root), "--socket-path", str(sock),
        "--member", f"{os.getuid()}={GPU}", "--reserve-mib", "1024",
    ]) == 0
    assert seen["pool"].root == root
    assert seen["socket"] == sock
    assert seen["gid"] is None
    assert not (root / "state.sqlite").exists()
    assert not sock.exists()  # monkeypatched service: no hidden launcher


@pytest.mark.parametrize("member", ["wrong", "-1=" + GPU, "5=/dev/nvidia0", "5=", "5=" + GPU + "," + GPU])
def test_pool_cli_rejects_bad_members_before_writes(tmp_path, member):
    root = tmp_path / "pool"
    with pytest.raises(SystemExit) as stopped:
        cli.main(["gpu-pool-serve", "--pool-root", str(root),
                  "--socket-path", str(tmp_path / "pool.sock"), "--member", member])
    assert stopped.value.code == 2
    assert not root.exists()


def test_pool_status_is_a_client_and_has_no_private_fallback(tmp_path, capsys):
    sock = tmp_path / "absent.sock"
    assert cli.main(["gpu-pool-status", "--socket-path", str(sock)]) == 2
    output = json.loads(capsys.readouterr().err)
    assert output["status"] == "error"
    assert not sock.exists()
    assert list(tmp_path.iterdir()) == []


def test_pool_status_passes_reviewed_server_uid_without_starting_service(tmp_path, monkeypatch, capsys):
    from hypoloom import gpu_pool_client
    seen = {}
    class Client:
        def __init__(self, path, **kwargs):
            seen.update(path=path, **kwargs)
        def request(self, operation, arguments):
            assert (operation, arguments) == ("snapshot", {})
            return {"devices": []}
    monkeypatch.setattr(gpu_pool_client, "PoolClient", Client)
    sock = tmp_path / "reviewed.sock"
    assert cli.main(["gpu-pool-status", "--socket-path", str(sock),
                     "--expected-server-uid", "1234"]) == 0
    assert seen == {"path": sock, "timeout_seconds": 5.0, "expected_server_uid": 1234}
    assert json.loads(capsys.readouterr().out) == {"devices": []}
    assert list(tmp_path.iterdir()) == []


def test_pool_audit_of_absent_root_is_read_only(tmp_path, capsys):
    root = tmp_path / "absent"
    assert cli.main(["gpu-pool-audit", "--pool-root", str(root)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert not root.exists()


@pytest.mark.parametrize("server_uid", [None, 0, 1001])
def test_serve_forwards_explicit_pool_binding_without_starting_service(tmp_path, monkeypatch, server_uid):
    from hypoloom import gpu_pool_server, workflow_host

    seen = {}

    def serve(state_root, **options):
        seen.update(state_root=state_root, **options)
        return 17

    def forbidden(*args, **kwargs):
        raise AssertionError("Host options must not start the GPU pool service")

    monkeypatch.setattr(workflow_host, "serve", serve)
    monkeypatch.setattr(gpu_pool_server, "serve_pool", forbidden)
    sock = tmp_path / "operator-pool.sock"
    state = tmp_path / "state"
    arguments = ["serve", "--state-root", str(state), "--gpu-pool-socket", str(sock),
        "--gpu-pool-namespace", "operator-root-alpha"]
    if server_uid is not None:
        arguments.extend(("--gpu-pool-server-uid", str(server_uid)))
    assert cli.main(arguments) == 17
    assert seen["gpu_pool_socket"] == sock
    assert seen["gpu_pool_namespace"] == "operator-root-alpha"
    assert seen["gpu_pool_server_uid"] == server_uid
    assert not sock.exists()
    assert not state.exists()


def test_serve_without_pool_options_remains_an_unconfigured_host(tmp_path, monkeypatch):
    from hypoloom import workflow_host

    seen = {}

    def serve(state_root, **options):
        seen.update(options)
        return 0

    monkeypatch.setattr(workflow_host, "serve", serve)
    assert cli.main(["serve", "--state-root", str(tmp_path / "state")]) == 0
    assert all(seen[name] is None for name in ("gpu_pool_socket", "gpu_pool_namespace", "gpu_pool_server_uid"))


@pytest.mark.parametrize("options", [
    ("--gpu-pool-socket", "pool.sock"),
    ("--gpu-pool-namespace", "root-alpha"),
    ("--gpu-pool-server-uid", "1001"),
    ("--gpu-pool-namespace", "root-alpha", "--gpu-pool-server-uid", "1001"),
    ("--gpu-pool-socket", "pool.sock", "--gpu-pool-namespace", "root-alpha", "--gpu-pool-server-uid", "-1"),
    ("--gpu-pool-socket", "", "--gpu-pool-namespace", "root-alpha"),
    ("--gpu-pool-socket", "pool.sock", "--gpu-pool-namespace", ""),
    ("--gpu-pool-socket", "pool.sock", "--gpu-pool-namespace", "/a/private/root"),
])
def test_serve_rejects_incomplete_or_invalid_pool_configuration_before_writes(tmp_path, monkeypatch, options):
    from hypoloom import workflow_host

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid pool binding must not construct a Host")

    monkeypatch.setattr(workflow_host, "serve", forbidden)
    state = tmp_path / "state"
    with pytest.raises(SystemExit) as stopped:
        cli.main(["serve", "--state-root", str(state), *options])
    assert stopped.value.code == 2
    assert not state.exists()
