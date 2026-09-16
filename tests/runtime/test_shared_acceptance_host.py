"""Common acceptance through actual Host, CPU process, publication and recovery."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, FakeAgentMode
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_deferred_measurement_host import (
    DeclaredMeasurementAgent, _candidate_bodies, deferred_payload,
)
from tests.runtime.test_host_experiment_tools import _until
from tests.runtime.test_shared_acceptance import acceptance_dict


RUN_ID = "c5-host"
REPOSITORY = Path(__file__).resolve().parents[2]
NUMERIC_PARSER = b'''import json
from pathlib import Path
raw = json.loads(Path('/raw/raw.json').read_text())
Path('/parsed/result.json').write_text(json.dumps({
    'measurements': {'alpha': raw['alpha'], 'beta': raw['beta']},
    'checks': {'causal': raw['causal']}}))
'''
ARTIFACT_PARSER = b'''import json
from pathlib import Path
valid = Path('/raw/bundle.txt').read_text() == 'bounded-package\\n'
evidence_valid = Path('/raw/validity.txt').read_text() == 'valid\\n'
Path('/parsed/result.json').write_text(json.dumps({
    'measurements': {}, 'checks': {'package_valid': valid, 'evidence_valid': evidence_valid}}))
'''


class SharedAcceptanceAgent(DeclaredMeasurementAgent):
    def __init__(self, *, scope="GOAL", local_pass=True, goal_value="0.7", goal_only=False,
                 decline_analysis=False, fail_goal_parser=False, invalid_goal_evidence=False,
                 artifact_pass=True):
        super().__init__()
        self.scope = scope
        self.local_pass = local_pass
        self.goal_value = goal_value
        self.goal_only = goal_only
        self.decline_analysis = decline_analysis
        self.fail_goal_parser = fail_goal_parser
        self.invalid_goal_evidence = invalid_goal_evidence
        self.artifact_pass = artifact_pass
        self.review_count = 0
        self.grounding_calls = 0
        self.consumed_sources = []

    @property
    def acceptance(self):
        value = acceptance_dict()
        if self.goal_only:
            value.update(criteria=[{"name": "package_valid", "operator": "IS_TRUE"}],
                validity_checks=["evidence_valid"], raw_artifacts=["bundle.txt", "validity.txt"])
        return value

    def invoke(self, request):
        response = super().invoke(request)
        candidate = json.loads(json.dumps(response.typed_candidate))
        if request.command is AgentCommand.GROUNDING:
            self.grounding_calls += 1
            candidate["acceptance"] = self.acceptance
            parser = ARTIFACT_PARSER if self.goal_only else NUMERIC_PARSER
            if self.fail_goal_parser: parser += b"raise RuntimeError('COMMON_PARSER_DID_NOT_COMPLETE')\n"
            return with_output_digest(replace(response, typed_candidate=candidate,
                proposed_workspace_artifacts=(WorkspaceArtifact.from_bytes("parse_goal.py", parser),)))
        if request.command is AgentCommand.ANSWER_RENDEZVOUS:
            self.review_count += 1
            if self.decline_analysis and self.review_count == 2:
                return with_output_digest(replace(response, typed_candidate={**candidate,
                    "disposition": "DECLINED", "summary": "Analysis rejects the submitted evidence."}))
        if request.command is AgentCommand.HYPOTHESIS_PROPOSAL and self.goal_only:
            for body in _candidate_bodies(request, candidate):
                body.pop("measurement", None)
            return with_output_digest(replace(response, typed_candidate=candidate))
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        supplied = [json.loads(item.content_bytes()) for item in request.input_artifacts
            if item.ref.endswith("--shared-acceptance")]
        assert len(supplied) == 1
        assert supplied[0]["acceptance"] == self.acceptance
        self.consumed_sources.append(supplied[0]["source_attempt_id"])
        candidate["evaluation_scope"] = self.scope
        files = {item.path: item.content_bytes() for item in response.proposed_workspace_artifacts}
        if not self.local_pass:
            files["experiment.sh"] = files["experiment.sh"].replace(b"10,8\n10,9\n10,100", b"10,10\n10,11\n10,100")
        if self.goal_only:
            candidate.update(measurement=None, metrics=[], acceptance_criteria=["Package bytes pass the common artifact parser"])
            files = {"experiment.sh": b"printf 'bounded-package\\n' > bundle.txt\n", "implementation.patch": b""}
            if not self.artifact_pass:
                files["experiment.sh"] = files["experiment.sh"].replace(b"bounded-package", b"different-package")
            validity = b"invalid" if self.invalid_goal_evidence else b"valid"
            files["experiment.sh"] += b"printf '" + validity + b"\\n' > validity.txt\n"
        else:
            raw = json.dumps({"alpha": self.goal_value, "beta": "0.8", "causal": not self.invalid_goal_evidence})
            files["experiment.sh"] += ("printf '%s' '" + raw + "' > raw.json\n").encode()
        return with_output_digest(replace(response, typed_candidate=candidate,
            proposed_workspace_artifacts=tuple(WorkspaceArtifact.from_bytes(path, data)
                for path, data in sorted(files.items()))))


def shared_payload(tmp_path, target):
    payload = deferred_payload(tmp_path, target)
    payload["research_contract"]["schema_version"] = 3
    payload["research_contract"]["goal"]["evaluation"] = {"mode": "grounding"}
    return payload


@pytest.fixture
def shared_host(tmp_path, request):
    with tempfile.TemporaryDirectory(prefix="hypoloom-common-target-", dir="/var/tmp") as target:
        options = dict(getattr(request, "param", {}))
        product_shared_goal = options.pop("product_shared_goal", False)
        agent = SharedAcceptanceAgent(**options)
        host = WorkflowHost(tmp_path / "state", local_agent=agent)
        host.initialize()
        try:
            payload = shared_payload(tmp_path, Path(target))
            if product_shared_goal:
                from tests.runtime.test_profile_v4 import documents

                product = project_profile_v4(**documents()).settings
                product["goal"].update(evaluation="grounding",
                    stop_conditions={"max_rounds_per_expert": 1})
                product["experts"]["defaults"].update(max_forks=1, max_hypotheses_per_round=1)
                product["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 0}
                product["methodology"]["graph_evolution"]["enabled"] = False
                product["notify"] = {"sinks": {}, "events": {}}
                payload["research_contract"]["methodology"]["controls"]["product"] = product
            if agent.decline_analysis:
                contract = payload["research_contract"]
                contract["roles"].append({**contract["roles"][0], "id": "reviewer"})
                contract["methodology"]["controls"]["experiment_campaigns"] = {
                    "enabled": True, "required_roles": ["preflight", "analyze"],
                    "role_owners": {"*": {"preflight": "reviewer", "analyze": "reviewer"}}}
            response = host.handle(ControlRequest(1, "create-common", 0, "CREATE_RUN",
                payload))
            assert response.status == "accepted", response
            yield host, agent
        finally:
            host.close()


def completed(host):
    _until(host, lambda: host.store.latest_transition(RUN_ID)["state"] == "COMPLETED")
    outcomes = host.store.list_facts(RUN_ID, "CanonicalOutcomeAccepted")
    specs = host.store.list_facts(RUN_ID, "ExperimentPreregistered")
    assessments = host.store.list_facts(RUN_ID, "HypothesisAssessed")
    verdicts = host.store.list_facts(RUN_ID, "GoalVerdictRecorded")
    assert len(outcomes) == len(specs) == len(assessments) == len(verdicts) == 1
    processes = [row for row in host.store.list_attempts() if row["run_id"] == RUN_ID and row["kind"] == "PROCESS"]
    assert len(processes) == 1
    assert processes[0]["terminal_reason"] == "SUCCEEDED"
    source = host.shared_acceptance.source(RUN_ID, required=True)
    assert specs[0]["payload"]["goal_evaluation"]["source_attempt_id"] == source[0]
    assert specs[0]["payload"]["goal_evaluation"]["acceptance"] == source[1].to_dict()
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts() if row["run_id"] == RUN_ID)
    return tuple(rows[0]["payload"] for rows in (outcomes, specs, assessments, verdicts))


def assert_oracle(host):
    report = Oracle(host.store.database_path, host.publisher.root).audit(RUN_ID)
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("shared_host,expected_closing,expected_assessment", [
    ({"product_shared_goal": True, "scope": "LOCAL"}, False, "SUPPORTED"),
    ({"product_shared_goal": True, "goal_value": "0.6674"}, False, "SUPPORTED"),
    ({"product_shared_goal": True, "invalid_goal_evidence": True}, False, "SUPPORTED"),
    ({"product_shared_goal": True, "local_pass": False}, True, "REFUTED"),
], indirect=["shared_host"], ids=["local-only", "equal-threshold", "invalid-evidence", "common-pass-local-refutation"])
def test_common_goal_controls_interaction_closing_after_real_cpu(
        shared_host, expected_closing, expected_assessment):
    host, _ = shared_host
    _, _, assessment, verdict = completed(host)
    assert assessment["assessment"] == expected_assessment
    assert (verdict["verdict"] == "GOAL_MET") is expected_closing
    assert host.interaction._enabled(RUN_ID)
    assert host.interaction._closing(RUN_ID) is expected_closing
    if not expected_closing:
        assert any(json.loads(row["observation_json"]).get("request", {}).get("command") == "OBSERVER"
            for row in host.store.list_attempts() if row["kind"] == "AGENT")
    assert_oracle(host)


def test_exhausted_grounding_closes_inconclusively_without_an_evaluator_or_experiment(shared_host):
    host, agent = shared_host
    original = agent.invoke
    failure = FakeAgent(FakeAgentMode.NONZERO_EXIT)
    agent.invoke = lambda request: (failure.invoke(request)
        if request.command is AgentCommand.GROUNDING else original(request))
    for _ in range(40):
        host.tick()
        if host.store.latest_transition(RUN_ID)["state"] in {"COMPLETED", "FAILED"}:
            break
    assert host.store.latest_transition(RUN_ID)["state"] == "COMPLETED"
    verdicts = host.store.list_facts(RUN_ID, "GoalVerdictRecorded")
    assert len(verdicts) == 1 and verdicts[0]["payload"]["verdict"] == "INCONCLUSIVE"
    assert host.shared_acceptance.source(RUN_ID) is None
    for kind in ("HypothesisProposed", "ExperimentPreregistered", "CanonicalOutcomeAccepted", "HypothesisAssessed"):
        assert not host.store.list_facts(RUN_ID, kind)
    assert all(row["state"] == "CLOSED" and row["kind"] == "AGENT" for row in host.store.list_attempts())
    assert_oracle(host)


@pytest.mark.parametrize("shared_host", [{"scope": "LOCAL"}], indirect=True)
def test_local_support_never_substitutes_for_whole_goal(shared_host):
    host, agent = shared_host
    outcome, spec, assessment, verdict = completed(host)
    assert outcome["outcome"] == "SUCCESS" and assessment["assessment"] == "SUPPORTED"
    assert spec["goal_evaluation"]["scope"] == "LOCAL"
    assert outcome.get("goal_evaluation") is None
    assert verdict["verdict"] == "INCONCLUSIVE"
    assert agent.grounding_calls == 1 and len(agent.consumed_sources) == 1
    assert_oracle(host)


@pytest.mark.parametrize("shared_host", [{"goal_value": "0.6674"}], indirect=True)
def test_local_support_with_equal_strict_goal_threshold_does_not_stop_as_success(shared_host):
    host, _ = shared_host
    outcome, _, assessment, verdict = completed(host)
    assert assessment["assessment"] == "SUPPORTED"
    assert outcome["goal_evaluation"]["measurements"] == {"alpha": "0.6674", "beta": "0.8"}
    assert verdict["verdict"] == "GOAL_NOT_MET"
    assert_oracle(host)


@pytest.mark.parametrize("shared_host", [{"local_pass": False}], indirect=True)
def test_common_pass_can_stop_despite_local_refutation(shared_host):
    host, _ = shared_host
    outcome, _, assessment, verdict = completed(host)
    assert outcome["outcome"] == "CLEAN_REFUTATION" and outcome["metric_value"] == "0.1"
    assert assessment["assessment"] == "REFUTED"
    assert outcome["goal_evaluation"]["measurements"] == {"alpha": "0.7", "beta": "0.8"}
    assert verdict["verdict"] == "GOAL_MET"
    assert_oracle(host)


@pytest.mark.parametrize("shared_host", [{"decline_analysis": True}], indirect=True)
def test_analysis_rejection_cannot_be_overridden_by_passing_common_parser(shared_host):
    host, agent = shared_host
    _until(host, lambda: bool(host.store.list_facts(RUN_ID, "CanonicalOutcomeAccepted")))
    outcome = host.store.list_facts(RUN_ID, "CanonicalOutcomeAccepted")[0]["payload"]
    assert agent.review_count == 2
    assert outcome["outcome"] == "INTEGRITY_REJECTED"
    assert outcome.get("goal_evaluation") is None
    assert not any(row["payload"]["verdict"] == "GOAL_MET"
        for row in host.store.list_facts(RUN_ID, "GoalVerdictRecorded"))
    assert_oracle(host)


@pytest.mark.parametrize("shared_host", [{"fail_goal_parser": True}], indirect=True)
def test_failed_common_parser_keeps_local_result_and_actionable_diagnostic(shared_host):
    host, _ = shared_host
    _until(host, lambda: host.store.latest_transition(RUN_ID)["state"] == "COMPLETED")
    outcome = host.store.list_facts(RUN_ID, "CanonicalOutcomeAccepted")[0]["payload"]
    assert outcome["outcome"] == "SUCCESS" and outcome.get("goal_evaluation") is None
    assert host.store.list_facts(RUN_ID, "GoalVerdictRecorded")[0]["payload"]["verdict"] == "INCONCLUSIVE"
    terminal = host.store.latest_transition(host.store.list_facts(RUN_ID, "ExperimentPreregistered")[0]["experiment_id"])
    evaluation = json.loads(terminal["payload_json"])["outcome"]
    assert "goal parser did not complete" in evaluation["parser_validation_error"]
    assert_oracle(host)


def test_raw_process_failure_is_not_reported_as_a_common_parser_format_error(shared_host):
    host, agent = shared_host
    original = agent.invoke

    def fail_raw(request):
        response = original(request)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            response = with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(item.path, b"exit 17\n" + item.content_bytes())
                if item.path == "experiment.sh" else item for item in response.proposed_workspace_artifacts)))
        return response

    agent.invoke = fail_raw
    _until(host, lambda: host.store.latest_transition(RUN_ID)["state"] == "COMPLETED")
    outcome = host.store.list_facts(RUN_ID, "CanonicalOutcomeAccepted")[0]
    assert outcome["payload"]["outcome"] == "EXECUTION_FAILURE"
    assert outcome["payload"]["validation_code"] == "raw_execution_failed"
    assert outcome["payload"].get("goal_evaluation") is None
    terminal = host.store.latest_transition(outcome["experiment_id"])
    evaluation = json.loads(terminal["payload_json"])["outcome"]
    assert "goal parser not run because raw execution" in evaluation["parser_validation_error"]
    assert "invalid_parsed_measurement" not in evaluation["parser_validation_error"]
    assert_oracle(host)


@pytest.mark.parametrize("observation", [
    {"terminal_reason": "TIMED_OUT", "returncode": -9},
    {"terminal_reason": "CANCELLED", "returncode": None},
    {"terminal_reason": "FAILED", "returncode": 1},
    {"terminal_reason": "FAILED", "returncode": 70, "synthetic_before_spawn": True},
])
def test_unfinished_outer_process_does_not_claim_the_parser_never_ran(tmp_path, observation):
    from types import SimpleNamespace
    from hypoloom.shared_acceptance_host import SharedAcceptanceHost
    prepared = SimpleNamespace(local_parser=None, goal_parser=object())
    _, _, error = SharedAcceptanceHost(None).evaluate(
        {"payload": {"goal_evaluation": {"scope": "GOAL"}}}, tmp_path, observation, prepared)
    assert "phase completion is unconfirmed" in error
    assert "parser not run" not in error


@pytest.mark.parametrize("shared_host", [{"goal_only": True}], indirect=True)
def test_artifact_goal_only_needs_no_fabricated_local_measurement(shared_host):
    host, _ = shared_host
    outcome, spec, _, verdict = completed(host)
    hypothesis = host.store.list_facts(RUN_ID, "HypothesisProposed")[0]["payload"]
    assert hypothesis.get("measurement") is None and spec.get("measurement") is None
    assert spec["result_kind"] == "artifact" and spec["metric"] is None
    assert outcome["metric_value"] is None
    assert outcome["goal_evaluation"]["measurements"] == {}
    assert outcome["goal_evaluation"]["checks"] == {"package_valid": True, "evidence_valid": True}
    assert verdict["verdict"] == "GOAL_MET"
    assert_oracle(host)


@pytest.mark.parametrize("shared_host", [{"goal_only": True, "invalid_goal_evidence": True}], indirect=True)
def test_invalid_artifact_evidence_is_not_a_clean_scientific_refutation(shared_host):
    host, _ = shared_host
    outcome, spec, assessment, verdict = completed(host)
    assert spec.get("measurement") is None
    assert outcome["goal_evaluation"]["checks"] == {"package_valid": True, "evidence_valid": False}
    assert outcome["outcome"] == "EXECUTION_FAILURE"
    assert outcome["metric_value"] is None
    assert assessment["assessment"] == "INCONCLUSIVE"
    assert verdict["verdict"] == "INCONCLUSIVE"
    terminal = host.store.latest_transition(host.store.list_facts(RUN_ID, "ExperimentPreregistered")[0]["experiment_id"])
    candidate = json.loads(terminal["payload_json"])["outcome"]
    assert candidate["criterion_met"] is None
    assert "validity" in candidate["validation_error"]
    assert "validity" in candidate["parser_validation_error"]
    assert_oracle(host)


@pytest.mark.parametrize("shared_host", [{"goal_only": True, "artifact_pass": False}], indirect=True)
def test_valid_artifact_failure_is_still_a_clean_scientific_refutation(shared_host):
    host, _ = shared_host
    outcome, _, assessment, verdict = completed(host)
    assert outcome["goal_evaluation"]["checks"] == {"package_valid": False, "evidence_valid": True}
    assert outcome["outcome"] == "CLEAN_REFUTATION"
    assert assessment["assessment"] == "REFUTED"
    assert verdict["verdict"] == "GOAL_NOT_MET"
    assert_oracle(host)


@pytest.mark.parametrize("shared_host", [{"invalid_goal_evidence": True}], indirect=True)
def test_invalid_common_evidence_does_not_erase_successful_local_measurement(shared_host):
    host, _ = shared_host
    outcome, _, assessment, verdict = completed(host)
    assert outcome["goal_evaluation"]["checks"] == {"causal": False}
    assert outcome["outcome"] == "SUCCESS"
    assert assessment["assessment"] == "SUPPORTED"
    assert verdict["verdict"] == "INCONCLUSIVE"
    assert_oracle(host)


def test_shared_publication_crash_recovers_same_source_without_reexecuting(tmp_path):
    with tempfile.TemporaryDirectory(prefix="hypoloom-common-recovery-", dir="/var/tmp") as target:
        state_root = tmp_path / "state"
        host = WorkflowHost(state_root, local_agent=SharedAcceptanceAgent(local_pass=False))
        host.initialize()
        try:
            response = host.handle(ControlRequest(1, "create-recovery", 0, "CREATE_RUN",
                shared_payload(tmp_path, Path(target))))
            assert response.status == "accepted", response
        finally:
            host.close()
        script = '''from pathlib import Path
import sys
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_shared_acceptance_host import SharedAcceptanceAgent, RUN_ID
from tests.runtime.test_host_experiment_tools import _until
host = WorkflowHost(Path(sys.argv[1]), local_agent=SharedAcceptanceAgent(local_pass=False),
    fault_point='after_artifact_publish_before_digest_commit')
host.initialize()
_until(host, lambda: host.store.latest_transition(RUN_ID)['state'] == 'COMPLETED')
raise AssertionError('the real publication crash hook did not fire')
'''
        crashed = subprocess.run([sys.executable, "-c", script, str(state_root)], cwd=REPOSITORY,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=35)
        assert crashed.returncode == 86, (crashed.stdout, crashed.stderr)
        resumed_agent = SharedAcceptanceAgent(local_pass=False)
        recovered = WorkflowHost(state_root, local_agent=resumed_agent)
        try:
            recovered.initialize()
            source = recovered.shared_acceptance.source(RUN_ID, required=True)[0]
            assert not recovered.store.list_facts(RUN_ID, "CanonicalOutcomeAccepted")
            outcome, spec, _, verdict = completed(recovered)
            assert source == spec["goal_evaluation"]["source_attempt_id"] == outcome["goal_evaluation"]["source_attempt_id"]
            assert verdict["verdict"] == "GOAL_MET"
            assert resumed_agent.grounding_calls == 0 and resumed_agent.consumed_sources == []
            assert len(recovered.store.list_facts(RUN_ID, "EvidenceLinked")) == 1
            assert_oracle(recovered)
        finally:
            recovered.close()
