"""Offline long-experiment controller retry and downstream-progress probes.

Explicit adapter timeout observations stand in for elapsed Agent deadlines.
The independently owned CPU Process and durable timestamps remain real; this
is not a literal 30-hour experiment or a native Claude Code qualification.
"""

import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, AttemptTerminalReason
from hypoloom.expert_loop import ResearchNodeKind, ResearchNodeTask
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_host_experiment_tools import _until
from tests.runtime.test_profile_v4 import documents


class _IterateOnce(FakeAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if (request.command is AgentCommand.RESULT_ANALYSIS
                and request.task_context.key.experiment_ordinal == 1):
            response = with_output_digest(replace(response, typed_candidate={
                **response.typed_candidate, "iteration_disposition": "ITERATE"}))
        return response


@pytest.fixture
def long_execution(tmp_path, request):
    with tempfile.TemporaryDirectory(prefix="hypoloom-controller-timeout-", dir="/var/tmp") as directory:
        payload = _pilot_payload(tmp_path, Path(directory), "controller-timeout")
        contract = payload["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 1
        settings["methodology"]["inner_loop"] = {
            "max_iterations_per_hypothesis": 2, "repair_attempts": 1}
        settings["methodology"]["interaction"] = {
            "leader": {"enabled": False}, "debate": {"enabled": False}}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        contract["methodology"]["controls"]["product"] = settings
        retries = getattr(request, "param", 2)
        contract["budget"].update(max_infrastructure_retries=retries, max_experiments=3,
                                  wallclock_hours=1680, run_tokens=1_000_000,
                                  per_expert_tokens=1_000_000, experiment_timeout=108000)
        contract["budget"]["agent_invocation"]["wall_seconds"] = 3000
        contract["roles"][0]["budget"]["max_experiments"] = 3
        host = WorkflowHost(tmp_path / "state", local_agent=_IterateOnce())
        host.initialize()
        try:
            result = host.handle(ControlRequest(schema_version=1, request_id="create-timeout",
                expected_revision=0, command="CREATE_RUN", payload=payload))
            assert result.status == "accepted", result
            _until(host, lambda: bool(host.local_execution_agents))
            request = next(iter(host.local_execution_agents.values()))
            host.local_execution_agents.clear()
            design = ResearchNodeTask(key=replace(request.task_context.key,
                node_kind=ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT))
            source = host._expert_success(design)
            packet = json.loads(source["observation_json"])["response"]
            submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", {
                "candidate": packet["typed_candidate"],
                "workspace_files": packet["proposed_workspace_artifacts"]})
            _until(host, lambda: bool(host.running))
            yield host, request, submitted["exp_id"], retries
        finally:
            host.close()


def _timeout(host, request, *, code="timed_out", retryable=True):
    reason = (AttemptTerminalReason.FAILED if code == "session_archive_failed"
              else AttemptTerminalReason.TIMED_OUT)
    host._agent_failure(request, reason, code,
                        retryable=retryable)


def test_timed_out_controller_waits_for_its_owned_experiment_before_retry(long_execution):
    host, request, experiment_id, _ = long_execution
    _timeout(host, request)
    host._resume_tool_execution_agents(request.run_id)
    assert host.store.latest_transition(experiment_id)["state"] == "EXECUTING"
    assert not host.local_execution_agents
    assert len(host._expert_attempts(request.task_context)) == 1
    _until(host, lambda: bool(host.local_execution_agents))
    assert host.store.latest_transition(experiment_id)["state"] == "TERMINAL"
    assert len(host.local_execution_agents) == 1
    resumed = next(iter(host.local_execution_agents.values()))
    assert resumed.command_id == request.command_id
    assert resumed.input_artifacts == request.input_artifacts
    assert resumed.immutable_context_refs == request.immutable_context_refs
    assert len(host.store.list_workflow_heads(request.run_id, "EXPERIMENT")) == 1


def test_rejected_early_completion_waits_then_retries_without_duplicate_submission(long_execution):
    host, request, experiment_id, _ = long_execution
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    failed = host.store.get_attempt(request.attempt_id)
    observation = json.loads(failed["observation_json"])
    assert failed["terminal_reason"] == "FAILED" and observation["retry_class"] == "MODEL"
    assert observation["failure_code"] == "invalid_candidate"
    for _ in range(3):
        host._resume_tool_execution_agents(request.run_id)
        assert not host.local_execution_agents
        assert len(host._expert_attempts(request.task_context)) == 1
    _until(host, lambda: bool(host.local_execution_agents))
    assert host.store.latest_transition(experiment_id)["state"] == "TERMINAL"
    resumed = next(iter(host.local_execution_agents.values()))
    assert resumed.command_id == request.command_id
    assert len(host.store.list_workflow_heads(request.run_id, "EXPERIMENT")) == 1
    view = host.handle_agent_tool(resumed.attempt_id, "list_my_experiments", {})
    assert [item["exp_id"] for item in view["experiments"]] == [experiment_id]
    assert view["experiments"][0]["outcome"]["outcome"] == "SUCCESS"
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    assert host._expert_success(request.task_context)["attempt_id"] == resumed.attempt_id
    assert len(host.store.list_facts(request.run_id, "CanonicalOutcomeAccepted")) == 2
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()
    assert report.open_attempt_count == report.pending_effect_count == 0


@pytest.mark.parametrize("long_execution", [2, 9], indirect=True)
def test_full_controller_timeout_allowance_preserves_analysis_and_next_iteration(long_execution):
    host, request, experiment_id, retries = long_execution
    original = request
    assert request.budget.wall_seconds == 3000
    assert host._research_contract(request.run_id).document["budget"]["experiment_timeout"] == 108000
    for index in range(retries + 1):
        _timeout(host, request)
        host._resume_tool_execution_agents(request.run_id)
        if index == 0:
            # The existing Runner/collection path completes owned work before
            # the controller consumes another infrastructure retry allowance.
            assert not host.local_execution_agents
            assert len(host._expert_attempts(original.task_context)) == 1
            assert host.store.latest_transition(experiment_id)["state"] == "EXECUTING"
            _until(host, lambda: bool(host.local_execution_agents))
            assert host.store.latest_transition(experiment_id)["state"] == "TERMINAL"
        if index < retries:
            assert len(host.local_execution_agents) == 1
            request = next(iter(host.local_execution_agents.values()))
            host.local_execution_agents.clear()
            assert request.command_id == original.command_id
    assert not host.local_execution_agents
    assert host._expert_task_exhausted(original.run_id, original.task_context)
    attempts = host._expert_attempts(original.task_context)
    assert len(attempts) == retries + 1
    assert {row["terminal_reason"] for row in attempts} == {"TIMED_OUT"}
    assert {json.loads(row["observation_json"])["retry_class"] for row in attempts} == {"INFRASTRUCTURE"}
    assert host.store.latest_transition(experiment_id)["state"] == "TERMINAL"
    failures = host._expert_final_failures(original.run_id)
    assert [item["failure_code"] for item in failures] == ["timed_out"]
    assert not host._run_wallclock_expired(original.run_id)
    _until(host, lambda: host.store.latest_transition(original.run_id)["state"] == "COMPLETED")
    outcomes = host.store.list_facts(original.run_id, "CanonicalOutcomeAccepted")
    assert len(outcomes) == 2
    assert {item["payload"]["outcome"] for item in outcomes} == {"SUCCESS"}
    analyses = [row for row in host.store.list_attempts() if row["kind"] == "AGENT"
        and json.loads(row["observation_json"])["request"]["command"] == "RESULT_ANALYSIS"]
    assert len(analyses) == 2
    assert {row["terminal_reason"] for row in analyses} == {"SUCCEEDED"}
    assert len(host._expert_attempts(original.task_context)) == retries + 1
    assert not host.running and not host.local_execution_agents
    report = Oracle(host.store.database_path, host.publisher.root).audit(original.run_id)
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("code", ["profile_wallclock_exhausted", "session_archive_failed"])
def test_nonretryable_controller_failure_does_not_reopen_execution(long_execution, code):
    host, request, _, _ = long_execution
    _timeout(host, request, code=code, retryable=False)
    host._resume_tool_execution_agents(request.run_id)
    assert not host.local_execution_agents
    assert len(host._expert_attempts(request.task_context)) == 1
    assert host._expert_task_exhausted(request.run_id, request.task_context)
    assert json.loads(host._expert_attempts(request.task_context)[0]["observation_json"])["retry_class"] is None
