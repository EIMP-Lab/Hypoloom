import json
import time

import pytest

from hypoloom.agent_aci import AgentRequest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.lifecycle import Command, RunState
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_batch_host import batch_host as _batch_host

batch_host = _batch_host


def _cancel(host, cause, identity="stop-request"):
    head = host.store.latest_transition("batch-host")
    result = host.handle(ControlRequest(1, identity, head["revision"], "CANCEL",
        {"run_id": "batch-host", "cause": cause}))
    assert result.status == "accepted", result


def _drive(host, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        host.tick()
        if predicate(): return
        time.sleep(.005)
    raise AssertionError(host.store.latest_transition("batch-host"))


def _reports(host):
    return [row for row in host.store.list_attempts() if row["kind"] == "AGENT"
        and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT"]


def _context(row):
    request = AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
    return next(item for item in request.input_artifacts if item.ref.endswith("--final-report-context"))


def test_grounding_failure_report_names_actual_stage_and_zero_experiments(batch_host, monkeypatch):
    from dataclasses import replace
    from hypoloom.agent_aci import with_output_digest

    host = batch_host
    invoke = host.agent.invoke
    def fail_grounding(request):
        response = invoke(request)
        return with_output_digest(replace(response, typed_candidate={"invalid": True})) if request.command is AgentCommand.GROUNDING else response
    monkeypatch.setattr(host.agent, "invoke", fail_grounding)
    _drive(host, lambda: host.store.latest_transition("batch-host")["state"] == "COMPLETED")
    context = json.loads(_context(_reports(host)[0]).content_bytes())
    outcome = context["run_outcome"]
    assert outcome["expert_nodes_started"] == outcome["process_attempts"] == 0
    assert outcome["fact_counts"]["HypothesisProposed"] == outcome["fact_counts"]["ExperimentPreregistered"] == 0
    assert outcome["final_failures"][-1]["command"] == "GROUNDING"
    assert "0 hypotheses" in context["stop_reason"]
    assert "after the admitted expert rounds" not in context["stop_reason"]
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


@pytest.mark.parametrize("stop", ["cancel", "failed"])
def test_early_stop_reports_no_verdict_and_keeps_actual_disposition(batch_host, stop):
    host = batch_host
    if stop == "cancel": _cancel(host, "operator ended the unstarted study")
    else:
        with host.store.transaction() as tx:
            tx.append_transition(host._transition_record(tx, "batch-host", "batch-host", "RUN",
                Command.FAIL_INTEGRITY, RunState.STOPPING,
                payload={"stop_disposition": "FAILED", "cause": "typed test integrity failure"}))
        _cancel(host, "later cancel cannot downgrade failure")
    expected = "CANCELLED" if stop == "cancel" else "FAILED"
    _drive(host, lambda: host.store.latest_transition("batch-host")["state"] in {"FAILED", "CANCELLED"})
    assert host.store.latest_transition("batch-host")["state"] == expected
    rows = _reports(host)
    assert len(rows) == 1 and rows[0]["terminal_reason"] == "SUCCEEDED"
    assert not host.store.list_facts("batch-host", "GoalVerdictRecorded")
    context = json.loads(_context(rows[0]).content_bytes())
    assert context["stop_controls"] and all(row["command_id"] in context["stop_reason"] for row in context["stop_controls"])
    assert "as of" in context["stop_reason"].lower()
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


@pytest.mark.parametrize("batch_host", ["final-report-missing"], indirect=True)
def test_cancelled_report_exhaustion_is_control_failure_not_integrity(batch_host):
    host = batch_host
    _cancel(host, "cancel before a scientific verdict")
    _drive(host, lambda: host.store.latest_transition("batch-host")["state"] in {"FAILED", "CANCELLED"})
    assert host.store.latest_transition("batch-host")["state"] == "FAILED"
    transitions = host.store.inspect("SELECT t.*,c.command_type FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.run_id='batch-host' ORDER BY t.rowid")
    assert any(row["command_type"] == "FINAL_REPORT_FAILED" for row in transitions)
    assert not any(row["command_type"] == "FAIL_INTEGRITY" for row in transitions)
    assert transitions[-1]["command_type"] == "FINISH_STOP"
    assert not host.store.list_facts("batch-host", "GoalVerdictRecorded")
    assert len({_context(row).content_base64 for row in _reports(host)}) == 1
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


def test_stopped_report_open_restart_reuses_first_source_and_unique_command(batch_host, monkeypatch):
    host = batch_host
    _cancel(host, "initial operator cause")
    finish = host._finish_fake_agent
    monkeypatch.setattr(host, "_finish_fake_agent", lambda request, raw: None if request.command is AgentCommand.FINAL_REPORT else finish(request, raw))
    _drive(host, lambda: bool(_reports(host)))
    first = _reports(host)[0]
    assert first["state"] == "OPEN"
    _cancel(host, "later cause is visible but cannot rebind source", "stop-later")
    host.close()
    recovered = WorkflowHost(host.state_root, local_agent=host.agent)
    recovered.initialize()
    try:
        _drive(recovered, lambda: recovered.store.latest_transition("batch-host")["state"] == "CANCELLED")
        rows = _reports(recovered)
        assert [row["terminal_reason"] for row in rows] == ["LOST", "SUCCEEDED"]
        assert len({row["command_id"] for row in rows}) == 1
        assert {_context(row).content_base64 for row in rows} == {_context(first).content_base64}
        audit = Oracle(recovered.store.database_path, recovered.publisher.root).audit("batch-host")
        assert audit.ok, audit.to_dict()
    finally:
        recovered.close()


def test_real_cpu_cancel_after_normal_report_intent_keeps_as_of_source(batch_host, monkeypatch):
    host = batch_host; held = []
    finish = host._finish_fake_agent
    def hold(request, raw):
        if request.command is AgentCommand.FINAL_REPORT: held.append((request, raw))
        else: finish(request, raw)
    monkeypatch.setattr(host, "_finish_fake_agent", hold)
    _drive(host, lambda: bool(held))
    assert len(host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == 2
    assert len(host.store.list_facts("batch-host", "GoalVerdictRecorded")) == 1
    source = _context(_reports(host)[0]).content_base64
    _cancel(host, "operator cancelled after the report source was frozen")
    finish(*held[0])
    _drive(host, lambda: host.store.latest_transition("batch-host")["state"] == "CANCELLED")
    rows = _reports(host)
    assert len(rows) == 1 and rows[0]["terminal_reason"] == "SUCCEEDED"
    assert _context(rows[0]).content_base64 == source
    assert host.store.inspect("SELECT COUNT(*) n FROM execution_attempts WHERE state='OPEN'")[0]["n"] == 0
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


@pytest.mark.parametrize("batch_host", ["parallel"], indirect=True)
def test_real_cpu_stop_drains_processes_before_freezing_no_verdict_report(batch_host):
    host = batch_host
    _drive(host, lambda: bool(host.running))
    processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    assert any(row["process_group_id"] for row in processes)
    _cancel(host, "stop the admitted CPU work without a scientific verdict")
    _drive(host, lambda: host.store.latest_transition("batch-host")["state"] == "CANCELLED")
    report = _reports(host)[0]
    processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    assert all(row["state"] == "CLOSED" and row["ended_at"] <= report["started_at"] for row in processes)
    assert not host.store.list_facts("batch-host", "GoalVerdictRecorded")
    assert host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
    assert not host.running and not host.running_agents
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


@pytest.mark.parametrize("batch_host", ["legacy13"], indirect=True)
def test_pre14_cancel_keeps_legacy_no_report_semantics(batch_host):
    host = batch_host
    assert int(host.store.get_run("batch-host")["agent_aci_version"]) == 13
    _cancel(host, "retained pre14 control behavior")
    _drive(host, lambda: host.store.latest_transition("batch-host")["state"] == "CANCELLED")
    assert not _reports(host)
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


def test_terminal_run_cannot_open_a_report_or_other_auxiliary(batch_host):
    from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask
    host = batch_host
    _cancel(host, "stop before work")
    _drive(host, lambda: host.store.latest_transition("batch-host")["state"] == "CANCELLED")
    count = len(host.store.list_attempts())
    for kind in (AuxiliaryRequestKind.FINAL_REPORT, AuxiliaryRequestKind.GROUNDING):
        host._expert_agent_step("batch-host", "batch-host", AuxiliaryTask(kind=kind, run_id="batch-host"), "grounding", "grounding")
    assert len(host.store.list_attempts()) == count
