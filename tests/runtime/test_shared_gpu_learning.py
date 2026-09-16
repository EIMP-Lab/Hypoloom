"""Workload estimates and live resource reads through the real Host submit path."""
import json

import pytest

from hypoloom.agent_aci import AgentACIError
from hypoloom.state_store import IdentityConflict
from hypoloom.shared_gpu_host import SharedGpuHost
import test_shared_gpu_mcp as fixture
from test_host_experiment_tools import _until

configured_gpu_host = fixture.configured_gpu_host
shared_pool = fixture.shared_pool
execution = fixture.execution


@pytest.fixture(autouse=True)
def current_aci(shared_pool, monkeypatch):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 18)


def gpu_arguments(arguments, peak=12000, ids=()):
    return {**arguments, "candidate": {**arguments["candidate"], "resource_preference": "GPU_REQUIRED"},
        "gpus": 1, "resource_estimate": {"workload": {"program": "generic-workload-v1"},
            "peak_memory_mib": peak, "basis": "test conservative per-device estimate", "measurement_attempt_ids": list(ids)}}


def test_submission_estimate_changes_real_intent_without_replaying_live_history(execution, monkeypatch):
    host, request, arguments = execution
    arguments = gpu_arguments(arguments)
    submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    experiment = submitted["exp_id"]
    _until(host, lambda: host.store.latest_transition(experiment)["state"] == "READY")
    spec = host.store.find_fact(request.run_id, "ExperimentPreregistered", experiment)
    assert spec["payload"]["execution_contract"]["resource_intent"]["workload_memory_mib"] == 12000
    planning = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": experiment, "max_wait_seconds": 0})["resource_planning"]
    assert planning["estimate_status"] == "submitted"
    assert planning["resource_intent"]["workload_memory_mib"] == 12000
    monkeypatch.setattr(host.shared_gpu, "resolve_arguments", lambda *a: pytest.fail("replay queried current history"))
    assert host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments) == submitted
    with pytest.raises(IdentityConflict):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", gpu_arguments(arguments, 13000))


def test_unknown_measurement_reference_creates_no_experiment(execution):
    host, request, arguments = execution
    with pytest.raises(AgentACIError, match="matching workload"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", gpu_arguments(arguments, ids=("foreign-attempt",)))
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")


def test_omitted_estimate_is_explicitly_unestimated(execution):
    host, request, arguments = execution
    submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    view = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": submitted["exp_id"], "max_wait_seconds": 0})
    assert view["resource_planning"]["estimate_status"] == "unestimated"
    assert view["resource_planning"]["resource_estimate"] is None


def test_resource_query_is_live_read_only_and_hides_other_pool_identity(execution, shared_pool):
    host, request, _ = execution
    before = tuple(host.store.inspect("SELECT * FROM workflow_commands"))
    first = host.handle_agent_tool(request.attempt_id, "get_resources", {})
    assert first["status"] == "ok" and len(first["devices"]) == 3
    uuid = first["devices"][0]["gpu_uuid"]
    shared_pool[0][uuid] = 10000
    second = host.handle_agent_tool(request.attempt_id, "get_resources", {})
    assert next(item for item in second["devices"] if item["gpu_uuid"] == uuid)["free_memory_mib"] == 10000
    assert first != second
    assert tuple(host.store.inspect("SELECT * FROM workflow_commands")) == before
    assert not {"namespace", "request_seq", "run_id", "command", "pid"} & {
        field for item in second["devices"] for field in item}
    assert json.loads(host.store.get_attempt(request.attempt_id)["observation_json"])["request"] == request.to_dict()
    with pytest.raises(AgentACIError):
        host.handle_agent_tool(request.attempt_id, "get_resources", {"run_id": "foreign"})


@pytest.mark.parametrize("status,code,pressure,expected", [
    ("FAILED", 1, False, True), ("SUCCEEDED", 0, False, False),
    ("TIMED_OUT", -15, False, False), ("FAILED", -15, True, False)])
def test_oom_requires_failed_own_workload_exception_not_pressure(tmp_path, status, code, pressure, expected):
    log = tmp_path / "stderr.log"
    log.write_text("Traceback (most recent call last):\n  own function\n"
                   "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 8.00 GiB\n")
    payload = {"terminal_reason": status, "returncode": code}
    if pressure: payload["resource_pressure"] = {"reason": "sustained_free_memory_below_reserve"}
    SharedGpuHost.observe_oom(payload, log)
    assert (payload.get("resource_failure") == "gpu_out_of_memory") is expected
    if expected: assert payload["resource_failure_excerpt"] in log.read_text()


def test_generic_error_and_quoted_oom_advice_do_not_become_measurements(tmp_path):
    log = tmp_path / "stderr.log"
    log.write_text('RuntimeError: unknown error\nAdvice: "torch.OutOfMemoryError: CUDA out of memory"\n')
    payload = {"terminal_reason": "FAILED", "returncode": 1}
    SharedGpuHost.observe_oom(payload, log)
    assert "resource_failure" not in payload
