"""Real Host/store/pool decisions with synthetic GPU inventory and execution."""
import json
import os
from pathlib import Path

import pytest

from hypoloom.compute_resources import ComputeCapabilities, GpuCapability, GpuRuntimeObservation, LocalComputeResources
from hypoloom.control_protocol import ControlRequest
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_client import PoolClient
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.oracle import audit_gpu_pool
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost
from test_gpu_host_recovery import _DeterministicRunner

GPU_A = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GPU_B = "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python").write_bytes(b"synthetic-runtime-not-executable")
    (runtime / "bin/python").chmod(0o755)
    inventory = ComputeCapabilities(gpus=tuple(GpuCapability(index=i, uuid=u, name="synthetic",
        total_memory_mib=24000, free_memory_mib=20000-i*1000, driver_version="test") for i,u in enumerate((GPU_A,GPU_B))))
    free = {GPU_A: 18000, GPU_B: 16000}
    def sample(uuid):
        return GpuRuntimeObservation(uuid=uuid,total_memory_mib=24000,free_memory_mib=free[uuid],
                                     used_memory_mib=24000-free[uuid],utilization_percent=60)
    pool = GpuPool(tmp_path / "pool", members={os.getuid(): [GPU_A,GPU_B]}, reserve_memory_mib=1000,
                   sampler=lambda: CapacitySample(devices=tuple(sample(u) for u in (GPU_A,GPU_B))))
    socket = tmp_path / "pool.sock"
    server = PoolServer(pool,socket)
    server.start()
    hosts = []
    def host(namespace):
        resources = LocalComputeResources(allowed_gpu_uuids=(GPU_A,GPU_B),gpu_runtime_prefix=runtime,
            inventory_provider=lambda: inventory,observation_provider=sample)
        result = WorkflowHost(tmp_path / namespace,compute_resources=resources,
                              gpu_pool_socket=socket,gpu_pool_namespace=namespace)
        result.runner = _DeterministicRunner(complete=False)
        result.initialize()
        hosts.append(result)
        return result
    try:
        yield host,free,pool,runtime
    finally:
        for item in hosts: item.close()
        server.close()


def create(host, runtime, name="shared"):
    admitted = admit_profile(ROOT / "tests/fixtures/profiles/component-demo",run_id=name,allow_gpu=True)
    assert admitted.admitted
    payload = admitted.create_run_payload
    payload["research_contract"]["runtime"]["conda_env"] = str(runtime)
    payload["execution"]["runtime_prefix"] = str(runtime)
    payload["research_contract"]["budget"].update(min_vram_per_experiment=8000,gpu_vram_reserve_mb=1000)
    result = host.handle(ControlRequest(1,"create-"+name,0,"CREATE_RUN",payload))
    assert result.status == "accepted", result


@pytest.mark.parametrize("allow_gpu", [False, True])
def test_initial_memory_gpu_mode_requires_pool_before_creating_a_run(tmp_path, allow_gpu):
    admitted = admit_profile(ROOT / "tests/fixtures/profiles/component-demo", run_id="initial", allow_gpu=allow_gpu)
    assert admitted.admitted, admitted.to_dict()
    payload = admitted.create_run_payload
    payload["research_contract"]["budget"].update(min_vram_per_experiment=1024, initial_vram_per_experiment=8192)
    host = WorkflowHost(tmp_path / "host")
    try:
        host.initialize()
        result = host.handle(ControlRequest(1, "create-initial", 0, "CREATE_RUN", payload))
        if allow_gpu:
            assert result.status == "rejected"
            assert "initial_vram_per_experiment requires an explicitly configured shared GPU pool" in json.dumps(result.to_mapping())
            assert not host.store.list_workflow_heads("initial")
        else:
            assert result.status == "accepted", result
    finally:
        host.close()


def until(host,predicate):
    for _ in range(160):
        if predicate(): return
        # Complete validation, but leave real experiment-shaped fake jobs OPEN.
        for handle in host.running.values():
            if handle.request.execution_id.endswith("--candidate-validation"):
                handle.returncode = 0
        host.tick()
    raise AssertionError("Host did not reach expected state")


def specs(host):
    return host.store.list_facts("shared","ExperimentPreregistered")


def test_shared_run_freezes_intent_and_dispatches_using_fresh_pool_choice(environment):
    make,free,pool,runtime = environment
    host = make("alpha")
    create(host,runtime)
    until(host,lambda: bool(specs(host)))
    original = specs(host)[0]
    contract = original["payload"]["execution_contract"]
    assert host.version.research_contract_version == "hypoloom-research-v12"
    assert "resource_grant" not in contract
    assert contract["resource_intent"]["allowed_gpu_uuids"] == [GPU_A,GPU_B]
    free[GPU_A] = 2000
    until(host,lambda: any(not h.request.execution_id.endswith("--candidate-validation") for h in host.running.values()))
    attempt = next(row for row in host.store.list_attempts() if row["kind"]=="PROCESS" and row["execution_id"]==original["execution_id"])
    intent = next(json.loads(row["payload_json"]) for row in host.store.inspect("SELECT payload_json FROM workflow_transitions")
                  if json.loads(row["payload_json"]).get("attempt_id")==attempt["attempt_id"])
    assert intent["resource_grant"] == {"kind":"GPU","gpu_uuid":GPU_B}
    assert intent["gpu_pool_key"]["namespace"] == "alpha"
    assert specs(host)[0] == original
    assert audit_gpu_pool(pool.root)["ok"]


def test_status_discloses_the_stored_pool_binding_without_querying_live_capacity(environment, monkeypatch):
    from hypoloom.monitor import build_monitor_snapshot
    make, _, _, runtime = environment
    host = make("visible-binding")
    create(host, runtime)
    expected = host.shared_gpu.configuration

    def unavailable(*args, **kwargs):
        raise AssertionError("binding display must not depend on live pool availability")

    monkeypatch.setattr(host.shared_gpu.client, "request", unavailable)
    result = host.handle(ControlRequest(1, "status-binding", None, "STATUS", {"run_id": "shared"}))
    assert result.status == "accepted" and result.result["gpu_pool"] == expected
    assert build_monitor_snapshot(host.state_root, "shared").status["gpu_pool"] == expected


def test_shared_gpu_wait_has_no_process_attempt_and_other_root_uses_same_pool(environment):
    make,free,pool,runtime = environment
    free.update({GPU_A:12000,GPU_B:1000})
    first,second = make("alpha"),make("beta")
    for item in (first,second): create(item,runtime)
    until(first,lambda: bool(specs(first)) and any(not h.request.execution_id.endswith("--candidate-validation") for h in first.running.values()))
    until(second,lambda: bool(specs(second)) and bool(second.resource_waits))
    execution = specs(second)[0]["execution_id"]
    assert not second.store.list_attempts(execution_id=execution)
    assert not second.store.list_facts("shared","CanonicalOutcomeAccepted")
    assert audit_gpu_pool(pool.root)["ok"]


def test_unreachable_configured_pool_waits_without_cpu_fallback(environment, tmp_path):
    make, _, _, runtime = environment
    host = make("unavailable")
    create(host, runtime)  # Readiness succeeds; the service disappears during this run.
    host.shared_gpu.client = PoolClient(tmp_path / "absent.sock")
    until(host, lambda: bool(specs(host)) and bool(host.resource_waits))
    frozen = specs(host)[0]
    assert frozen["payload"]["execution_contract"]["resource_intent"]["kind"] == "GPU"
    assert not host.store.list_attempts(execution_id=frozen["execution_id"])
    assert not host.store.list_facts("shared", "CanonicalOutcomeAccepted")
    assert "unavailable" in host.resource_waits[frozen["experiment_id"]][1]


def test_unreachable_pool_does_not_block_explicit_cpu_run(environment, tmp_path):
    make, _, _, _ = environment
    host = make("cpu")
    host.shared_gpu.client = PoolClient(tmp_path / "absent.sock")
    admitted = admit_profile(ROOT / "tests/fixtures/profiles/component-demo", run_id="shared", allow_gpu=False)
    assert admitted.admitted
    result = host.handle(ControlRequest(1, "create-cpu", 0, "CREATE_RUN", admitted.create_run_payload))
    assert result.status == "accepted", result
    until(host, lambda: bool(specs(host)) and bool(host.store.list_attempts(execution_id=specs(host)[0]["execution_id"])))
    contract = specs(host)[0]["payload"]["execution_contract"]
    assert contract["resource_grant"] == {"kind": "CPU", "gpu_uuid": None}
    assert "resource_intent" not in contract
    assert not host.resource_waits
