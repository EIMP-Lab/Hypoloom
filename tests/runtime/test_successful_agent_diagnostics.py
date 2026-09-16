"""Adapter observations survive existing Host acceptance, not replace it."""

import copy
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentResponse, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.debug_mode import build_debug_report
from hypoloom.diagnostics import structured_output_trace
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.oracle import Oracle, _audit_agent_attempts
from hypoloom.workflow_host import WorkflowHost
from test_expert_loop_host import _pilot_payload, mount_root as mount_root
from test_phase8_parallel_host import _payload
from test_structured_output_trace import _call, _result


def _diagnostics():
    return {"provider_subtype": "success", "structured_output_correction":
        structured_output_trace([
            _call("first", {"typed_candidate": {"extra": "private candidate"}}),
            _result("first", error=True, content="unexpected candidate field"),
            _call("second", {"typed_candidate": {"method": "private correction"}}),
            _result("second"),
        ])}


def _poll_fake_response(host, monkeypatch, request, response, diagnostics):
    """Only substitute the adapter boundary; use actual Host/DB/validators."""
    assert isinstance(response, AgentResponse)
    observation = SimpleNamespace(response=response, diagnostics=diagnostics,
        returncode=0, pid=123, process_group_id=123, failure_code=None)
    cleaned = []
    adapter = SimpleNamespace(poll=lambda handle: observation,
                              cleanup=lambda handle: cleaned.append(handle.request.attempt_id))
    host.running_agents[request.attempt_id] = SimpleNamespace(request=request, tool_workspace=None)
    with monkeypatch.context() as context:
        context.setattr(host, "claude_agent_host", adapter)
        assert host._poll_agents()
    assert cleaned == [request.attempt_id]
    assert not host.running_agents


@pytest.mark.parametrize("command", [
    AgentCommand.PROPOSE_HYPOTHESIS,
    AgentCommand.DRAFT_EXPERIMENT_SPEC,
    AgentCommand.ANSWER_RENDEZVOUS,
    AgentCommand.HYPOTHESIS_PROPOSAL,
])
def test_successful_agent_closure_keeps_adapter_diagnostics(
    tmp_path, mount_root, monkeypatch, command,
):
    run_id = "successful-diagnostics"
    host = WorkflowHost(tmp_path / "state")
    diagnostics = _diagnostics()
    before = copy.deepcopy(diagnostics)
    selected = []
    original_finish = host._finish_fake_agent

    def finish(request, response):
        if request.command is not command or selected:
            return original_finish(request, response)
        selected.append((request, response))
        _poll_fake_response(host, monkeypatch, request, response, diagnostics)

    monkeypatch.setattr(host, "_finish_fake_agent", finish)
    try:
        host.initialize()
        if command is AgentCommand.HYPOTHESIS_PROPOSAL:
            payload = _pilot_payload(tmp_path, mount_root, run_id)
        else:
            payload = _payload(run_id, roles=("analyst",), component_count=1)
            if command is AgentCommand.ANSWER_RENDEZVOUS:
                contract = payload["research_contract"]
                contract["roles"].append({**contract["roles"][0], "id": "reviewer"})
                contract["methodology"]["controls"]["experiment_campaigns"] = {
                    "enabled": True, "required_roles": ["preflight", "analyze"],
                    "role_owners": {"*": {"preflight": "reviewer", "analyze": "reviewer"}}}
        created = host.handle(ControlRequest(1, "create-diagnostics", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 20
        while not selected and time.monotonic() < deadline:
            host.tick()
            time.sleep(0.005)
        assert selected, command
        request, response = selected[0]
        row = host.store.get_attempt(request.attempt_id)
        assert (row["state"], row["terminal_reason"]) == ("CLOSED", "SUCCEEDED")
        observation = json.loads(row["observation_json"])
        assert observation["diagnostics"] == diagnostics
        assert observation["response"] == response.to_dict()
        assert observation["request"] == request.to_dict()
        assert "returncode" not in observation and "failure_code" not in observation
        assert diagnostics == before
        if command in {AgentCommand.DRAFT_EXPERIMENT_SPEC, AgentCommand.ANSWER_RENDEZVOUS}:
            deadline = time.monotonic() + 20
            while host.store.latest_transition(run_id)["state"] != "COMPLETED" and time.monotonic() < deadline:
                host.tick()
                time.sleep(0.005)
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
            # Deferred review/validation must neither re-close nor drop the
            # accepted Agent observation while publishing its later effects.
            assert host.store.get_attempt(request.attempt_id) == row
        report = build_debug_report(host.state_root, run_id)
        projected = next(item for item in report["attempts"] if item["attempt_id"] == request.attempt_id)
        assert projected["terminal_reason"] == "SUCCEEDED"
        assert projected["structured_output_correction"] == diagnostics["structured_output_correction"]
        assert projected["accounting"]["source"] == "response"
        assert "private candidate" not in json.dumps(projected)
        assert host.store.get_attempt(request.attempt_id) == row
        for other in host.store.list_attempts():
            if other["kind"] == "AGENT" and other["attempt_id"] != request.attempt_id:
                assert "diagnostics" not in json.loads(other["observation_json"])
        oracle = Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit(run_id)
        assert oracle.ok, oracle.to_json()
    finally:
        host.close()


def _successful_observation_issues(extra, *, invalid_candidate=False):
    """Exercise the existing Oracle envelope validator without writing rows."""
    request = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    response = FakeAgent().invoke(request)
    if invalid_candidate:
        response = with_output_digest(replace(response,
            typed_candidate={**response.typed_candidate, "extra": True}))
    attempt = {"attempt_id": request.attempt_id, "attempt_ordinal": request.attempt_ordinal,
        "run_id": request.run_id, "workflow_id": request.owner_workflow_id,
        "command_id": request.command_id, "kind": "AGENT", "state": "CLOSED",
        "terminal_reason": "SUCCEEDED", "process_group_id": None}
    command = {"command_id": request.command_id, "run_id": request.run_id,
        "workflow_id": request.owner_workflow_id, "command_type": "REQUEST_RESEARCH_NODE"}
    goal = {"fact_id": "goal", "fact_type": "GoalDefined", "run_id": request.run_id}
    contract = {"roles": [{"id": request.role}], "components": [],
        "budget": {"agent_invocation": request.budget.to_dict()}}
    observation = {"request": request.to_dict(), "response": response.to_dict(), **extra}
    issues = []
    _audit_agent_attempts("hypoloom-research-v5", str(request.schema_version), [goal],
        {"goal": {"research_contract": contract}}, [], {}, [command], [attempt],
        {request.attempt_id: observation}, issues)
    return [item.code for item in issues]


@pytest.mark.parametrize("diagnostics", [None, [], "invalid", 0, False])
def test_oracle_rejects_non_mapping_success_diagnostics(diagnostics):
    assert _successful_observation_issues({"diagnostics": diagnostics}) == [
        "successful_agent_observation_invalid"]


def test_oracle_keeps_old_success_and_rejects_other_unknown_observation_fields():
    assert _successful_observation_issues({}) == []
    assert _successful_observation_issues({"unexpected": {}}) == [
        "successful_agent_observation_invalid"]


@pytest.mark.parametrize("invalid_candidate", [False, True])
def test_success_diagnostics_do_not_change_oracle_candidate_verdict(invalid_candidate):
    expected = ["agent_postflight_invalid"] if invalid_candidate else []
    assert _successful_observation_issues({}, invalid_candidate=invalid_candidate) == expected
    for diagnostic in ({}, _diagnostics(), {"structured_output_correction": {
            "status": "unresolved"}, "accepted": True, "metrics": {"invented": 100}}):
        assert _successful_observation_issues({"diagnostics": diagnostic},
            invalid_candidate=invalid_candidate) == expected


def test_recovered_tool_observation_does_not_accept_an_invalid_host_candidate(tmp_path, monkeypatch):
    run_id = "invalid-diagnostics"
    host = WorkflowHost(tmp_path / "state")
    selected = []
    diagnostics = _diagnostics()

    def finish(request, response):
        assert request.command is AgentCommand.PROPOSE_HYPOTHESIS
        invalid = with_output_digest(replace(response,
            typed_candidate={**response.typed_candidate, "extra": True}))
        selected.append(request)
        _poll_fake_response(host, monkeypatch, request, invalid, diagnostics)

    monkeypatch.setattr(host, "_finish_fake_agent", finish)
    try:
        host.initialize()
        created = host.handle(ControlRequest(1, "create-invalid", 0, "CREATE_RUN",
            _payload(run_id, roles=("analyst",), component_count=1)))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 20
        while not selected and time.monotonic() < deadline:
            host.tick()
        assert selected
        request = selected[0]
        row = host.store.get_attempt(request.attempt_id)
        observation = json.loads(row["observation_json"])
        assert row["terminal_reason"] == "FAILED"
        assert observation["failure_stage"] == "host_candidate_validation"
        assert observation["diagnostics"]["structured_output_correction"] == diagnostics["structured_output_correction"]
        assert "rejected_response" in observation and "response" not in observation
        assert not host.store.list_facts(run_id, "HypothesisProposed")
    finally:
        host.close()
