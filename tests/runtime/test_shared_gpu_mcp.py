"""Submitted GPU intent is source-stable while allocation uses live pool capacity."""
import json
import os
from functools import partial

import pytest

from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom.domain import ResourceGrant, ResourceKind
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.oracle import Oracle
import test_host_experiment_tools as host_tools
import test_multigpu_mcp_host as multigpu
from test_host_experiment_tools import _until
from test_multigpu_resources import GPU_A, GPU_B, GPU_C
from test_gpu_host_recovery import _DeterministicRunner

execution = host_tools.execution
configured_gpu_host = multigpu.configured_gpu_host


@pytest.fixture(autouse=True)
def shared_pool(configured_gpu_host, tmp_path, monkeypatch):
    import test_host_experiment_tools as fixture
    values = {gpu.uuid: gpu for gpu in (GPU_A, GPU_B, GPU_C)}
    free = {uuid: 22000 for uuid in values}
    def observe(uuid):
        total = values[uuid].total_memory_mib
        return GpuRuntimeObservation(uuid=uuid, total_memory_mib=total,
            free_memory_mib=free[uuid], used_memory_mib=total-free[uuid], utilization_percent=0)
    pool = GpuPool(tmp_path / "pool", members={os.getuid(): list(values)},
        reserve_memory_mib=1000, sampler=lambda: CapacitySample(devices=tuple(observe(uuid) for uuid in values)))
    server = PoolServer(pool, tmp_path / "pool.sock")
    server.start()
    original = fixture._pilot_payload
    def payload(*args):
        result = original(*args)
        result["research_contract"]["budget"].update(min_vram_per_experiment=8000, gpu_vram_reserve_mb=1000)
        return result
    monkeypatch.setattr(fixture, "_pilot_payload", payload)
    monkeypatch.setattr(fixture, "WorkflowHost", partial(fixture.WorkflowHost,
        gpu_pool_socket=tmp_path / "pool.sock", gpu_pool_namespace="mcp"))
    try:
        yield free, observe
    finally:
        server.close()


@pytest.mark.parametrize("prestart_failure", [False, True])
def test_submitted_plural_intent_replays_then_allocates_current_pair(execution, shared_pool, monkeypatch, prestart_failure):
    host, request, arguments = execution
    free, observe = shared_pool
    monkeypatch.setattr(host.compute_resources, "_observation_provider", observe)
    if not prestart_failure:
        monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    arguments = {**arguments, "candidate": {**arguments["candidate"], "resource_preference": "GPU_REQUIRED"}, "gpus": 2}
    submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    experiment = submitted["exp_id"]
    _until(host, lambda: host.store.latest_transition(experiment)["state"] == "READY")
    frozen = host.store.find_fact(request.run_id, "ExperimentPreregistered", experiment)
    assert frozen["payload"]["execution_contract"]["resource_intent"]["gpu_count"] == 2
    assert "resource_grant" not in frozen["payload"]["execution_contract"]
    free[GPU_A.uuid] = 1000
    assert host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments) == submitted
    host.runner = _DeterministicRunner(complete=True)
    _until(host, lambda: host.store.latest_transition(experiment)["state"] == "TERMINAL")
    process = host.store.list_attempts(execution_id=frozen["execution_id"])[0]
    reconstructed = host._execution_inputs(request.run_id, experiment, attempt_id=process["attempt_id"])
    assert reconstructed[-1] == ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=(GPU_B.uuid, GPU_C.uuid))
    assert host.store.find_fact(request.run_id, "ExperimentPreregistered", experiment) == frozen
    if prestart_failure:
        assert json.loads(process["observation_json"])["synthetic_before_spawn"]
        assert process["process_group_id"] is None
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, json.dumps(report.to_dict(), indent=2)
