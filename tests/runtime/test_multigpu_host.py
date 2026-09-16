"""Host resource decisions against synthetic UUIDs; never opens a GPU device."""
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from contextlib import nullcontext
import time

import pytest

from hypoloom.agent_aci import AgentACIError, EmbeddedInput, validate_experiment_submission
from hypoloom.domain import ComponentResultKind
from hypoloom.workflow_host import WorkflowHost
from test_multigpu_resources import _resources
from test_multigpu_submission import arguments
from test_compute_resources import GPU_A, GPU_B


@pytest.mark.parametrize("count,cap", [(2, 2), (3, 3), (2, 1)])
def test_submission_plan_honors_exact_count_and_profile_cap(tmp_path, monkeypatch, count, cap):
    import hypoloom.workflow_host as module
    resources = _resources(tmp_path)
    request, args = arguments(count=count)
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(
        "node--compute-capabilities", "application/json", resources.snapshot().canonical_bytes()),))
    validated, _, _ = validate_experiment_submission(request, args)
    research = SimpleNamespace(document={"budget": {}, "methodology": {"controls": {
        "product": {"safety": {"max_gpus_per_experiment": cap}}}}},
        component=lambda component: SimpleNamespace(result_kind=ComponentResultKind.METRIC))
    host = SimpleNamespace(compute_resources=resources, shared_gpu=None, _research_contract=lambda run: research,
                           _implementation_patch=lambda *args: None)
    monkeypatch.setattr(module, "_component_plan", lambda *args: {"argv": ("python", "experiment.py")})
    monkeypatch.setattr(module, "component_is_production", lambda *args: False)
    if count > cap:
        with pytest.raises(AgentACIError, match="max_gpus_per_experiment"):
            WorkflowHost._submission_execution_plan(host, request, validated, args)
    else:
        _, grant, _, _, _ = WorkflowHost._submission_execution_plan(host, request, validated, args)
        assert len(grant.device_uuids) == count


def test_busy_resources_query_accounts_for_every_uuid_and_excludes_own_workflow():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE execution_attempts(workflow_id,run_id,execution_id,kind,state,attempt_id);"
        "CREATE TABLE research_facts(run_id,execution_id,fact_type,payload_json);"
        "CREATE TABLE workflow_transitions(run_id,workflow_id,payload_json);")
    for name, grant, state in (("plural", {"kind": "GPU", "gpu_uuids": [GPU_A.uuid, GPU_B.uuid]}, "OPEN"),
        ("single", {"kind": "GPU", "gpu_uuid": GPU_B.uuid}, "OPEN"),
        ("closed", {"kind": "GPU", "gpu_uuid": "GPU-closed"}, "CLOSED")):
        db.execute("INSERT INTO execution_attempts VALUES(?,?,?,?,?,?)", (name, "run", name, "PROCESS", state, name + "-attempt"))
        db.execute("INSERT INTO research_facts VALUES(?,?,?,?)", ("run", name, "ExperimentPreregistered",
            json.dumps({"execution_contract": {"resource_grant": grant}})))
    host = SimpleNamespace(store=SimpleNamespace(inspect=lambda query: [dict(row) for row in db.execute(query)]))
    try:
        assert WorkflowHost._busy_gpu_uuids(host) == (GPU_A.uuid, GPU_B.uuid)
        assert WorkflowHost._busy_gpu_uuids(host, "plural") == (GPU_B.uuid,)
    finally:
        db.close()


def test_pressure_on_second_uuid_cancels_once_and_records_both_devices():
    from hypoloom.compute_resources import GpuPressureTracker, GpuRuntimeObservation
    from hypoloom.domain import AttemptTerminalReason
    samples = {uuid: GpuRuntimeObservation(uuid=uuid, total_memory_mib=16000,
        free_memory_mib=8000, used_memory_mib=8000, utilization_percent=50)
        for uuid in (GPU_A.uuid, GPU_B.uuid)}
    trackers = tuple(GpuPressureTracker(initial_observation=item, reserve_memory_mib=2000,
        grace_seconds=0) for item in samples.values())
    samples[GPU_B.uuid] = replace(samples[GPU_B.uuid], free_memory_mib=1000, used_memory_mib=15000)
    requested, cancelled, closed = [], [], []
    process = SimpleNamespace(returncode=None, started_at=time.monotonic(), request=SimpleNamespace(
        timeout_seconds=30, to_dict=lambda: {"attempt_id": "multi-process"}))
    def cancel(handle):
        cancelled.append(handle)
        handle.returncode = -15
        return True
    observed = SimpleNamespace(terminal_reason=AttemptTerminalReason.CANCELLED, process_group_id=100,
        to_dict=lambda: {"terminal_reason": "CANCELLED"})
    tx = SimpleNamespace(close_attempt=lambda *args, **kwargs: closed.append(kwargs),
        append_transition=lambda transition: None)
    host = SimpleNamespace(running={"multi-process": process}, spec_validations={}, shared_gpu=None,
        gpu_pressure={"multi-process": (trackers, 2000)},
        store=SimpleNamespace(get_attempt=lambda attempt: {"run_id": "r", "workflow_id": "e"},
            transaction=lambda: nullcontext(tx)), _run_wallclock_expired=lambda run: False,
        compute_resources=SimpleNamespace(observe_runtime=lambda uuid: (requested.append(uuid), samples[uuid])[1]),
        runner=SimpleNamespace(cancel=cancel, wait=lambda handle: observed),
        _transition_record=lambda *args, **kwargs: {}, _crash=lambda point: None)
    assert WorkflowHost._poll_processes(host)
    assert requested == [GPU_A.uuid, GPU_B.uuid] and cancelled == [process]
    payload = closed[0]["payload"]
    assert payload["terminal_reason"] == "FAILED"
    assert [row["gpu_uuid"] for row in payload["resource_usage"]] == [GPU_A.uuid, GPU_B.uuid]
    assert [row["pressure_exceeded"] for row in payload["resource_usage"]] == [False, True]
    assert payload["resource_pressure"]["gpu_uuids"] == [GPU_B.uuid]
