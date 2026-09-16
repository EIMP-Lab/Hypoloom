"""Bounded projection work must preserve validators and detailed evidence."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from hypoloom import monitor, observer_reliability, observer_summary
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.state_store import StateStore
from test_monitor import _create_fresh_run
from test_observer_reliability import build, correction, row
from test_observer_summary import _summary, _transition


def test_lineage_validates_only_relevant_updates_and_rejects_bad_response(monkeypatch):
    contract = SimpleNamespace(document={"methodology": {"controls": {
        "research_loop": "expert-six-node/1"}}})
    hypotheses = [{"payload": {"component_id": "primary.score", "proposer_role": "scientist"}}]
    attempts = []
    for command in (AgentCommand.CYCLE_REPORT, AgentCommand.HYPOTHESIS_UPDATE):
        request = default_request(command)
        response = FakeAgent().invoke(request)
        attempts.append({"state": "CLOSED", "terminal_reason": "SUCCEEDED",
            "observation_json": json.dumps({"request": request.to_dict(), "response": response.to_dict()})})
    original = monitor.validate_postflight
    validated = []

    def validate(request, response):
        validated.append(request.command)
        return original(request, response)

    monkeypatch.setattr(monitor, "validate_postflight", validate)
    assert monitor._expert_lineage_termination_components(contract, attempts, hypotheses,
        research_contract_version="hypoloom-research-v12") == frozenset({"primary.score"})
    assert validated == [AgentCommand.HYPOTHESIS_UPDATE]
    observation = json.loads(attempts[-1]["observation_json"])
    observation["response"]["typed_candidate"]["summary"] = "Changed after response creation"
    attempts[-1]["observation_json"] = json.dumps(observation)
    validated.clear()
    assert monitor._expert_lineage_termination_components(contract, attempts, hypotheses,
        research_contract_version="hypoloom-research-v12") == frozenset()
    assert validated == [AgentCommand.HYPOTHESIS_UPDATE]


@pytest.mark.parametrize("malformed", ["role", "inputs", "task", "response", "parsed_observation"])
def test_lineage_malformed_relevant_observations_never_terminate(malformed):
    request = default_request(AgentCommand.HYPOTHESIS_UPDATE)
    response = FakeAgent().invoke(request)
    observation = {"request": request.to_dict(), "response": response.to_dict()}
    if malformed == "role":
        observation["request"]["role"] = []
    elif malformed == "inputs":
        observation["request"]["input_artifacts"] = [{"ref": "broken"}]
    elif malformed == "task":
        observation["request"]["task_context"]["key"] = []
    elif malformed == "response":
        observation["response"] = []
    attempt = {"state": "CLOSED", "terminal_reason": "SUCCEEDED",
        "observation_json": json.dumps(observation)}
    if malformed == "parsed_observation":
        attempt["observation_json"] = "[]"
        attempt["observation"] = []
    contract = SimpleNamespace(document={"methodology": {"controls": {
        "research_loop": "expert-six-node/1"}}})
    hypotheses = [{"payload": {"component_id": "primary.score", "proposer_role": "scientist"}}]
    assert monitor._expert_lineage_termination_components(contract, [attempt], hypotheses,
        research_contract_version="hypoloom-research-v12") == frozenset()


def test_observer_accepts_supplied_audit_and_transaction_but_defaults_to_full_audit(tmp_path, monkeypatch):
    root, run_id = tmp_path / "state", "projection-audit"
    _create_fresh_run(root, run_id)
    audits = []
    original = monitor.Oracle.audit

    def audit(self, selected):
        audits.append(selected)
        return original(self, selected)

    monkeypatch.setattr(monitor.Oracle, "audit", audit)
    pending = {"run_id": run_id, "status": "pending", "clean": None, "issues": []}
    with StateStore(root).readonly_connection() as connection:
        connection.execute("BEGIN")
        snapshot = monitor.build_observer_snapshot(root, run_id,
            _connection=connection, _oracle=pending)
        assert connection.in_transaction
    assert snapshot.oracle == pending
    assert audits == []
    complete = monitor.build_monitor_snapshot(root, run_id)
    assert audits == [run_id]
    assert complete.oracle["run_id"] == run_id


def test_host_research_files_do_not_replay_audit_or_historical_call_diagnostics(tmp_path, monkeypatch):
    from hypoloom.research_projection import build_research_projection

    root, run_id = tmp_path / "state", "projection-no-diagnostics"
    _create_fresh_run(root, run_id)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Host projection ran independent historical diagnostics")

    monkeypatch.setattr(monitor.Oracle, "audit", unexpected)
    monkeypatch.setattr(observer_summary, "build_run_summary", unexpected)
    projection = build_research_projection(root, run_id)
    snapshot = json.loads(projection.files["run.json"])
    assert snapshot["oracle"]["ok"] is None
    assert snapshot["overview"] == {}
    assert snapshot["status"]["run_id"] == run_id
    assert snapshot["fact_counts"]["GoalDefined"] == 1


def test_idle_host_skips_unchanged_projection_but_tracks_attempts_and_retries(tmp_path, monkeypatch):
    from hypoloom import research_projection, workflow_host
    from hypoloom.control_protocol import ControlRequest
    from test_monitor import _v4_payload

    root, run_id = tmp_path / "state", "projection-refresh"
    clock, published = [100.0], []
    monkeypatch.setattr(workflow_host.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(workflow_host.WorkflowHost, "_drive_active", lambda *_: None)

    def publish(*args):
        published.append(args)

    monkeypatch.setattr(research_projection, "update_research_projection", publish)
    host = workflow_host.WorkflowHost(root)
    try:
        host.initialize()
        payload = _v4_payload(run_id)
        payload["research_contract"]["methodology"]["controls"]["research_loop"] = "expert-six-node/1"
        assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload)).status == "accepted"
        host.tick()
        assert len(published) == 1
        clock[0] += 2
        host.tick()
        assert len(published) == 1
        revision = host.store.latest_transition(run_id)["revision"]
        with host.store.transaction() as tx:
            tx.record_command({"command_id": "agent-command", "run_id": run_id, "workflow_id": run_id,
                "command_type": "REQUEST_RESEARCH_NODE", "request": {}, "status": "accepted", "result": {}})
            tx.open_attempt({"attempt_id": "agent-attempt", "run_id": run_id, "workflow_id": run_id,
                "command_id": "agent-command", "kind": "AGENT", "host_generation": host.generation,
                "attempt_ordinal": 1, "started_at": 1.0, "observation": {"request": {}}})
        clock[0] += 2
        host.tick()
        assert len(published) == 2
        clock[0] += 2
        host.tick()
        assert len(published) == 3  # Open Attempts may append logs without DB writes.
        with host.store.transaction() as tx:
            tx.close_attempt("agent-attempt", terminal_reason="FAILED", ended_at=2.0, observation={})
        assert host.store.latest_transition(run_id)["revision"] == revision
        clock[0] += 2

        def unavailable(*args):
            raise research_projection.ResearchProjectionError("temporary publication failure")

        monkeypatch.setattr(research_projection, "update_research_projection", unavailable)
        host.tick()
        assert run_id in host.projection_errors
        monkeypatch.setattr(research_projection, "update_research_projection", publish)
        clock[0] += 2
        host.tick()
        assert len(published) == 4 and run_id not in host.projection_errors
        clock[0] += 2
        host.tick()
        assert len(published) == 4
    finally:
        host.close()
    restarted = workflow_host.WorkflowHost(root)
    try:
        restarted.initialize()
        restarted.tick()
        assert len(published) == 5  # No persisted cache can hide the first refresh.
    finally:
        restarted.close()


def test_summary_parses_each_transition_once_without_changing_caller_rows(tmp_path, monkeypatch):
    transitions = [_transition(1, payload={"role": "scientist", "detail": "recorded"})]
    transitions[0]["payload_json"] = json.dumps(transitions[0].pop("payload"))
    original_rows = copy.deepcopy(transitions)
    encoded = transitions[0]["payload_json"]
    loads = observer_summary.json.loads
    calls = []

    def decode(value, *args, **kwargs):
        if value == encoded:
            calls.append(value)
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(observer_summary.json, "loads", decode)
    result = _summary(tmp_path, transitions=transitions,
        experts=[{"role": "scientist"}, {"role": "peer"}, {"role": "other"}])
    assert result["experts"][0]["last_activity_at"] == 0
    assert len(calls) == 1
    assert transitions == original_rows


def test_reliability_parses_each_attempt_once_and_keeps_expert_groups(tmp_path, monkeypatch):
    attempt = row("a", correction=correction())
    encoded = attempt["observation_json"]
    loads = observer_reliability.json.loads
    calls = []

    def decode(value, *args, **kwargs):
        if value == encoded:
            calls.append(value)
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(observer_reliability.json, "loads", decode)
    assert build([attempt])["counts"]["agent_attempts"] == 1
    assert len(calls) == 1
    summary = _summary(tmp_path, attempts=[attempt])
    expert = summary["experts"][0]["reliability"]
    assert "nodes" not in expert
    assert expert["groups"] == summary["reliability"]["groups"]
    assert expert["metrics"] == summary["reliability"]["metrics"]
    assert summary["reliability"]["nodes"][0]["attempt_ids"] == ["a"]
