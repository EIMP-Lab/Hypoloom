"""Real Host/tool/pool repair flow with isolated fake GPUs and no paid calls."""
import json
import sqlite3
from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentACIError
from hypoloom.gpu_pool import CapacitySample
from hypoloom.oracle import Oracle, audit_gpu_pool
from hypoloom.execution_workspace import ExecutionWorkspace
from test_host_experiment_tools import _until
from test_gpu_host_recovery import _DeterministicRunner
from test_shared_gpu_measurement_feedback import _close, _configured, _running
from hypoloom.fake_agent import FakeAgent
from test_shared_gpu_learning import gpu_arguments
from test_execution_handoff import handoff
import test_shared_gpu_mcp as fixture

configured_gpu_host = fixture.configured_gpu_host
shared_pool = fixture.shared_pool
execution = fixture.execution


@pytest.fixture(autouse=True)
def current_aci(shared_pool, monkeypatch):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 27)


def test_preview_explains_budget_without_commands_and_impossible_submit_is_rejected(execution, shared_pool):
    host, request, arguments = execution
    args = gpu_arguments(arguments, peak=60000)
    before = host.store.inspect("SELECT * FROM workflow_commands")
    preview = host.handle_agent_tool(request.attempt_id, "get_resources", {"resource_estimate": args["resource_estimate"]})
    assert preview["budget_preview"]["sharing"] == "exclusive"
    assert preview["budget_preview"]["resolved_workload_memory_mib"] == 60000
    assert preview["capacity_preview"]["reason"] == "capacity_requires_revision"
    assert host.store.inspect("SELECT * FROM workflow_commands") == before
    with pytest.raises(AgentACIError) as error:
        host.handle_agent_tool(request.attempt_id, "submit_experiment", args)
    assert error.value.code == "capacity_requires_revision"
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")


@pytest.mark.parametrize("crash_point", [None, "after_resource_revision_intent", "after_resource_revision_release", "preexec_capacity_loss"])
def test_accepted_request_closes_technically_then_replacement_executes(execution, shared_pool, monkeypatch, crash_point):
    host, request, arguments = execution
    free, observe = shared_pool
    monkeypatch.setattr(host.compute_resources, "_observation_provider", observe)
    monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", gpu_arguments(arguments, peak=20000))
    exp = first["exp_id"]
    _until(host, lambda: host.store.latest_transition(exp)["state"] == "READY")
    original_request = request
    host._finish_fake_agent(request, handoff(request, exp))
    # Admission happened with larger application capacity. A later reliable
    # driver-reserve observation establishes the capacity shortfall.
    pool_root = host.shared_gpu.socket_path.parent / "pool"
    from hypoloom.gpu_pool import GpuPool
    import os
    reduced = {"value": crash_point != "preexec_capacity_loss"}
    pool = GpuPool(pool_root, members={os.getuid(): list(free)}, reserve_memory_mib=1000,
        sampler=lambda: CapacitySample(tuple(replace(observe(uuid),
            total_memory_mib=21000, free_memory_mib=19500, used_memory_mib=1500,
            system_reserved_memory_mib=1500) if reduced["value"] else observe(uuid) for uuid in free)))
    # Use the real pool transaction through a client adapter; no research pool.
    monkeypatch.setattr(host.shared_gpu.client, "request", lambda op, args: pool.handle(os.getuid(), op, args))
    host.runner = _DeterministicRunner(complete=False)
    if crash_point == "preexec_capacity_loss":
        monkeypatch.setattr(host, "_crash", lambda point: reduced.update(value=True)
                            if point == "after_process_intent_before_spawn" else None)
    elif crash_point:
        class Crash(BaseException): pass
        def crash(point):
            if point == crash_point: raise Crash(point)
        with monkeypatch.context() as patch:
            patch.setattr(host, "_crash", crash)
            with pytest.raises(Crash, match=crash_point):
                _until(host, lambda: False)
        from hypoloom.workflow_host import WorkflowHost
        root, resources, config = host.state_root, host.compute_resources, host.shared_gpu.configuration
        host.close()
        host = WorkflowHost(root, compute_resources=resources,
            gpu_pool_socket=config["socket_path"], gpu_pool_namespace=config["namespace"])
        host.initialize()
        host.runner = _DeterministicRunner(complete=False)
        monkeypatch.setattr(host.shared_gpu.client, "request", lambda op, args: pool.handle(os.getuid(), op, args))
    _until(host, lambda: bool(host.local_execution_agents))
    request = next(iter(host.local_execution_agents.values()))
    host.local_execution_agents.clear()
    assert request.command_id == original_request.command_id
    assert request.attempt_id != original_request.attempt_id
    _until(host, lambda: host.store.latest_transition(exp)["state"] == "TERMINAL"
           and bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    view = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": exp, "max_wait_seconds": 0})
    assert view["outcome"]["outcome"] == "EXECUTION_FAILURE"
    assert view["resource_failure"] == "capacity_requires_revision" and view["repairable"]
    attempts = host.store.list_attempts(workflow_id=exp)
    processes = [a for a in attempts if a["kind"] == "PROCESS"]
    assert len(processes) == (2 if crash_point == "preexec_capacity_loss" else 1)
    assert processes[-1]["process_group_id"] is None
    assert json.loads(processes[-1]["observation_json"])["synthetic_before_spawn"]
    second = host.handle_agent_tool(request.attempt_id, "submit_experiment",
        {**gpu_arguments(arguments, peak=10000), "replaces_experiment_id": exp})
    _until(host, lambda: any(host.store.get_attempt(a)["workflow_id"] == second["exp_id"] for a in host.running))
    handle = next(h for a, h in host.running.items() if host.store.get_attempt(a)["workflow_id"] == second["exp_id"])
    _close(host, second, handle, success=True, arguments=arguments)
    result = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": second["exp_id"]})
    assert result["outcome"]["outcome"] == "SUCCESS", result
    host.shared_gpu.reconcile()
    with sqlite3.connect(pool.database_path) as c:
        assert c.execute("SELECT count(*) FROM requests WHERE state != 'RELEASED'").fetchone()[0] == 0
    assert audit_gpu_pool(pool.root)["ok"]
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()
    from hypoloom.observation_api import product_events
    events = product_events(request.run_id, host.store.list_facts(request.run_id),
        host.store.inspect("SELECT * FROM workflow_transitions WHERE run_id=?", (request.run_id,)),
        host.store.list_attempts())
    failures = [e for e in events if e["payload"].get("error_code") == "capacity_requires_revision"]
    assert len(failures) == 1 and failures[0]["type"] == "experiment_failed"
    assert len([a for a in host.store.list_attempts() if a["kind"] == "PROCESS"]) == len(processes) + 1
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    final = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert final.ok and final.open_attempt_count == 0 and final.pending_effect_count == 0, final.to_dict()
    assert not host.running and not final.owned_process_residue
    host.close()


def test_successful_owned_measurement_lowers_margin_in_actual_admission_and_oracle(execution, shared_pool, monkeypatch):
    host, request, arguments, free, measurement = _configured(execution, shared_pool, monkeypatch)
    measurement["mib"] = 16198
    arguments = gpu_arguments(arguments, peak=16384)
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    spec, handle = _running(host, first)
    assert spec["payload"]["execution_contract"]["resource_intent"]["sharing"] == "exclusive"
    free.update({uuid: 5000 for uuid in free})
    host.tick()
    process, observation = _close(host, first, handle, success=True, arguments=arguments, malformed_result=True)
    assert observation["resource_usage"]["owned_memory"]["unknown_sample_count"] == 0
    _until(host, lambda: bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    free.update({uuid: 22000 for uuid in free})
    preview = host.handle_agent_tool(request.attempt_id, "get_resources", {"resource_estimate": arguments["resource_estimate"]})
    assert preview["budget_preview"]["headroom_percent"] == 15
    assert preview["budget_preview"]["resolved_workload_memory_mib"] == 18688
    assert preview["budget_preview"]["measurement_attempt_ids"] == [process["attempt_id"]]
    second = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "replaces_experiment_id": first["exp_id"]})
    spec, handle = _running(host, second)
    intent = spec["payload"]["execution_contract"]["resource_intent"]
    assert intent["sharing"] == "shared" and intent["workload_memory_mib"] == 18688
    _close(host, second, handle, success=True, arguments=arguments)
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok and report.open_attempt_count == 0, report.to_dict()
