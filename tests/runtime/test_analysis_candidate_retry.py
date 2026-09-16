"""Analysis choice corrections retain Host input authority and retry budgets."""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import (
    AgentACIError, EmbeddedInput, IterationDisposition, WorkspaceArtifact,
    canonical_json, validate_postflight, with_output_digest,
)
from hypoloom.agent_context import (
    RETRYABLE_AGENT_FAILURES, RETRYABLE_MODEL_FAILURES, agent_retry_class,
    retryable_agent_validation_details,
)
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_agent_aci14 import _analysis_request, _response
from tests.runtime.test_expert_loop_failures import _drive
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_profile_v4 import documents


def _candidate(request, *, cite=True, disposition="ITERATE"):
    return {
        "task_kind": "RESULT_ANALYSIS",
        "summary": "Analyze the accepted current outcome without changing it.",
        "supporting_refs": [request.command_id + "--current-outcome"] if cite else [],
        "iteration_disposition": disposition,
    }


def _rejected(request, candidate):
    with pytest.raises(AgentACIError) as caught:
        validate_postflight(request, _response(request, candidate))
    return caught.value


@pytest.mark.parametrize("version", [14, 17])
@pytest.mark.parametrize("outcome", ["EXECUTION_FAILURE", "INTEGRITY_REJECTED", "INCONCLUSIVE"])
def test_operational_iterate_choice_is_correctable_without_rewriting_outcome(version, outcome):
    request = replace(_analysis_request(outcome), schema_version=version)
    original = request.to_dict()
    error = _rejected(request, _candidate(request))
    assert error.code == "invalid_iteration_candidate"
    assert "not operational repair" in error.message
    assert error.code in RETRYABLE_MODEL_FAILURES
    assert agent_retry_class(error.code, {}, retryable=True) == "MODEL"
    assert retryable_agent_validation_details(error) == {
        "diagnostics": {"validation_error": {"code": error.code, "message": error.message}},
    }
    corrected = validate_postflight(
        request, _response(request, _candidate(request, disposition="COMPLETE")),
    )
    assert corrected.candidate.iteration_disposition is IterationDisposition.COMPLETE
    assert not corrected.workspace_files
    assert request.to_dict() == original
    assert json.loads(request.input_artifacts[1].content_bytes())["payload"]["outcome"] == outcome


@pytest.mark.parametrize("version", [14, 17])
@pytest.mark.parametrize("outcome", ["SUCCESS", "CLEAN_REFUTATION"])
def test_missing_current_outcome_citation_is_correctable_only_with_existing_ref(version, outcome):
    request = replace(_analysis_request(outcome), schema_version=version)
    error = _rejected(request, _candidate(request, cite=False))
    assert error.code == "invalid_iteration_candidate"
    assert "cite its current accepted outcome" in error.message
    assert agent_retry_class(error.code, {}, retryable=True) == "MODEL"
    corrected = validate_postflight(request, _response(request, _candidate(request)))
    assert corrected.candidate.iteration_disposition is IterationDisposition.ITERATE
    assert corrected.candidate.supporting_refs == (request.command_id + "--current-outcome",)
    foreign = {**_candidate(request), "supporting_refs": ["foreign-outcome"]}
    assert _rejected(request, foreign).code == "invalid_candidate"


@pytest.mark.parametrize("tamper", [
    "missing_spec", "missing_outcome", "unbound_spec", "wrong_media_type",
    "malformed_json", "nonobject_fact", "nonobject_payload", "missing_payload",
    "wrong_fact_id", "wrong_fact_type", "wrong_row_type", "foreign_run",
    "foreign_component", "foreign_spec_hypothesis", "wrong_experiment",
    "wrong_execution", "wrong_method_family", "wrong_result_kind",
])
def test_invalid_host_analysis_inputs_remain_nonretryable(tamper):
    request = replace(_analysis_request(), schema_version=17)
    spec, outcome = request.input_artifacts
    if tamper.startswith("missing_") and tamper != "missing_payload":
        omitted = spec if tamper == "missing_spec" else outcome
        request = replace(request, input_artifacts=tuple(item for item in request.input_artifacts if item is not omitted))
    elif tamper == "unbound_spec":
        request = replace(request, immutable_context_refs=(outcome.ref,))
    else:
        item = spec if tamper == "foreign_spec_hypothesis" else outcome
        row = json.loads(item.content_bytes())
        if tamper == "malformed_json":
            content = b"{broken"
        elif tamper == "nonobject_fact":
            content = b"[]"
        else:
            if tamper == "nonobject_payload":
                row["payload"] = []
            elif tamper == "missing_payload":
                row.pop("payload")
            elif tamper == "wrong_fact_id":
                row["fact_id"] = "foreign"
            elif tamper == "wrong_row_type":
                row["fact_type"] = "EvidenceLinked"
            elif tamper == "wrong_fact_type":
                row["payload"]["fact_type"] = "EvidenceLinked"
            elif tamper == "foreign_spec_hypothesis":
                row["payload"]["hypothesis_id"] = "foreign"
            elif tamper in {"foreign_run", "foreign_component"}:
                row["payload"]["run_id" if tamper == "foreign_run" else "component_id"] = "foreign"
                if tamper == "foreign_run":
                    row["run_id"] = "foreign"
            elif tamper != "wrong_media_type":
                field = {"wrong_experiment": "experiment_id", "wrong_execution": "execution_id",
                         "wrong_method_family": "method_family_id", "wrong_result_kind": "result_kind"}[tamper]
                row["payload"][field] = "foreign"
                if field in row:
                    row[field] = "foreign"
            content = canonical_json(row)
        changed = EmbeddedInput.from_bytes(
            item.ref, "text/plain" if tamper == "wrong_media_type" else item.media_type, content,
        )
        request = replace(request, input_artifacts=tuple(changed if old is item else old for old in request.input_artifacts))
    # Host input failures take precedence even when the candidate also omits its citation.
    error = _rejected(request, _candidate(request, cite=False))
    assert error.code == "invalid_iteration_input"
    assert error.code not in RETRYABLE_AGENT_FAILURES
    assert agent_retry_class(error.code, {}, retryable=False) is None
    assert retryable_agent_validation_details(error) is None


def test_duplicate_host_analysis_fact_is_rejected_before_postflight():
    request = _analysis_request()
    with pytest.raises(AgentACIError) as caught:
        replace(request, input_artifacts=(*request.input_artifacts, request.input_artifacts[1]))
    assert caught.value.code == "duplicate_input_artifact"
    assert caught.value.code not in RETRYABLE_AGENT_FAILURES


def test_retained_aci13_analysis_has_no_iteration_choice():
    request = replace(_analysis_request("EXECUTION_FAILURE"), schema_version=13)
    candidate = _candidate(request)
    candidate.pop("iteration_disposition")
    accepted = validate_postflight(request, _response(request, candidate))
    assert accepted.candidate.iteration_disposition is None
    assert _rejected(request, {**candidate, "iteration_disposition": "COMPLETE"}).code == "invalid_schema"


class _OperationalAnalysisAgent(FakeAgent):
    def __init__(self, correct):
        super().__init__()
        self.correct = correct
        self.analyses = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            response = replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(item.path, item.content_bytes() + (
                    b'\nraise RuntimeError("analysis retry operational failure")\n'
                    if item.path == "experiment.py" else b""))
                for item in response.proposed_workspace_artifacts))
        elif request.command is AgentCommand.RESULT_ANALYSIS:
            self.analyses.append(request)
            disposition = "COMPLETE" if self.correct and request.attempt_ordinal > 1 else "ITERATE"
            response = replace(response, typed_candidate=_candidate(request, disposition=disposition))
        return with_output_digest(response)


@pytest.mark.parametrize(("correct", "max_retries"), [(True, 1), (False, 0), (False, 2)])
def test_host_analysis_correction_or_exhaustion_preserves_failed_science(tmp_path, correct, max_retries):
    run_id = "analysis-choice-retry"
    agent = _OperationalAnalysisAgent(correct)
    with tempfile.TemporaryDirectory(prefix="prom-analysis-retry-", dir="/var/tmp") as mount:
        payload = _pilot_payload(tmp_path, Path(mount), run_id)
        contract = payload["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"].update(max_hypotheses_per_round=1, max_forks=1)
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 1
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 0}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
        contract["methodology"]["controls"]["product"] = settings
        contract["budget"].update(max_retries=max_retries, max_experiments=1)
        contract["roles"][0]["budget"]["max_experiments"] = 1
        host = WorkflowHost(tmp_path / "state", local_agent=agent)
        host.initialize()
        try:
            assert host.handle(ControlRequest(1, "create-analysis-retry", 0, "CREATE_RUN", payload)).status == "accepted"
            _drive(host, run_id)
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
            assert len(agent.analyses) == max_retries + 1
            first = agent.analyses[0]
            attempts = host.store.list_attempts()
            analyses = [row for row in attempts if row["command_id"] == first.command_id]
            assert [row["terminal_reason"] for row in analyses] == (
                ["FAILED", "SUCCEEDED"] if correct else ["FAILED"] * (max_retries + 1))
            for row in analyses[:1] if correct else analyses:
                observation = json.loads(row["observation_json"])
                assert observation["failure_code"] == "invalid_iteration_candidate"
                assert observation["retry_class"] == "MODEL"
                assert observation["diagnostics"]["validation_error"]["code"] == "invalid_iteration_candidate"
            for prior, retry in zip(agent.analyses, agent.analyses[1:]):
                assert retry.command_id == first.command_id and retry.task_context == first.task_context
                assert retry.attempt_ordinal == prior.attempt_ordinal + 1
                assert retry.attempt_id != prior.attempt_id
                assert retry.immutable_context_refs == first.immutable_context_refs
                for name in ("spec", "outcome"):
                    ref = first.command_id + "--current-" + name
                    assert next(item for item in retry.input_artifacts if item.ref == ref) == next(item for item in first.input_artifacts if item.ref == ref)
                diagnostic = next(item for item in retry.input_artifacts if item.ref.endswith("--agent-retry-diagnostic"))
                correction = json.loads(diagnostic.content_bytes())
                assert correction["source_attempt_id"] == prior.attempt_id
                assert correction["failure_code"] == "invalid_iteration_candidate"
                assert diagnostic.ref not in retry.immutable_context_refs
            outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 1 and outcomes[0]["payload"]["outcome"] == "EXECUTION_FAILURE"
            original_outcome = json.loads(next(item.content_bytes() for item in first.input_artifacts if item.ref.endswith("--current-outcome")))
            assert outcomes[0] == original_outcome
            assert len(host.store.list_facts(run_id, "ExperimentPreregistered")) == 1
            assessments = host.store.list_facts(run_id, "HypothesisAssessed")
            assert len(assessments) == 1 and assessments[0]["payload"]["assessment"] == "INCONCLUSIVE"
            assert host.store.list_facts(run_id, "GoalVerdictRecorded")[0]["payload"]["verdict"] == "INCONCLUSIVE"
            failures = [item for item in host._expert_final_failures(run_id) if item["failed_task_command_id"] == first.command_id]
            assert len(failures) == (0 if correct else 1)
            if failures:
                assert failures[0]["attempt_id"] == analyses[-1]["attempt_id"]
            assert all(row["state"] == "CLOSED" for row in attempts)
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            assert audit.ok, audit.to_dict()
            assert not host.running and not host.running_agents
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
        finally:
            host.close()
