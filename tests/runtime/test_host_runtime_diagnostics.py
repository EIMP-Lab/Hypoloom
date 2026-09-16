"""Host failures are evidence even when no Agent Attempt could be opened."""
from __future__ import annotations

import json

import pytest

import hypoloom.workflow_host as module
from hypoloom.agent_aci import AgentACIError
from hypoloom.control_protocol import ControlRequest
from tests.runtime.test_shared_acceptance_host import RUN_ID, shared_host as shared_host


def _records(root):
    return [json.loads(line) for line in (root / "diagnostics/host.jsonl").read_text().splitlines()]


def test_dispatch_error_isolated_from_peer_and_control_cancel_still_closes(shared_host, monkeypatch):
    from hypoloom.oracle import Oracle
    from tests.runtime.test_host_experiment_tools import _until

    host, _ = shared_host
    raw = json.loads(host.store.inspect("SELECT request_json FROM workflow_commands WHERE command_type='CREATE_RUN'")[0]["request_json"])
    payload = dict(raw.get("payload", raw))
    assert "research_contract" in payload
    payload["run_id"] = "peer-run"
    created = host.handle(ControlRequest(1, "create-peer", 0, "CREATE_RUN", payload))
    assert created.status == "accepted", created
    host.tick()
    original = host._drive_active
    called = []
    def fail_one(run):
        called.append(run["run_id"])
        if run["run_id"] == RUN_ID:
            raise ValueError("Observer must start a fresh stateless session")
        return original(run)
    monkeypatch.setattr(host, "_drive_active", fail_one)
    host.tick()
    host.tick()
    assert called.count(RUN_ID) == 1 and called.count("peer-run") == 2
    assert set(host.dispatch_errors) == {RUN_ID}
    status = host.handle(ControlRequest(1, "quarantine-status", None, "STATUS", {"run_id": RUN_ID}))
    assert status.status == "accepted" and status.result["dispatch_error"]["exception_type"] == "ValueError"
    for run_id in (RUN_ID, "peer-run"):
        response = host.handle(ControlRequest(1, "cancel-" + run_id, host.store.latest_transition(run_id)["revision"], "CANCEL", {"run_id": run_id, "cause": "fixture cleanup"}))
        assert response.status == "accepted", response
    _until(host, lambda: all(host.store.latest_transition(run_id)["state"] == "CANCELLED" for run_id in (RUN_ID, "peer-run")))
    assert not [row for row in host.store.list_attempts() if row["state"] == "OPEN"]
    for run_id in (RUN_ID, "peer-run"):
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        assert report.ok, report.to_dict()


@pytest.mark.parametrize("exception", [module.OperationalHalt("integrity"), module.sqlite3.DatabaseError("sqlite")])
def test_dispatch_integrity_and_database_errors_still_halt(shared_host, monkeypatch, exception):
    host, _ = shared_host
    host.tick()
    def fail(run):
        raise exception
    monkeypatch.setattr(host, "_drive_active", fail)
    with pytest.raises(type(exception)):
        host.tick()
    assert host.dispatch_errors == {}


def test_pre_attempt_request_failure_records_original_traceback_without_fake_attempt(shared_host, monkeypatch):
    host, _ = shared_host
    host.tick()  # CREATED -> ACTIVE, before the first Agent request.
    before = host.store.list_facts(RUN_ID)
    assert not host.store.list_attempts()
    failure = AgentACIError("duplicate_input_artifact", "reproduced request construction failure")

    def fail_request(**kwargs):
        raise failure

    monkeypatch.setattr(module, "AgentRequest", fail_request)
    host.tick()
    assert host.dispatch_errors[RUN_ID]["exception_type"] == "AgentACIError"
    host.tick()
    assert not host.store.list_attempts()
    assert host.store.list_facts(RUN_ID) == before
    records = _records(host.state_root)
    assert len(records) == 1
    record = records[0]
    assert record["run_id"] == RUN_ID
    assert record["phase"] == "dispatch_quarantined"
    assert record.get("attempt_id") is None
    assert "fail_request" in record["traceback"]
    assert "duplicate_input_artifact" in record["traceback"]


def test_control_exception_keeps_protocol_result_and_durable_traceback(shared_host, monkeypatch):
    host, _ = shared_host

    def fail_status(request):
        raise RuntimeError("control status diagnostic")

    monkeypatch.setattr(host, "_status_response", fail_status)
    request = ControlRequest(1, "diagnostic-status", None, "STATUS", {"run_id": RUN_ID})
    response = host.handle(request)
    assert response.status == "error" and response.error["code"] == "host_error"
    record = _records(host.state_root)[0]
    assert record["request_id"] == request.request_id
    assert record["run_id"] == RUN_ID and record.get("attempt_id") is None
    assert "fail_status" in record["traceback"]
    assert not host.store.list_attempts()


def test_initialize_failure_is_recorded_without_initializing_authoritative_state(tmp_path, monkeypatch):
    host = module.WorkflowHost(tmp_path / "state")

    def failed_preflight():
        raise RuntimeError("runtime probe unavailable")

    monkeypatch.setattr(host.compute_resources, "snapshot", failed_preflight)
    try:
        with pytest.raises(RuntimeError, match="runtime probe unavailable"):
            host.initialize()
        assert not host.store.database_path.exists()
        record = _records(host.state_root)[0]
        assert record["phase"] == "initialize"
        assert record.get("run_id") is None and record.get("attempt_id") is None
        assert "failed_preflight" in record["traceback"]
    finally:
        host.close()


def test_host_error_copy_redacts_known_credentials_without_masking_exception(shared_host, monkeypatch):
    host, _ = shared_host
    credential = "host-private-diagnostic-test-credential"
    monkeypatch.setattr(host, "_provider_environment", {"ANTHROPIC_API_KEY": credential})
    failure = RuntimeError("upstream echoed " + credential)

    def failed_tick():
        raise failure

    monkeypatch.setattr(host, "_tick", failed_tick)
    with pytest.raises(RuntimeError) as raised:
        host.tick()
    assert raised.value is failure
    raw = (host.state_root / "diagnostics/host.jsonl").read_text()
    assert credential not in raw and "[REDACTED]" in raw
    assert "secrets" not in _records(host.state_root)[0]


def test_host_error_storage_failure_keeps_original_exception(shared_host, monkeypatch, capsys):
    host, _ = shared_host
    (host.state_root / "diagnostics").write_text("unavailable directory")
    failure = RuntimeError("original dispatch failure")

    def failed_tick():
        raise failure

    monkeypatch.setattr(host, "_tick", failed_tick)
    with pytest.raises(RuntimeError) as raised:
        host.tick()
    assert raised.value is failure
    assert "diagnostic capture failed" in capsys.readouterr().err
    assert not host.store.list_attempts()
