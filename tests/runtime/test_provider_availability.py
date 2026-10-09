"""Shared availability, operator connection selection and real Host recovery."""
import http.client
import json
import os
import socket
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from hypoloom.control_protocol import ControlRequest, ControlResponse
from hypoloom.domain import AgentCommand, AttemptTerminalReason
from hypoloom.provider_observation import ProviderObservation, provider_failure, provider_retry_gate
from hypoloom.workflow_host import WorkflowHost
from test_expert_batch_host import batch_host, _complete
from test_host_experiment_tools import _until


def failure(identity, start, end, code="rate_limited", retry_at=None):
    return {"attempt_id": identity, "started_at": start, "ended_at": end, "state": "CLOSED",
        "code": code, "retry_at": end + 60 if retry_at is None else retry_at, "http_status": 429}


def gate(rows, now, clear=None):
    return provider_retry_gate(rows, cooldown_seconds=18000, cleared_at=clear, now=now)


def test_transient_recovery_slows_down_and_late_old_calls_cannot_reblock():
    rows = [failure("a", 0, 10), failure("b", 1, 11), failure("c", 2, 12)]
    assert gate(rows, 20)["recovery_attempts"] == 1
    assert gate(rows, 73)["state"] == "recovering"
    rows += [{"attempt_id": "retry", "started_at": 74, "state": "OPEN"}]
    assert gate(rows, 75)["pending"]
    rows[-1] = failure("retry", 74, 80)
    rows.append(failure("retry2", 141, 150))
    waiting = gate(rows, 1000)
    assert waiting["state"] == "recovering" and not waiting['requires_operator']
    assert gate(rows, 200)['next_retry_at'] == 390
    rows.append(failure("late", 149, 201))
    assert gate(rows, 202, clear=200)["state"] == "recovering"
    assert not gate(rows, 202, clear=200)["pending"]
    rows.append({"attempt_id": "ok", "started_at": 203, "ended_at": 205, "state": "CLOSED", "provider_success": True})
    assert gate(rows, 206, clear=200)["state"] == "ready"


def test_recovery_ignores_preexisting_work_and_its_late_success():
    rows = [failure("reader", 1, 10), {"attempt_id": "parent", "started_at": 0, "state": "OPEN"}]
    assert not gate(rows, 71)["pending"]  # Parent need not finish before a recovery invocation.
    rows[1].update(state="CLOSED", ended_at=72, provider_success=True)
    assert gate(rows, 73)["state"] == "recovering"  # Old success cannot confirm restored access.
    rows.append({"attempt_id": "probe", "started_at": 74, "state": "OPEN"})
    assert gate(rows, 75)["pending"]
    rows.append(failure("older-overlap", 0, 75))
    assert gate(rows, 136)["pending"]  # An old late failure cannot hide the already-open recovery invocation.
    rows[-2].update(state="CLOSED", ended_at=137, provider_success=True)
    assert gate(rows, 138)["state"] == "ready"


def test_quota_classification_does_not_guess_from_429_and_preserves_retry_after():
    parsed = ProviderObservation()
    parsed.feed(b'{"type":"error","error":{"type":"insufficient_quota","message":"private error"}}')
    observed = parsed.finish()
    quota = provider_failure([{**observed, "upstream_status": 429, "ended_at": 10}])
    assert quota == {"code": "quota_exhausted", "http_status": 429}
    assert gate([{**quota, "attempt_id": "quota", "started_at": 1, "ended_at": 10}], 999999)["requires_operator"]
    assert provider_failure([{"upstream_status": 429, "ended_at": 10}])["code"] == "rate_limited"
    known = provider_failure([{**observed, "upstream_status": 429, "ended_at": 10, "response_headers": {"retry-after": "3600"}}])
    assert known["retry_at"] == 3610
    assert gate([{**known, "attempt_id": "quota", "started_at": 1, "ended_at": 10}], 20)["state"] == "cooldown"


def test_local_admission_timeout_never_claims_remote_rate_limit():
    local = provider_failure([{'failure_reason': 'local_request_queue', 'upstream_status': None, 'ended_at': 10}])
    assert local == {'code': 'local_request_queue', 'http_status': None, 'source': 'local_admission'}
    assert gate([{**local, 'attempt_id': 'queued', 'started_at': 1, 'ended_at': 10}], 11)['state'] == 'ready'


def test_local_queue_retries_do_not_exhaust_nodes_or_pause_other_work(batch_host, monkeypatch):
    host = batch_host
    original, deferred = host._accept_agent_response, []
    def respond(request, response, **kwargs):
        if len(deferred) < 6:
            deferred.append(request.command_id)
            host._agent_failure(request, AttemptTerminalReason.FAILED, 'nonzero_exit', retryable=True,
                details={'diagnostics': {'provider_failure': {'code':'local_request_queue', 'source':'local_admission', 'http_status':None}}})
            assert host._provider_retry_status('batch-host')['state'] == 'ready'
        else:
            original(request, response, **kwargs)
    monkeypatch.setattr(host, '_accept_agent_response', respond)
    _complete(host)
    assert len(deferred) == 6 and not host._expert_final_failures('batch-host')
    assert len([r for r in host.store.list_attempts() if r['kind'] == 'PROCESS']) == 2


def test_zero_auth_delay_still_bounds_recovery_and_quality_failure_can_prove_access():
    rows = [failure(str(i), i * 10, i * 10 + 1, code="auth_failed") for i in range(3)]
    status = provider_retry_gate(rows, cooldown_seconds=0, cleared_at=None, now=100)
    assert status["pending"] and status["requires_operator"]
    # A real API response can be malformed scientifically yet prove connectivity.
    from hypoloom.observation_api import product_events
    attempts = [{"attempt_id": "rejected", "started_at": 0, "ended_at": 1, "state": "CLOSED", "kind": "AGENT",
        "terminal_reason": "FAILED", "observation_json": json.dumps({"diagnostics": {"provider_failure": {"code": "rate_limited", "retry_at": 61}}})},
        {"attempt_id": "invalid-candidate", "started_at": 62, "ended_at": 65, "state": "CLOSED", "kind": "AGENT",
        "terminal_reason": "FAILED", "observation_json": json.dumps({"failure_code": "candidate_schema_invalid",
            "diagnostics": {"provider_observed_usage": {"completed_requests": 1}}})}]
    facts = [{"fact_id": "goal", "fact_type": "GoalDefined", "created_at": 0, "payload": {"research_contract": {"budget": {}}}}]
    events = product_events("test", facts, [], attempts)
    assert [e["type"] for e in events if e["type"].startswith("provider_")] == ["provider_waiting", "provider_recovered"]


def test_api_wait_does_not_prevent_bounded_stop(batch_host, monkeypatch):
    host = batch_host
    host._accept_agent_response = lambda request, response, **kw: host._agent_failure(request,
        AttemptTerminalReason.FAILED, "nonzero_exit", retryable=True,
        details={"diagnostics": {"provider_failure": {"code": "quota_exhausted", "http_status": 429}}})
    _until(host, lambda: host._provider_retry_pending("batch-host"))
    response = host.handle(ControlRequest(1, "stop", None, "CANCEL", {"run_id": "batch-host", "cause": "operator"}))
    assert response.status == "accepted"
    _until(host, lambda: host.store.latest_transition("batch-host")["state"] in {"FAILED", "CANCELLED"})
    assert not host.store.list_attempts(state="OPEN")
    reports = [r for r in host.store.list_attempts() if json.loads(r["observation_json"]).get("request", {}).get("command") == "FINAL_REPORT"]
    assert 0 < len(reports) <= 3
    assert host.store.inspect("SELECT 1 FROM workflow_commands WHERE command_type='FINAL_REPORT_FAILED'")


def test_submitted_process_is_collected_during_api_wait_and_not_repeated(batch_host):
    host = batch_host
    _until(host, lambda: bool(host.running) and bool(host.local_execution_agents))
    original_processes = {r["attempt_id"] for r in host.store.list_attempts() if r["kind"] == "PROCESS"}
    # Simulate the execution controller's provider dying while its already
    # submitted Process remains owned by the real Host/runner.
    identity, request = next(iter(host.local_execution_agents.items()))
    host.local_execution_agents.pop(identity)
    host._agent_failure(request, AttemptTerminalReason.FAILED, "nonzero_exit", retryable=True,
        details={"diagnostics": {"provider_failure": {"code": "quota_exhausted", "http_status": 429}}})
    assert host._provider_retry_pending("batch-host")
    _until(host, lambda: all(host.store.get_attempt(identity)["state"] == "CLOSED" for identity in original_processes))
    assert host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
    assert host._provider_retry_pending("batch-host")
    response = host.handle(ControlRequest(1, "after-collection", None, "PROVIDER_RETRY_CLEAR", {"run_id": "batch-host", "reason": "operator"}))
    assert response.status == "accepted"
    _complete(host)
    processes = [r for r in host.store.list_attempts() if r["kind"] == "PROCESS"]
    assert len(processes) == 2 and original_processes <= {r["attempt_id"] for r in processes}
    from hypoloom.oracle import Oracle
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("code,status", [("rate_limited", 429), ("usage_window_exhausted", 403)])
def test_host_waits_without_exhausting_node_then_continues_real_processes(batch_host, monkeypatch, code, status):
    host = batch_host
    original = host._accept_agent_response
    offset = [0]
    import hypoloom.provider_observation as observation
    original_gate = observation.provider_retry_gate
    monkeypatch.setattr(observation, "provider_retry_gate", lambda rows, **kw: original_gate(rows, **{**kw, "now": kw["now"] + offset[0]}))
    rejected = []
    def reject(request, response, **kwargs):
        rejected.append(request)
        host._agent_failure(request, AttemptTerminalReason.FAILED, "nonzero_exit", retryable=True,
            details={"diagnostics": {"provider_failure": {"code": code, "http_status": status, "retry_at": time.time() + 60}}})
    monkeypatch.setattr(host, "_accept_agent_response", reject)
    for count in range(1, 7):
        _until(host, lambda: len(rejected) == count)
        if count < 6:
            offset[0] = min(900, 60 * 2 ** min(count - 1, 4)) + 1
    assert len({r.command_id for r in rejected}) == 1
    offset[0] = 0
    assert host._provider_retry_status("batch-host")["state"] == "cooldown"
    before = len(host.store.list_attempts())
    for _ in range(4): host.tick()
    assert len(host.store.list_attempts()) == before
    assert not host._expert_final_failures("batch-host")
    reopened = WorkflowHost(host.state_root, local_agent=host.agent)
    assert not reopened._provider_retry_status("batch-host")["requires_operator"]
    assert reopened._provider_retry_status('batch-host')['next_retry_at'] is not None
    reopened.store.close()
    monkeypatch.setattr(host, "_accept_agent_response", original)
    offset[0] = 901
    _complete(host)
    assert host._provider_retry_status("batch-host")["state"] == "ready"
    processes = [a for a in host.store.list_attempts() if a["kind"] == "PROCESS"]
    assert len(processes) == 2 and all(a["state"] == "CLOSED" for a in processes)
    from hypoloom.oracle import Oracle
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


def test_live_response_releases_dispatch_before_agent_closes_and_repeat_clear_is_noop(batch_host, monkeypatch):
    import threading
    from hypoloom.provider_traffic import for_run
    host = batch_host
    original = host._accept_agent_response
    monkeypatch.setattr(host, '_accept_agent_response', lambda request, response, **kw:
        host._agent_failure(request, AttemptTerminalReason.FAILED, 'nonzero_exit', retryable=True,
            details={'diagnostics': {'provider_failure': {'code': 'rate_limited', 'http_status': 429}}}))
    _until(host, lambda: host._provider_retry_status('batch-host')['state'] == 'cooldown')
    monkeypatch.setattr(host, '_accept_agent_response', original)
    clear = host.handle(ControlRequest(1, 'first-clear', None, 'PROVIDER_RETRY_CLEAR',
        {'run_id': 'batch-host', 'reason': 'operator'}))
    assert clear.status == 'accepted'
    boundary = clear.result['provider_retry']['cleared_at']
    finish, held = host._finish_fake_agent, []
    monkeypatch.setattr(host, '_finish_fake_agent', lambda request, raw: held.append((request, raw)))
    _until(host, lambda: bool(held))
    assert host._provider_retry_status('batch-host')['pending']
    again = host.handle(ControlRequest(1, 'second-clear', None, 'PROVIDER_RETRY_CLEAR',
        {'run_id': 'batch-host', 'reason': 'operator'}))
    assert again.status == 'accepted' and again.result['recovery_already_requested']
    assert again.result['provider_retry']['cleared_at'] == boundary
    assert again.result['provider_retry']['pending']
    for _ in range(3): host.tick()
    assert len(held) == 1
    traffic = for_run(host, 'batch-host')
    ticket = traffic.acquire(model='fixture', owner=('expert', held[0][0].role), connection=boundary,
        deadline=time.monotonic() + 5, cancelled=threading.Event())
    traffic.finish(ticket, {'request_path': '/v1/messages', 'upstream_status': 200,
        'message_complete': True, 'response_body_complete': True, 'ended_at': time.time()})
    assert host.store.get_attempt(held[0][0].attempt_id)['state'] == 'OPEN'
    assert host._provider_retry_status('batch-host')['state'] == 'ready'
    ready_clear = host.handle(ControlRequest(1, 'ready-clear', None, 'PROVIDER_RETRY_CLEAR',
        {'run_id': 'batch-host', 'reason': 'operator'}))
    assert ready_clear.status == 'accepted' and ready_clear.result['recovery_already_requested']
    assert ready_clear.result['provider_retry']['cleared_at'] == boundary
    monkeypatch.setattr(host, '_finish_fake_agent', finish)
    finish(*held[0])
    _complete(host)
    from hypoloom.oracle import Oracle
    report = Oracle(host.store.database_path, host.publisher.root).audit('batch-host')
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["iterate"], indirect=True)
def test_failure_after_an_existing_result_closes_chain_without_inventing_next_result(batch_host, monkeypatch):
    host = batch_host
    accept = host._accept_agent_response
    failed = []
    def reject_next_design(request, response, **kwargs):
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and request.task_context.key.experiment_ordinal == 2:
            failed.append(request.attempt_id)
            host._agent_failure(request, AttemptTerminalReason.FAILED, "invalid_tool_workspace", retryable=False)
        else: accept(request, response, **kwargs)
    monkeypatch.setattr(host, "_accept_agent_response", reject_next_design)
    _complete(host)
    assert len(failed) == 2
    assert len(host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == 2
    assert len(host.store.list_facts("batch-host", "ExperimentPreregistered")) == 2
    from hypoloom.oracle import Oracle
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()
    # Removing just one chain's successor failure must invalidate adjudication;
    # the other chain's real failure is insufficient evidence for this one.
    from hypoloom.batch_oracle import audit_batches
    attempts = host.store.list_attempts()
    observations = {a["attempt_id"]: json.loads(a["observation_json"]) for a in attempts}
    facts = host.store.inspect("SELECT rowid AS raw_order,* FROM research_facts WHERE run_id=?", ("batch-host",))
    fact_payloads = {f["fact_id"]: json.loads(f["payload_json"]) for f in facts}
    transitions = host.store.inspect("SELECT t.rowid AS raw_order,t.*,c.command_type FROM workflow_transitions t "
        "JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.run_id=?", ("batch-host",))
    damaged = [r for r in transitions if not (json.loads(r["payload_json"]).get("agent_final_failure")
        and json.loads(r["payload_json"]).get("attempt_id") == failed[0])]
    commands = host.store.inspect("SELECT * FROM workflow_commands WHERE run_id=?", ("batch-host",))
    issues = audit_batches("batch-host", attempts, observations, facts, fact_payloads, damaged, commands)
    assert any(code == "batch_adjudication_invalid" and "still has scientific iterations" in message for code, message, _ in issues), issues


def test_actual_host_status_reaches_observer_snapshot(batch_host, monkeypatch):
    from hypoloom import monitor
    host = batch_host
    host._accept_agent_response = lambda request, response, **kw: host._agent_failure(request,
        AttemptTerminalReason.FAILED, "nonzero_exit", retryable=True,
        details={"diagnostics": {"provider_failure": {"code": "quota_exhausted", "http_status": 429}}})
    _until(host, lambda: host._provider_retry_pending("batch-host"))
    monkeypatch.setattr(monitor, "request_over_socket", lambda path, request, **kw: host.handle(request))
    from hypoloom.control_protocol import socket_address
    address, descriptor = socket_address(host.state_root / "control.sock")
    try:
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(address)
            snapshot = monitor.build_observer_snapshot(host.state_root, "batch-host")
    finally:
        if descriptor is not None: os.close(descriptor)
    assert snapshot.status_source.endswith("+resident-host-status")
    assert snapshot.status["provider_retry"]["reason"] == "quota_exhausted"
    assert snapshot.status["provider_retry"]["requires_operator"]


def test_connection_selection_preserves_pinning_replays_and_reloads_after_restart(tmp_path):
    from test_host_settings import _bound_host, _source
    from test_phase8_parallel_adversarial import _create_run
    from hypoloom.host_settings import prepare_host_settings, HostConfigurationError
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    private = prepare_host_settings(root, _source(tmp_path))
    original = private.read_bytes()
    selected = tmp_path / "connection.json"
    selected.write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "replacement-secret", "ANTHROPIC_BASE_URL": "http://127.0.0.1:8999"}}))
    selected.chmod(0o600)
    host = _bound_host(root, private)
    host.initialize()
    try:
        _create_run(host, "switch")
        before = host.version
        request = ControlRequest(1, "switch-1", None, "PROVIDER_RETRY_CLEAR", {"run_id": "switch", "reason": "operator", "settings_path": str(selected)})
        response = host.handle(request)
        assert response.status == "accepted", response
        replay = host.handle(request)
        assert replay.status == "accepted" and replay.revision == response.revision
        assert replay.result["provider_retry"]["cleared_at"] == response.result["provider_retry"]["cleared_at"]
        assert len(host.store.inspect("SELECT 1 FROM workflow_commands WHERE command_id=?", (request.request_id,))) == 1
        assert host._provider_environment_for("switch")["ANTHROPIC_AUTH_TOKEN"] == "replacement-secret"
        assert host._provider_environment_for("switch")["ANTHROPIC_API_KEY"] == ""
        assert "replacement-secret" not in json.dumps(host.store.inspect("SELECT * FROM workflow_transitions WHERE run_id=?", ("switch",)))
        assert private.read_bytes() == original
        assert host._runtime_version() == before
        host._check_agent_runtime()
    finally: host.close()
    reopened = _bound_host(root, private)
    reopened.initialize()
    try:
        assert reopened._provider_environment_for("switch")["ANTHROPIC_AUTH_TOKEN"] == "replacement-secret"
        selected.write_text('{"env":{"ANTHROPIC_API_KEY":"new-secret","ANTHROPIC_MODEL":"other"}}')
        rejected = reopened.handle(ControlRequest(1, "switch-2", None, "PROVIDER_RETRY_CLEAR", request.payload))
        assert rejected.status == "rejected"
        assert reopened._provider_environment_for("switch")["ANTHROPIC_AUTH_TOKEN"] == "replacement-secret"
        private.write_bytes(b'{"env":{"ANTHROPIC_API_KEY":"unauthorized"}}')
        with pytest.raises(HostConfigurationError): reopened._check_agent_runtime()
    finally: reopened.close()
    private.write_bytes(original)
    selected.unlink()
    recovered = _bound_host(root, private)
    recovered.initialize()
    try:
        assert recovered._provider_retry_status("switch")["reason"] == "connection_configuration"
        before = len(recovered.store.list_attempts())
        for _ in range(3): recovered.tick()
        assert len(recovered.store.list_attempts()) == before
        selected.write_text('{"env":{"ANTHROPIC_API_KEY":"new-valid-key"}}')
        selected.chmod(0o600)
        response = recovered.handle(ControlRequest(1, "switch-3", None, "PROVIDER_RETRY_CLEAR", request.payload))
        assert response.status == "accepted", response
        _until(recovered, lambda: bool(recovered.claude_agent_host.provider_environments))
        assert recovered.claude_agent_host.provider_environments[0]["ANTHROPIC_API_KEY"] == "new-valid-key"
        assert recovered.claude_agent_host.provider_environments[0]["ANTHROPIC_AUTH_TOKEN"] == ""
    finally: recovered.close()


def test_web_provider_requires_its_own_grant_and_exact_request_retry(tmp_path, monkeypatch):
    import hypoloom.control_protocol as transport
    from hypoloom.web_observer import create_server
    from test_web_observer import _TOKEN, _start, _stop
    calls = []
    def send(path, request, **kwargs):
        calls.append(request)
        if len(calls) == 1: raise ConnectionError("reply lost")
        return ControlResponse.accepted(request.request_id, 2, {"provider_retry": {"state": "recovering"}})
    monkeypatch.setattr(transport, "request_over_socket", send)
    server = create_server(tmp_path / "absent", "selected", port=0, token=_TOKEN)
    thread = _start(server)
    def post(grant="", origin=None, **extra):
        connection = http.client.HTTPConnection(*server.server_address, timeout=10)
        try:
            connection.request("POST", "/api/provider-retry", json.dumps({"request_id": "exact", **extra}),
                {"Authorization": "Bearer " + _TOKEN, "Content-Type": "application/json", "X-Hypoloom-Provider-Token": grant,
                    "Origin": origin or f"http://127.0.0.1:{server.server_port}"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally: connection.close()
    try:
        assert post()[0] == 403
        answer = parse_qs(urlsplit(server.human_answer_url()).fragment)["answer_token"][0]
        grant = parse_qs(urlsplit(server.provider_control_url()).fragment)["provider_token"][0]
        assert post(answer)[0] == 403 and post(grant, origin="https://example.invalid")[0] == 403
        assert post(grant, run_id="foreign")[0] == 400
        assert post(grant, settings_path="secret")[0] == 400
        assert not calls
        assert post(grant, settings_path="/operator/connection.json")[0] == 503
        assert post(grant, settings_path="/operator/connection.json")[0] == 200
        assert calls[0] == calls[1]
        assert calls[0].payload == {"run_id": "selected", "reason": "operator", "settings_path": "/operator/connection.json"}
    finally: _stop(server, thread)


def test_cli_selects_connection_and_web_control_is_explicit(tmp_path, monkeypatch):
    from hypoloom import cli, web_observer
    calls = []
    monkeypatch.setattr(cli, "_submit", lambda args, command, payload: calls.append((command, payload)) or 0)
    assert cli.main(["provider-retry-clear", "--state-root", str(tmp_path), "--run-id", "run", "--settings", "/operator/connection.json"]) == 0
    assert calls == [("PROVIDER_RETRY_CLEAR", {"run_id": "run", "reason": "operator", "settings_path": "/operator/connection.json"})]
    options = []
    monkeypatch.setattr(web_observer, "run_web_observer", lambda *a, **kw: options.append(kw) or 0)
    assert cli.main(["web", "--state-root", str(tmp_path), "--run-id", "run"]) == 0
    assert not options[-1].get("allow_provider_control")
    assert cli.main(["web", "--state-root", str(tmp_path), "--run-id", "run", "--allow-provider-control"]) == 0
    assert options[-1]["allow_provider_control"]


def test_web_resource_recheck_keeps_auth_origin_and_run_scope(tmp_path, monkeypatch):
    import hypoloom.control_protocol as transport
    from hypoloom.web_observer import create_server
    from test_web_observer import _TOKEN, _start, _stop
    calls = []
    def send(path, request, **kwargs):
        calls.append(request)
        return ControlResponse.accepted(request.request_id, 2, {})
    monkeypatch.setattr(transport, "request_over_socket", send)
    server = create_server(tmp_path / "absent", "selected", port=0, token=_TOKEN)
    thread = _start(server)
    def post(grant="", origin=None, **extra):
        connection = http.client.HTTPConnection(*server.server_address, timeout=10)
        try:
            connection.request("POST", "/api/resource-recheck", json.dumps({"request_id": "recheck", **extra}),
                {"Authorization": "Bearer " + _TOKEN, "Content-Type": "application/json", "X-Hypoloom-Provider-Token": grant,
                 "Origin": origin or f"http://127.0.0.1:{server.server_port}"})
            response = connection.getresponse(); response.read()
            return response.status
        finally: connection.close()
    try:
        grant = parse_qs(urlsplit(server.provider_control_url()).fragment)["provider_token"][0]
        assert post() == 403 and post(grant, origin="http://example.invalid") == 403
        assert post(grant, run_id="foreign") == 400 and post(grant, settings_path="/other") == 400
        assert not calls
        assert post(grant) == 200
        assert calls[0].command == "RESOURCE_RECHECK" and calls[0].payload == {"run_id": "selected", "reason": "operator"}
    finally: _stop(server, thread)
