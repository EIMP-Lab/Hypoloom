"""Pool failures are Host-owned operational state, not fabricated science."""
import json

import pytest

from hypoloom.agent_aci import AgentACIError
from hypoloom.control_protocol import ControlRequest, SCHEMA_VERSION
from hypoloom.fake_agent import FakeAgent
from hypoloom.gpu_pool import PoolError
from hypoloom.oracle import Oracle
from test_shared_gpu_learning import gpu_arguments
import test_shared_gpu_mcp as fixture

configured_gpu_host = fixture.configured_gpu_host
shared_pool = fixture.shared_pool
execution = fixture.execution


@pytest.fixture(autouse=True)
def current_aci(shared_pool, monkeypatch, request):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 28)
    if request.node.name == "test_rechecked_node_reaches_real_cpu_collection_and_next_action":
        import sys
        from pathlib import Path
        import test_host_experiment_tools as host_fixture
        host_fixture.WorkflowHost.keywords["compute_resources"].gpu_runtime_prefix = Path(sys.prefix)
        # Select a real CPU implementation as well as a CPU grant. Synthetic
        # GPU inventory must not make FakeAgent author a CUDA-only script.
        monkeypatch.setattr("hypoloom.fake_agent._has_gpu_capability", lambda _: False)



def recheck(host, request, key="recheck"):
    return host.handle(ControlRequest(SCHEMA_VERSION, key, None, "RESOURCE_RECHECK",
                                     {"run_id": request.run_id, "reason": "operator"}))


@pytest.mark.parametrize("tool", ["submit_experiment", "get_resources"])
@pytest.mark.parametrize("code", ["incompatible_pool", "unavailable", "unauthorized_server", "invalid_response"])
def test_pool_failure_closes_without_cpu_bypass_and_requires_fresh_recheck(execution, monkeypatch, tool, code):
    host, request, arguments = execution
    original = host.shared_gpu.client.request
    def failed(*args): raise PoolError(code, "synthetic pool outage")
    monkeypatch.setattr(host.shared_gpu.client, "request", failed)
    try:
        result = host.handle_agent_tool(request.attempt_id, tool, gpu_arguments(arguments, peak=8000) if tool == "submit_experiment" else {})
        assert result["status"] == "unavailable"
    except AgentACIError as exc:
        assert exc.code == code
    blocked = host.resource_recovery.for_request(request)
    assert blocked["attempt_id"] == request.attempt_id and blocked["code"] == code
    assert host.handle_agent_tool(request.attempt_id, "list_my_experiments", {})["resource_blocked"] == blocked
    with pytest.raises(AgentACIError, match="Do not downgrade to CPU"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")
    assert recheck(host, request, "still-running").status != "accepted"
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    attempt = host.store.get_attempt(request.attempt_id)
    assert attempt["state"] == "CLOSED" and attempt["terminal_reason"] == "FAILED"
    observed = json.loads(attempt["observation_json"])
    assert observed["failure_code"] == "resource_infrastructure_blocked"
    assert observed["retry_class"] == "INFRASTRUCTURE"
    assert not host._expert_task_exhausted(request.run_id, request.task_context)
    count = len(host._expert_attempts(request.task_context))
    host._expert_agent_step(request.run_id, request.owner_workflow_id, request.task_context,
                            request.role, request.component_id, request.method_family_id)
    assert len(host._expert_attempts(request.task_context)) == count
    assert recheck(host, request, "still-broken").status != "accepted"
    monkeypatch.setattr(host.shared_gpu.client, "request", original)
    response = recheck(host, request)
    assert response.status == "accepted", response
    assert recheck(host, request) == response  # One durable operator action.
    assert not host.resource_recovery.blocked(request.run_id)
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_native_stop_uses_host_block_and_never_accepts_scientific_completion(execution, monkeypatch, tmp_path):
    import time
    from hypoloom.host_tools_server import AgentToolServer
    from hypoloom.native_output import NativeOutputValidator
    from test_execution_completion import _validate
    from test_native_output import SCHEMA
    host, request, _ = execution
    tools = AgentToolServer(tmp_path / "tools.sock", request.attempt_id, host.handle_agent_tool)
    output = NativeOutputValidator(tmp_path / "out.sock", output_schema=SCHEMA,
        session_id="execution-session", max_failed_validations=4, max_request_bytes=4096,
        deadline_at=time.monotonic() + 30, execution_tool_socket=tools.socket_path)
    try:
        assert _validate(output, tools)["decision"] == "block"
        monkeypatch.setattr(host.shared_gpu.client, "request", lambda *_: (_ for _ in ()).throw(PoolError("incompatible_pool", "old service")))
        host.handle_agent_tool(request.attempt_id, "get_resources", {})
        assert _validate(output, tools) == {}
        host._finish_fake_agent(request, FakeAgent().invoke(request))
        assert host.store.get_attempt(request.attempt_id)["terminal_reason"] == "FAILED"
        assert not host._expert_success(request.task_context)
        assert not host.store.list_facts(request.run_id, "CanonicalOutcomeAccepted")
    finally:
        output.close(); tools.close()


def test_pre_submit_block_survives_restart_then_recheck_retries_same_node(execution, monkeypatch):
    from hypoloom.workflow_host import WorkflowHost
    host, request, _ = execution
    original = host.shared_gpu.client.request
    monkeypatch.setattr(host.shared_gpu.client, "request", lambda *_: (_ for _ in ()).throw(PoolError("unavailable", "lost socket")))
    host.handle_agent_tool(request.attempt_id, "get_resources", {})
    config, root, compute = host.shared_gpu.configuration, host.state_root, host.compute_resources
    monkeypatch.setattr(host.shared_gpu.client, "request", original)
    host.close()
    host = WorkflowHost(root, compute_resources=compute, gpu_pool_socket=config["socket_path"], gpu_pool_namespace=config["namespace"])
    host.initialize()
    try:
        assert host.store.get_attempt(request.attempt_id)["terminal_reason"] == "LOST"
        assert host.resource_recovery.for_request(request)
        assert recheck(host, request).status == "accepted"
        host._expert_agent_step(request.run_id, request.owner_workflow_id, request.task_context,
                                request.role, request.component_id, request.method_family_id)
        attempts = host._expert_attempts(request.task_context)
        assert len(attempts) == 2 and attempts[-1]["command_id"] == request.command_id
        report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
        assert report.ok, report.to_dict()
    finally:
        from hypoloom.control_protocol import ControlRequest
        response = host.handle(ControlRequest(SCHEMA_VERSION, "cleanup", None, "CANCEL", {"run_id": request.run_id, "cause": "test cleanup"}))
        assert response.status == "accepted", response
        for _ in range(30):
            host.tick()
            if host.store.latest_transition(request.run_id)["state"] in {"CANCELLED", "FAILED"}: break
        host.close()


def test_resource_oracle_rejects_forged_block_source(execution, monkeypatch):
    import sqlite3
    host, request, _ = execution
    monkeypatch.setattr(host.shared_gpu.client, "request", lambda *_: (_ for _ in ()).throw(PoolError("incompatible_pool", "old")))
    host.handle_agent_tool(request.attempt_id, "get_resources", {})
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    # Explicit test-only corruption of an isolated database; no production write path.
    with sqlite3.connect(host.store.database_path) as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='workflow_transitions'").fetchall():
            db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        db.execute("UPDATE workflow_transitions SET payload_json=json_set(payload_json,'$.resource_blocked.attempt_id','foreign') WHERE json_type(payload_json,'$.resource_blocked')='object'")
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert any(item.code == "resource_recovery_binding" for item in report.issues)


def test_rechecked_node_reaches_real_cpu_collection_and_next_action(execution, monkeypatch):
    from test_host_experiment_tools import _until
    from hypoloom.runner import process_group_exists
    from hypoloom.monitor import build_monitor_snapshot
    host, request, arguments = execution
    original = host.shared_gpu.client.request
    monkeypatch.setattr(host.shared_gpu.client, "request", lambda *_: (_ for _ in ()).throw(PoolError("unavailable", "socket unavailable")))
    host.handle_agent_tool(request.attempt_id, "get_resources", {})
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    offline = build_monitor_snapshot(host.state_root, request.run_id)
    assert offline.status["resource_blocked"][0]["code"] == "unavailable"
    monkeypatch.setattr(host.shared_gpu.client, "request", original)
    assert recheck(host, request).status == "accepted"
    _until(host, lambda: bool(host.local_execution_agents))
    retry = next(iter(host.local_execution_agents.values()))
    host.local_execution_agents.clear()
    # A reviewed CPU test after recovery; never allocate the synthetic devices.
    submitted = host.handle_agent_tool(retry.attempt_id, "submit_experiment", {**arguments, "gpus": 0})
    _until(host, lambda: bool(host.running))
    response = host.handle(ControlRequest(SCHEMA_VERSION, "status", None, "STATUS", {"run_id": request.run_id}))
    activity = response.result["process_activity"]
    assert activity and activity[0]["resource_grant"]["kind"] == "CPU"
    assert activity[0]["process_alive"] in (True, False) and activity[0]["progress"] == "unknown"
    _until(host, lambda: host.store.latest_transition(submitted["exp_id"])["state"] == "TERMINAL")
    # Closed records also feed frozen CYCLE_REPORT inputs. Observation clocks
    # must not turn an unchanged completed experiment into a different task.
    completed = host._experiment_tool_view(request.run_id, submitted["exp_id"])
    import hypoloom.process_observation as progress
    with monkeypatch.context() as clock:
        now = progress.time.time()
        clock.setattr(progress.time, "time", lambda: now + 60)
        assert host._experiment_tool_view(request.run_id, submitted["exp_id"]) == completed
    host._finish_fake_agent(retry, FakeAgent().invoke(retry))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    attempts = host.store.list_attempts(run_id=request.run_id)
    assert all(row["state"] == "CLOSED" for row in attempts)
    assert len(host._expert_attempts(request.task_context)) == 2
    processes = [row for row in attempts if row["kind"] == "PROCESS"]
    assert processes and all(not json.loads(row["observation_json"]).get("synthetic_before_spawn") for row in processes)
    assert all(row["terminal_reason"] == "SUCCEEDED" for row in processes)
    assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
    outcomes = host.store.list_facts(request.run_id, "CanonicalOutcomeAccepted")
    assert len(outcomes) == 1 and outcomes[0]["payload"]["outcome"] == "SUCCESS"
    assert host.store.list_facts(request.run_id, "HypothesisAssessed")
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_old_pool_blocks_create_before_any_run_fact_or_agent(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path
    import test_host_experiment_tools as host_fixture
    with tempfile.TemporaryDirectory(prefix="resource-preflight-", dir="/var/tmp") as mount:
        host = host_fixture.WorkflowHost(tmp_path / "new-state")
        host.initialize()
        try:
            payload = host_fixture._pilot_payload(tmp_path, Path(mount), "not-started")
            snapshot = host.shared_gpu.client.request("snapshot", {})
            monkeypatch.setattr(host.shared_gpu.client, "request", lambda *_: {**snapshot, "capabilities": []})
            response = host.handle(ControlRequest(1, "blocked-create", 0, "CREATE_RUN", payload))
            assert response.status == "rejected" and "incompatible_pool" in response.error["message"]
            assert host.store.get_run("not-started") is None
            assert not host.store.list_attempts(run_id="not-started")
            assert not host.store.list_facts("not-started")
        finally: host.close()


def test_resource_handoff_keeps_process_cleanup_failure_precedence(execution, monkeypatch):
    from types import SimpleNamespace
    from hypoloom.claude_agent_host import AgentHostTerminalReason
    host, request, _ = execution
    monkeypatch.setattr(host.shared_gpu.client, "request", lambda *_: (_ for _ in ()).throw(PoolError("incompatible_pool", "old")))
    host.handle_agent_tool(request.attempt_id, "get_resources", {})
    observation = SimpleNamespace(returncode=1, pid=999999, process_group_id=999999,
        failure_code="resource_cleanup_failed", diagnostics={"resource_cleanup_error": "injected cleanup failure"},
        response=None, terminal_reason=AgentHostTerminalReason.FAILED)
    host.claude_agent_host = SimpleNamespace(cancel=lambda _: None, poll=lambda _: observation, cleanup=lambda _: None)
    host.running_agents[request.attempt_id] = SimpleNamespace(request=request, tool_workspace=None)
    assert host._poll_agents()
    result = json.loads(host.store.get_attempt(request.attempt_id)["observation_json"])
    assert result["failure_code"] == "resource_cleanup_failed" and result["retry_class"] is None
    assert host.resource_recovery.for_request(request)
