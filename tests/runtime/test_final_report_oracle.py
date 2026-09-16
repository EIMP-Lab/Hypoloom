import base64
import copy
import json

import pytest

from hypoloom.fake_agent import FakeAgent
from test_final_report import report_request


def records():
    request = report_request(); response = FakeAgent().invoke(request)
    context = json.loads(request.input_artifacts[0].content_bytes())
    payloads = {row["fact_id"]: json.loads(row["payload_json"]) for row in context["facts"]}
    attempts = [{"attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED"}]
    observations = {request.attempt_id: {"request": request.to_dict(), "response": response.to_dict()}}
    transitions = [{"workflow_id": request.run_id, "workflow_kind": "RUN", "state": "COMPLETED"}]
    return request, attempts, observations, context["facts"], payloads, transitions


def audit(tmp_path, args):
    from hypoloom.final_report_oracle import audit_final_reports
    request, attempts, observations, facts, payloads, transitions = args
    return audit_final_reports(tmp_path, request.run_id, attempts, observations, facts, payloads, transitions)


def stopped_records(*, failed_report=False):
    from dataclasses import replace
    from hypoloom.agent_aci import AgentRequest, EmbeddedInput
    args = records(); request, attempts, observations, facts, payloads, transitions = args
    facts[:] = [row for row in facts if row["fact_type"] != "GoalVerdictRecorded"]
    payloads.pop("verdict-fact")
    control = {"command_id": "cancel-command", "revision": 4, "command_type": "CANCEL", "payload": {"cause": "operator stopped", "stop_disposition": "CANCELLED"}}
    context = {"facts": facts, "stop_controls": [control], "stop_reason": "As of this request: cancel-command, operator stopped."}
    embedded = EmbeddedInput.from_bytes("final--final-report-context", "application/json", json.dumps(context).encode())
    request = replace(request, input_artifacts=(embedded,), immutable_context_refs=(embedded.ref,))
    attempts[0].update(run_id=request.run_id, command_id=request.command_id, started_at=10., ended_at=20.)
    observations[request.attempt_id] = {"request": request.to_dict(), "response": FakeAgent().invoke(request).to_dict()}
    transitions[:] = [{"workflow_id": request.run_id, "workflow_kind": "RUN", "state": "STOPPING", "previous_state": "ACTIVE", "created_at": 5.,
        **{key: control[key] for key in ("command_id", "revision", "command_type")}, "payload_json": json.dumps(control["payload"])},
        {"workflow_id": request.run_id, "workflow_kind": "RUN", "state": "CANCELLED", "command_type": "FINISH_STOP", "revision": 8, "created_at": 30., "payload_json": json.dumps({"stop_disposition": "CANCELLED"})}]
    if failed_report:
        attempts[0]["terminal_reason"] = "FAILED"
        observations[request.attempt_id].pop("response")
        failure = {"agent_final_failure": True, "attempt_id": request.attempt_id, "failed_task_command_id": request.command_id}
        transitions.insert(1, {"workflow_id": request.run_id, "workflow_kind": "RUN", "state": "STOPPING", "revision": 6,
            "command_type": "AGENT_ATTEMPT_FINAL_FAILURE", "created_at": 21., "payload_json": json.dumps(failure)})
        transitions.insert(2, {"workflow_id": request.run_id, "workflow_kind": "RUN", "state": "STOPPING", "revision": 7,
            "command_type": "FINAL_REPORT_FAILED", "created_at": 22., "payload_json": json.dumps({"report_missing": True,
                "source_attempt_id": request.attempt_id, "final_failure": failure, "stop_disposition": "FAILED"})})
        transitions[-1].update(state="FAILED", payload_json=json.dumps({"stop_disposition": "FAILED"}))
    return AgentRequest.from_dict(request.to_dict()), attempts, observations, facts, payloads, transitions


@pytest.mark.parametrize("failed_report", [False, True])
def test_stop_report_has_no_invented_verdict_and_honest_failure(tmp_path, failed_report):
    assert audit(tmp_path, stopped_records(failed_report=failed_report)) == []


@pytest.mark.parametrize("mutation", ["missing", "cause", "effect_not_drained", "failure_source", "disposition", "retry_source"])
def test_stop_report_rejects_missing_cause_effect_failure_and_retry_tampering(tmp_path, mutation):
    args = stopped_records(failed_report=mutation in {"failure_source", "disposition"})
    request, attempts, observations, facts, payloads, transitions = args
    if mutation == "missing": attempts.clear()
    elif mutation == "cause":
        value = json.loads(transitions[0]["payload_json"]); value["cause"] = "another actual cause"
        transitions[0]["payload_json"] = json.dumps(value)
    elif mutation == "effect_not_drained": attempts.append({"attempt_id": "other-process", "kind": "PROCESS", "run_id": request.run_id, "state": "CLOSED", "terminal_reason": "SUCCEEDED", "started_at": 2., "ended_at": 15.})
    elif mutation == "failure_source":
        value = json.loads(transitions[2]["payload_json"]); value["source_attempt_id"] = "invented"
        transitions[2]["payload_json"] = json.dumps(value)
    elif mutation == "disposition": transitions[-1]["state"] = "CANCELLED"
    else:
        old = copy.deepcopy(attempts[0]); old.update(attempt_id="older-report", started_at=7., ended_at=8., terminal_reason="LOST")
        attempts.insert(0, old); prior = copy.deepcopy(observations[request.attempt_id]); prior["request"]["attempt_id"] = "older-report"
        source = prior["request"]["input_artifacts"][0]; value = json.loads(base64.b64decode(source["content_base64"]))
        value["stop_reason"] += " This source was later silently changed."
        source["content_base64"] = base64.b64encode(json.dumps(value).encode()).decode()
        observations["older-report"] = prior
    assert audit(tmp_path, args)


def test_final_report_oracle_accepts_actual_complete_source_and_report(tmp_path):
    assert audit(tmp_path, records()) == []


def failed_cycle_report_records():
    from dataclasses import replace
    from hypoloom.agent_aci import EmbeddedInput
    from hypoloom.domain import AgentCommand
    from hypoloom.fake_agent import default_request

    request, attempts, observations, facts, payloads, transitions = records()
    failed_request = replace(default_request(AgentCommand.CYCLE_REPORT), attempt_id="failed-cycle-report")
    observed = {"request": failed_request.to_dict(), "failure_code": "invalid_tool_workspace"}
    failed = {"attempt_id": failed_request.attempt_id, "kind": "AGENT", "run_id": request.run_id,
        "state": "CLOSED", "terminal_reason": "FAILED", "command_id": failed_request.command_id,
        "started_at": 1., "ended_at": 2., "observation_json": json.dumps(observed)}
    control = {"agent_final_failure": True, "attempt_id": failed_request.attempt_id,
        "failed_task_command_id": failed_request.command_id, "failure_code": "invalid_tool_workspace"}
    failure_input = EmbeddedInput.from_bytes(failed_request.attempt_id + "--final-failure", "application/json",
        json.dumps({"attempt": failed, "control": control}).encode())
    context = {"facts": facts, "stop_reason": "Required CYCLE_REPORT failed-cycle-report permanently failed; no successful report was published."}
    context_input = EmbeddedInput.from_bytes("final--final-report-context", "application/json", json.dumps(context).encode())
    request = replace(request, input_artifacts=(context_input, failure_input),
        immutable_context_refs=(context_input.ref, failure_input.ref))
    attempts[0].update(run_id=request.run_id, command_id=request.command_id, started_at=5., ended_at=6.)
    attempts.append(failed)
    observations[failed_request.attempt_id] = observed
    observations[request.attempt_id] = {"request": request.to_dict(), "response": FakeAgent().invoke(request).to_dict()}
    transitions.insert(0, {"workflow_id": request.run_id, "workflow_kind": "RUN", "state": "ACTIVE",
        "command_type": "AGENT_ATTEMPT_FINAL_FAILURE", "created_at": 3., "payload_json": json.dumps(control)})
    return request, attempts, observations, facts, payloads, transitions


def test_failed_cycle_report_is_disclosed_without_becoming_a_scientific_fact(tmp_path):
    args = failed_cycle_report_records()
    assert audit(tmp_path, args) == []
    assert all(row["fact_id"] != "failed-cycle-report" for row in args[3])


@pytest.mark.parametrize("mutation", ["missing_disclosure", "wrong_source", "missing_source", "wrong_control"])
def test_final_report_requires_the_actual_failed_cycle_report_source(tmp_path, mutation):
    args = failed_cycle_report_records()
    request, attempts, observations, facts, payloads, transitions = args
    observed = observations[request.attempt_id]
    if mutation == "missing_disclosure":
        artifact = observed["response"]["proposed_workspace_artifacts"][0]
        content = base64.b64decode(artifact["content_base64"]).replace(b"failed-cycle-report", b"an unspecified failure")
        artifact["content_base64"] = base64.b64encode(content).decode()
    elif mutation == "missing_source":
        observed["request"]["input_artifacts"].pop()
    elif mutation == "wrong_source":
        embedded = observed["request"]["input_artifacts"][-1]
        source = json.loads(base64.b64decode(embedded["content_base64"]))
        source["attempt"]["attempt_id"] = request.attempt_id
        embedded["content_base64"] = base64.b64encode(json.dumps(source).encode()).decode()
    else:
        control = json.loads(transitions[0]["payload_json"])
        control["failed_task_command_id"] = request.command_id
        transitions[0]["payload_json"] = json.dumps(control)
    assert "final_report_source_or_publication_invalid" in {item[0] for item in audit(tmp_path, args)}


def test_final_report_postflight_rejects_omitted_failed_cycle_report_even_with_valid_digest():
    from dataclasses import replace
    from hypoloom.agent_aci import AgentResponse, WorkspaceArtifact, validate_postflight, with_output_digest
    request, _, observations, *_ = failed_cycle_report_records()
    response = AgentResponse.from_dict(observations[request.attempt_id]["response"])
    content = response.proposed_workspace_artifacts[0].content_bytes().replace(b"failed-cycle-report", b"an unspecified failure")
    changed = with_output_digest(replace(response,
        proposed_workspace_artifacts=(WorkspaceArtifact.from_bytes("FINAL_REPORT.md", content),)))
    with pytest.raises(ValueError, match="actual failed cycle-report reference"):
        validate_postflight(request, changed)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "foreign_source", "incomplete_source", "wrong_verdict", "missing_outcome", "archive_changed"])
def test_final_report_oracle_independently_rejects_causation_reference_and_byte_tampering(tmp_path, failure):
    args = records(); request, attempts, observations, facts, payloads, transitions = args
    observed = observations[request.attempt_id]
    if failure == "missing": attempts.clear()
    elif failure == "duplicate":
        attempts.append({**attempts[0], "attempt_id": "duplicate"}); observations["duplicate"] = copy.deepcopy(observed)
    elif failure in {"foreign_source", "incomplete_source"}:
        source = observed["request"]["input_artifacts"][0]
        value = json.loads(base64.b64decode(source["content_base64"]))
        if failure == "incomplete_source": value["facts"].pop()
        else: value["facts"][0]["run_id"] = "foreign"
        source["content_base64"] = base64.b64encode(json.dumps(value).encode()).decode()
    elif failure in {"wrong_verdict", "missing_outcome"}:
        artifact = observed["response"]["proposed_workspace_artifacts"][0]
        content = base64.b64decode(artifact["content_base64"])
        content = content.replace(b"GOAL_MET", b"GOAL_NOT_MET") if failure == "wrong_verdict" else content.replace(b"outcome-accepted", b"invented")
        artifact["content_base64"] = base64.b64encode(content).decode()
    else:
        directory = tmp_path / "artifacts" / request.run_id / "agent" / request.attempt_id / "transcript"
        directory.mkdir(parents=True); (directory / "FINAL_REPORT.md").write_text("changed after publication")
        observed["session"] = {"transcript": {"path": f"{request.run_id}/agent/{request.attempt_id}/transcript"}}
    codes = {item[0] for item in audit(tmp_path, args)}
    assert codes
    assert "final_report_completion_count" in codes if failure in {"missing", "duplicate"} else "final_report_source_or_publication_invalid" in codes
