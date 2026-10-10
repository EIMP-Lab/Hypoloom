"""Managed maintenance followed by real native Host/CPU publication.

Only the supervisor and device telemetry are substituted. Pool protocol, locks,
SQLite, native CC, broker, bwrap and CPU execution use their production paths.
"""
import json
import fcntl
import os
from pathlib import Path
import secrets
import tempfile
import time

from hypoloom import managed_gpu_pool as manager
from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom.control_protocol import ControlRequest
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_client import PoolClient
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.oracle import Oracle, audit_gpu_pool
from hypoloom.runner import process_group_exists

from test_managed_pool_lifecycle import managed, GPU  # noqa: F401
from test_pool_init_review import rows
from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import _DeclaredScenario, _NoPublicWeb, _six_yaml_profile_v4_payload
import test_full_cc_host_g3 as native

_PROBE = manager._probe_snapshot
_SOCKET_STATE = manager._socket_state


def test_native_pool_init(managed, monkeypatch, tmp_path):
    executable = _installed_claude()
    args, state, root = managed
    repo = root.parents[2]
    args.reserve_mib = 4096
    sample = CapacitySample(devices=(GpuRuntimeObservation(uuid=GPU, total_memory_mib=16000,
        free_memory_mib=16000, used_memory_mib=0, utilization_percent=0),))
    servers = []
    def launch(plan, *_):
        assert not state["active"]
        pool = GpuPool(plan.pool_root, members=dict(plan.members), reserve_memory_mib=plan.reserve_mib,
            sampler=lambda: sample)
        server = PoolServer(pool, plan.socket_path).start()
        servers.append(server)
        state.update(active=True, legacy=False, pool=pool)
        state["events"].append("launch")
    def stop(*_):
        servers[-1].close()
        state["active"] = False
        state["events"].append("stop")
        return True
    monkeypatch.setattr(manager, "_launch_pool", launch)
    monkeypatch.setattr(manager, "_stop_unit", stop)
    monkeypatch.setattr(manager, "_probe_snapshot", _PROBE)
    monkeypatch.setattr(manager, "_socket_state", _SOCKET_STATE)
    host = provider = controller = None
    try:
        assert manager.ensure(args)[1] == "ready"
        client = PoolClient(root / "control.sock", expected_server_uid=os.geteuid())
        key = {"namespace": "old", "run_id": "retired", "execution_id": "old-exec", "attempt_id": "old-attempt"}
        granted = client.request("reserve", {"key": key, "allowed_gpu_uuids": [GPU], "gpu_count": 1,
            "workload_memory_mib": 1024, "reserve_memory_mib": 512})
        assert granted["state"] == "RESERVED"
        assert client.request("release", {"key": key})["state"] == "RELEASED"
        before = rows(root)
        args.reviewed_action, args.reserve_mib = "reconfigure", 512
        assert not manager.review(args)["prepare_available"]
        args.pool_retired_owner = ["old/retired"]
        assert manager.review(args)["prepare_available"]
        assert manager.ensure(args)[1] == "reconfigured"
        assert client.request("snapshot", {})["devices"][0]["pool_reserve_memory_mib"] == 512
        assert rows(root) == before and state["events"] == ["launch", "stop", "launch"]
        assert manager.ensure(args)[1] == "reused" and len(servers) == 2

        trial = tmp_path / "trial"
        trial.mkdir()
        run = "new-run"
        state_root = repo / "state" / run
        original_host = native.WorkflowHost
        def configured_host(_state_root, **kwargs):
            return original_host(state_root, **kwargs, gpu_pool_socket=root / "control.sock",
                gpu_pool_namespace=run, gpu_pool_server_uid=os.geteuid())
        monkeypatch.setattr(native, "WorkflowHost", configured_host)
        native._FILE_BACKED_STATE_ROOTS.add(state_root.resolve())
        scenario = _DeclaredScenario(trial, "pool-maintenance")
        secret = "pool-native-" + secrets.token_hex(12)
        provider, thread = native._start_provider(scenario, secret)
        (trial / "settings.json").write_text("{}\n")
        (trial / "settings.json").chmod(0o600)
        host = native._new_host(trial, executable, f"http://127.0.0.1:{provider.server_port}", secret)
        host.grounding_web = _NoPublicWeb()
        with tempfile.TemporaryDirectory(prefix="pool-native-", dir="/var/tmp") as mount:
            payload = _six_yaml_profile_v4_payload(trial, Path(mount), run)
            host.initialize()
            controller = (state_root / "controller.lock").open("a+")
            os.chmod(state_root / "controller.lock", 0o600)
            fcntl.flock(controller, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload)).status == "accepted"
            # The new CPU-only run already binds the pool without any GPU request.
            args.reserve_mib = 768
            blocked = manager.review(args)
            assert not blocked["prepare_available"]
            assert {"run_id": run, "reason": "run_depends_on_pool"} in blocked["dependencies"]["blockers"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
                time.sleep(.01)
            assert host.store.latest_transition(run)["state"] == "COMPLETED"
            attempts = host.store.list_attempts()
            agents = [a for a in attempts if a["kind"] == "AGENT"]
            processes = [a for a in attempts if a["kind"] == "PROCESS"]
            assert len(agents) == 11 and len(processes) == 1
            assert all(a["state"] == "CLOSED" and a["terminal_reason"] == "SUCCEEDED" for a in attempts)
            assert all(not process_group_exists(a["process_group_id"]) for a in attempts if a["process_group_id"])
            outcomes = host.store.list_facts(run, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 1 and outcomes[0]["payload"]["metric_value"] == "-0.1"
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run)
            assert audit.ok and audit.open_attempt_count == audit.pending_effect_count == 0, audit.to_dict()
            assert audit_gpu_pool(root)["ok"] and rows(root) == before
            host.close()
            assert {"run_id": run, "reason": "host_present_or_unknown"} in manager.review(args)["dependencies"]["blockers"]
            controller.close()
            controller = None
            # After Host ownership drains, the same reviewed maintenance is possible.
            assert manager.review(args)["prepare_available"]
            (tmp_path / "census.json").write_text(json.dumps({"agents": len(agents), "processes": len(processes),
                "outcomes": len(outcomes), "pool_requests": len(before["requests"]),
                "service_events": state["events"], "oracle": audit.to_dict()}, indent=2))
    finally:
        if host: host.close()
        if controller: controller.close()
        if provider:
            provider.shutdown(); provider.server_close(); thread.join(timeout=5)
        for server in servers: server.close()
