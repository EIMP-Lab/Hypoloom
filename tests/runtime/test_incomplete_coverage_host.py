"""Profile-owned incomplete coverage through real CPU execution and closure."""
from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_loop_failures import _drive
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_profile_v4 import documents


class _CoverageAgent(FakeAgent):
    def __init__(self, choices):
        super().__init__()
        self.choices = choices
        self.analyses = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            artifacts = tuple(WorkspaceArtifact.from_bytes(item.path,
                item.content_bytes().replace(b'"accuracy": 0.55', b'"accuracy": 0.50'))
                for item in response.proposed_workspace_artifacts)
            assert artifacts != response.proposed_workspace_artifacts
            response = replace(response, proposed_workspace_artifacts=artifacts)
        elif request.command is AgentCommand.RESULT_ANALYSIS:
            self.analyses.append(request)
            choice = self.choices[min(request.attempt_ordinal - 1, len(self.choices) - 1)]
            response = replace(response, typed_candidate={**response.typed_candidate,
                "iteration_disposition": choice,
                "supporting_refs": [request.command_id + "--current-outcome"]})
        return with_output_digest(response)


@pytest.mark.parametrize(("complete", "choices", "analysis_terminals"), [
    (False, ("COMPLETE",), ["SUCCEEDED"]),
    (False, ("ITERATE", "COMPLETE"), ["FAILED", "SUCCEEDED"]),
    (False, ("ITERATE",), ["FAILED", "FAILED"]),
    (True, ("COMPLETE",), ["SUCCEEDED"]),
])
def test_profile_coverage_preserves_outcome_through_analysis_and_closure(
    tmp_path, complete, choices, analysis_terminals,
):
    run_id = "coverage-closure"
    agent = _CoverageAgent(choices)
    with tempfile.TemporaryDirectory(prefix="hypoloom-coverage-", dir="/var/tmp") as mount:
        _pilot_payload(tmp_path, Path(mount), run_id)
        # Exercise Profile admission as well as the Host. The existing JSON
        # artifact validator permits either coverage value; only inconclusive_if
        # assigns its scientific meaning. FakeAgent writes the first allowed value.
        path = tmp_path / "profile/methodology.yaml"
        methodology = yaml.safe_load(path.read_text(encoding="utf-8"))
        methodology["success_criteria"]["primary"][0]["experiment_kind"] = "coverage"
        methodology["experiments"] = {"protocols": {"coverage": {"result_kind": "metric", "requirements": {
            "required_metrics": ["accuracy"], "required_artifacts": ["metrics", "resolved_config"],
            "inconclusive_if": {"artifact": "resolved_config", "path": "coverage.full_matrix_complete", "equals": False},
            "artifact_validators": [{"type": "json_contract", "artifact": "resolved_config",
                "allowed_values": {"coverage.full_matrix_complete": [complete, not complete]}}],
        }}}}
        path.write_text(yaml.safe_dump(methodology, sort_keys=False), encoding="utf-8")
        report = admit_profile(path.parent, run_id=run_id)
        assert report.admitted, report.to_dict()
        payload = report.create_run_payload
        contract = payload["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"].update(max_hypotheses_per_round=1, max_forks=1)
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 1
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 2, "repair_attempts": 0}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
        contract["methodology"]["controls"]["product"] = settings
        contract["budget"].update(max_retries=1, max_experiments=2)
        host = WorkflowHost(tmp_path / "state", local_agent=agent)
        host.initialize()
        try:
            response = host.handle(ControlRequest(1, "create-coverage", 0, "CREATE_RUN", payload))
            assert response.status == "accepted", response
            _drive(host, run_id)
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
            outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            assert len(outcomes) == len(host.store.list_facts(run_id, "ExperimentPreregistered")) == 1
            outcome = outcomes[0]["payload"]
            assert outcome["outcome"] == ("CLEAN_REFUTATION" if complete else "INCONCLUSIVE")
            assert outcome["metric_value"] == ("0.5" if complete else None)
            if complete:
                assert {item["logical_artifact"] for item in outcome["logical_artifacts"]} == {"metrics", "resolved_config"}
            else:
                assert outcome["logical_artifacts"] == []
            assert outcome.get("validation_code") is None
            assert host.store.list_facts(run_id, "HypothesisAssessed")[0]["payload"]["assessment"] == (
                "REFUTED" if complete else "INCONCLUSIVE")
            verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
            assert len(verdicts) == 1
            assert verdicts[0]["payload"]["verdict"] == ("GOAL_NOT_MET" if complete else "INCONCLUSIVE")
            attempts = host.store.list_attempts()
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
            assert len(agent.analyses) == len(analysis_terminals)
            first = agent.analyses[0]
            analyses = [row for row in attempts if row["command_id"] == first.command_id]
            assert [row["terminal_reason"] for row in analyses] == analysis_terminals
            for row in analyses:
                if row["terminal_reason"] == "FAILED":
                    observation = json.loads(row["observation_json"])
                    assert observation["failure_code"] == "invalid_iteration_candidate"
                    assert observation["retry_class"] == "MODEL"
            for request in agent.analyses:
                inputs = {item.ref.rsplit("--current-", 1)[-1]: item for item in request.input_artifacts
                    if "--current-" in item.ref}
                assert outcomes[0] == json.loads(inputs["outcome"].content_bytes())
                evaluation = json.loads(inputs["evaluation"].content_bytes())
                assert evaluation["criterion_met"] is (False if complete else None)
                assert evaluation["validation_error"] is evaluation["execution_integrity_error"] is None
                assert all(item.ref in request.immutable_context_refs for item in inputs.values())
                assert request.command_id == first.command_id
                for original in first.input_artifacts:
                    if original.ref in first.immutable_context_refs:
                        assert original in request.input_artifacts
            assert len(host._expert_final_failures(run_id)) == (1 if analysis_terminals[-1] == "FAILED" else 0)
            assert all(row["state"] == "CLOSED" for row in attempts)
            assert not host.running and not host.running_agents and not host.local_execution_agents
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            assert audit.ok, audit.to_dict()
            assert audit.to_dict()["pending_effect_count"] == 0
        finally:
            host.close()
