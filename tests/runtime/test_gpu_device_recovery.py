"""Real Host transactions, collection and recheck with an injected owned Xid."""
import json
import time
from dataclasses import replace

import pytest

from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AttemptTerminalReason
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle, audit_gpu_pool
from hypoloom.runner import process_group_exists
from test_gpu_host_recovery import _DeterministicRunner
from test_host_experiment_tools import _until
from test_shared_gpu_learning import gpu_arguments
import test_shared_gpu_mcp as fixture

configured_gpu_host = fixture.configured_gpu_host
shared_pool = fixture.shared_pool
execution = fixture.execution


@pytest.fixture(autouse=True)
def current_aci(shared_pool, monkeypatch):
    import test_host_experiment_tools as host_fixture
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 30)
    original = host_fixture._pilot_payload
    def payload(*args):
        result = original(*args)
        contract = result["research_contract"]
        contract["budget"]["max_infrastructure_retries"] = 1
        contract["methodology"]["controls"]["product"]["methodology"]["inner_loop"]["repair_attempts"] = 0
        return result
    monkeypatch.setattr(host_fixture, "_pilot_payload", payload)


class FailedRunner(_DeterministicRunner):
    def wait(self, handle):
        observed = super().wait(replace(handle, returncode=0))
        return replace(observed, terminal_reason=AttemptTerminalReason.FAILED, returncode=1)


def test_owned_xid_closes_collects_blocks_then_rechecks_with_separate_budget(execution, shared_pool, monkeypatch):
    from hypoloom import gpu_diagnostics
    host, request, arguments = execution
    monkeypatch.setattr(host.compute_resources, "_observation_provider", shared_pool[1])
    monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    host.runner = FailedRunner(complete=False)
    arguments = gpu_arguments(arguments, peak=8000)
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)["exp_id"]
    _until(host, lambda: bool(host.running))
    identity, handle = next(iter(host.running.items()))
    gpu = host.gpu_pressure[identity][0][0].summary.gpu_uuid
    timestamp = time.monotonic()
    sample = {"gpu_uuid": gpu, "root_process_group_id": handle.process_group_id,
        "root_start_time_ticks": 1, "complete": True, "reason": None,
        "observed_at": time.time(), "duration_seconds": 0.0, "resident_bytes": 1024**2,
        "processes": [{"pid": handle.pid, "resident_bytes": 1024**2,
            "ancestry": [{"pid": handle.pid, "ppid": 1, "start_time_ticks": 1}]}]}
    monkeypatch.setattr(host.shared_gpu, "finish_owned", lambda _: {gpu: {
        "sample_count": 1, "unknown_sample_count": 0, "latest": sample, "peak": sample}})
    monkeypatch.setattr(gpu_diagnostics, "gpu_pci_identities", lambda: {"0000:3b:00.0": gpu})
    monkeypatch.setattr(gpu_diagnostics, "read_kernel_xids", lambda: {
        "status": "complete", "reason": None, "first_timestamp": 0.0,
        "events": [{"pci_bus": "0000:3b:00.0", "xid": 38, "pid": handle.pid,
            "timestamp_monotonic": timestamp - 0.001}]})
    handle.returncode = 1
    assert host._poll_processes()
    process = host.store.get_attempt(identity)
    observation = json.loads(process["observation_json"])
    assert process["terminal_reason"] == "FAILED" and gpu_diagnostics.confirmed_gpu_fault(observation)
    block = host.resource_recovery.for_request(request)
    assert block["code"] == "gpu_device_fault" and block["process_attempt_id"] == identity
    _until(host, lambda: host.store.latest_transition(first)["state"] == "TERMINAL"
        and bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    view = host.handle_agent_tool(request.attempt_id, "list_my_experiments", {})
    assert view["execution_budget"]["repairs_remaining"] == 0
    assert view["execution_budget"]["device_recoveries_remaining"] == 1
    assert view["execution_budget"]["resource_recheck_required"]
    assert view["experiments"][0]["failure_category"] == "gpu_device_fault"
    assert host.handle(ControlRequest(1, "early", None, "RESOURCE_RECHECK",
        {"run_id": request.run_id, "reason": "operator"})).status != "accepted"
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    count = len(host._expert_attempts(request.task_context))
    for _ in range(3):
        host.tick()
    assert len(host._expert_attempts(request.task_context)) == count
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()
    response = host.handle(ControlRequest(1, "recheck", None, "RESOURCE_RECHECK",
        {"run_id": request.run_id, "reason": "operator"}))
    assert response.status == "accepted", response
    _until(host, lambda: bool(host.local_execution_agents))
    retry = next(iter(host.local_execution_agents.values()))
    host.local_execution_agents.clear()
    second = host.handle_agent_tool(retry.attempt_id, "submit_experiment",
        {**arguments, "replaces_experiment_id": first})["exp_id"]
    assert second != first
    budget = host.handle_agent_tool(retry.attempt_id, "list_my_experiments", {})["execution_budget"]
    assert budget["repairs_used"] == 0 and budget["device_recoveries_used"] == 1
    host.local_execution_agents[retry.attempt_id] = retry
    cancelled = host.handle(ControlRequest(1, "cleanup", None, "CANCEL",
        {"run_id": request.run_id, "cause": "isolated regression cleanup"}))
    assert cancelled.status == "accepted"
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "CANCELLED")
    attempts = host.store.list_attempts(run_id=request.run_id)
    assert all(row["state"] == "CLOSED" for row in attempts)
    assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()
    assert audit_gpu_pool(host.shared_gpu.socket_path.parent / "pool")["ok"]
