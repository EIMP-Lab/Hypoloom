"""Successful stop reuses raw science and lifecycle rows, not full-round fiction."""
import copy
import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.batch_oracle import audit_batches, shared_goal_stop
from hypoloom.domain import AgentCommand, GoalVerdict
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.final_report_oracle import audit_final_reports
from hypoloom.interaction_oracle import _InteractionAudit
from hypoloom.oracle import (
    _ComponentResult, _audit_component_verdicts, _read_goal_component_contract,
)
from test_batch_oracle import _fixture
from test_domain import all_facts


def stopped_batch():
    data = _fixture()
    data.transitions = [row for row in data.transitions if row["command_type"] != "ACCEPT_ADJUDICATION"]
    data.attempts = [row for row in data.attempts if row["attempt_id"] != "adjudicate"]
    data.attempt_payloads.pop("adjudicate")
    for row in data.attempts:
        if row["state"] == "OPEN":
            row.update(state="CLOSED", terminal_reason="CANCELLED", ended_at=data.clock)
            data.attempt_payloads[row["attempt_id"]].pop("response", None)
    verdict = replace(all_facts()[-1], verdict=GoalVerdict.GOAL_MET,
                      assessment_ids=("assessment-1",))
    data.fact(verdict)
    stop = data.transition("RECORD_GOAL_VERDICT", {"stop_disposition": "COMPLETED"}, fact_id=verdict.fact_id)
    stop.update(previous_state="ACTIVE", state="STOPPING")
    finish = data.transition("FINISH_STOP", {"stop_disposition": "COMPLETED"})
    finish.update(previous_state="STOPPING", state="COMPLETED")
    return data


def audit(data, version="hypoloom-lifecycle-v9"):
    return audit_batches(data.run_id, data.attempts, data.attempt_payloads,
                         data.facts, data.fact_payloads, data.transitions, data.commands,
                         workflow_version=version)


@pytest.mark.parametrize("version", ["hypoloom-lifecycle-v9", "hypoloom-lifecycle-v10"])
def test_shared_success_stops_before_adjudication_without_inventing_round_closure(version):
    data = stopped_batch()
    before = copy.deepcopy((data.facts, data.transitions, data.attempts))
    assert audit(data, version) == []
    assert (data.facts, data.transitions, data.attempts) == before


@pytest.mark.parametrize("version", [None, "hypoloom-lifecycle-v8"])
def test_retained_version_does_not_gain_successful_stop_exemption(version):
    assert audit(stopped_batch(), version)


@pytest.mark.parametrize("tamper", ["wrong_assessment", "refuted", "conjunction", "not_stopping", "missing_fact", "future_assessment"])
def test_success_stop_requires_its_prior_exact_shared_supported_fact(tamper):
    data = stopped_batch()
    stop = data.transitions[-2]
    verdict = data.fact_payloads[stop["fact_id"]]
    if tamper == "wrong_assessment": verdict["assessment_ids"] = ["invented"]
    elif tamper == "refuted": data.fact_payloads["fact-assessment-1"]["assessment"] = "REFUTED"
    elif tamper == "conjunction": data.fact_payloads[data.facts[0]["fact_id"]]["research_contract"]["methodology"]["controls"]["product"]["goal"]["completion_mode"] = "explicit_signals"
    elif tamper == "not_stopping": stop["state"] = "ACTIVE"
    elif tamper == "missing_fact": stop["fact_id"] = "missing"
    else:
        source = next(row for row in data.transitions if row.get("fact_id") == "fact-assessment-1")
        source["raw_order"] = stop["raw_order"] + 1
    assert audit(data)


def verdict_issues(data, *, winner_result=None):
    issues = []
    contract = _read_goal_component_contract(data.facts, data.fact_payloads, "hypoloom-research-v9", issues)
    assert contract is not None and not issues
    assessments = {value["assessment_id"]: {**row, "subject_id": value["assessment_id"]}
        for row in data.facts for value in [data.fact_payloads[row["fact_id"]]]
        if row["fact_type"] == "HypothesisAssessed"}
    results = {identity: _ComponentResult("SUCCESS" if data.fact_payloads[row["fact_id"]]["assessment"] == "SUPPORTED"
        else "CLEAN_REFUTATION", data.fact_payloads[row["fact_id"]]["assessment"] == "SUPPORTED", "0.8", None, None)
        for identity, row in assessments.items()}
    if winner_result is not None:
        results["assessment-1"] = winner_result
    # The main Oracle supplies independently rederived criterion results here;
    # no ADJ-selected map or completed expert lineage exists at this seam.
    _audit_component_verdicts(contract, data.facts, data.fact_payloads, assessments, {}, results, issues,
        transitions=data.transitions, completion_mode="shared_goal", selected_specs={},
        expert_lineages_terminated=False, workflow_version="hypoloom-lifecycle-v9")
    return issues


def test_pre_adjudication_winner_is_qualified_by_rederived_criterion_not_round_completion():
    assert verdict_issues(stopped_batch()) == []


@pytest.mark.parametrize("result", [
    _ComponentResult("SUCCESS", False, "0.1", None, None),
    _ComponentResult("SUCCESS", None, None, None, None),
    _ComponentResult("CANCELLED", False, None, None, None),
    _ComponentResult("EXECUTION_FAILURE", False, None, None, None),
])
def test_supported_label_alone_cannot_qualify_successful_stop(result):
    assert {issue.code for issue in verdict_issues(stopped_batch(), winner_result=result)} == {"verdict_shared_goal_mismatch"}


@pytest.mark.parametrize("accepted_late", [False, True])
def test_stable_winner_uses_only_assessments_accepted_at_verdict(accepted_late):
    data = stopped_batch()
    # A second, lexically earlier qualified hypothesis is not a numerical rank.
    for value in data.fact_payloads.values():
        if value.get("hypothesis_id") == "hyp-2": value["hypothesis_id"] = "aaa-earlier-id"
    data.fact_payloads["fact-assessment-2"]["assessment"] = "SUPPORTED"
    if accepted_late:
        source = next(row for row in data.transitions if row.get("fact_id") == "fact-assessment-2")
        source["raw_order"] = data.transitions[-2]["raw_order"] + 1
    issues = verdict_issues(data)
    assert (not issues) is accepted_late, issues


def reported_stop(*, failure=False, cancel=False):
    data = stopped_batch()
    data.transitions.pop()  # Finish only after the actual final-report Attempt.
    stop = data.transitions[-1]
    controls = [{"command_id": stop["command_id"], "revision": stop["revision"],
        "command_type": stop["command_type"], "payload": json.loads(stop["payload_json"])}]
    context = EmbeddedInput.from_bytes("final--final-report-context", "application/json", json.dumps({
        "facts": data.facts, "stop_controls": controls,
        "stop_reason": "Accepted shared-goal success: " + stop["command_id"],
    }).encode())
    request = default_request(AgentCommand.FINAL_REPORT)
    task = replace(request.task_context, run_id=data.run_id)
    request = replace(request, run_id=data.run_id, task_context=task, command_id=task.command_id,
        input_artifacts=(context,), immutable_context_refs=(context.ref,))
    requested = data.transition("REQUEST_AUXILIARY", task.to_dict())
    requested.update(command_id=request.command_id, previous_state="STOPPING", state="STOPPING")
    data.clock += 1
    row = {"attempt_id": request.attempt_id, "run_id": data.run_id, "command_id": request.command_id,
        "kind": "AGENT", "state": "CLOSED", "terminal_reason": "FAILED" if failure else "SUCCEEDED",
        "started_at": data.clock, "ended_at": data.clock + 0.2}
    data.attempts.append(row)
    data.attempt_payloads[request.attempt_id] = {"request": request.to_dict()}
    if failure:
        control = {"agent_final_failure": True, "attempt_id": request.attempt_id,
            "failed_task_command_id": request.command_id}
        data.transition("AGENT_ATTEMPT_FINAL_FAILURE", control)
        data.transition("FINAL_REPORT_FAILED", {"report_missing": True, "source_attempt_id": request.attempt_id,
            "final_failure": control, "stop_disposition": "FAILED"})
    else:
        data.attempt_payloads[request.attempt_id]["response"] = FakeAgent().invoke(request).to_dict()
    disposition = "FAILED" if failure else "COMPLETED"
    if cancel:
        data.transition("CANCEL", {"cause": "Repeated operator stop after successful stop began", "stop_disposition": disposition})
    data.transition("FINISH_STOP", {"stop_disposition": disposition}).update(previous_state="STOPPING", state=disposition)
    for transition in data.transitions:
        transition.setdefault("state", "ACTIVE")
    return data, request.attempt_id


def report_issues(root, data, version="hypoloom-lifecycle-v9"):
    return audit_final_reports(root, data.run_id, data.attempts, data.attempt_payloads,
        data.facts, data.fact_payloads, data.transitions, workflow_version=version)


@pytest.mark.parametrize("failure,cancel", [(False, False), (True, False), (False, True), (True, True)])
def test_successful_stop_uses_drained_report_and_preserves_actual_terminal_disposition(tmp_path, failure, cancel):
    data, _ = reported_stop(failure=failure, cancel=cancel)
    assert report_issues(tmp_path, data) == []
    verdicts = [value for value in data.fact_payloads.values() if value["fact_type"] == "GoalVerdictRecorded"]
    assert len(verdicts) == 1 and verdicts[0]["verdict"] == "GOAL_MET"


@pytest.mark.parametrize("forged_disposition", ["CANCELLED", "FAILED"])
def test_repeated_cancel_cannot_downgrade_success_or_fabricate_failure(tmp_path, forged_disposition):
    data, _ = reported_stop(cancel=True)
    cancel = next(row for row in data.transitions if row["command_type"] == "CANCEL")
    value = json.loads(cancel["payload_json"])
    value["stop_disposition"] = forged_disposition
    cancel["payload_json"] = json.dumps(value)
    data.transitions[-1].update(state=forged_disposition, payload_json=json.dumps({"stop_disposition": forged_disposition}))
    assert report_issues(tmp_path, data)


@pytest.mark.parametrize("version", [None, "hypoloom-lifecycle-v8", "hypoloom-lifecycle-v9", "hypoloom-lifecycle-v10"])
def test_ordinary_cancel_without_accepted_success_still_finishes_cancelled(tmp_path, version):
    from test_final_report_oracle import stopped_records
    request, attempts, observations, facts, payloads, transitions = stopped_records()
    assert transitions[-1]["state"] == "CANCELLED"
    assert audit_final_reports(tmp_path, request.run_id, attempts, observations, facts, payloads,
        transitions, workflow_version=version) == []


@pytest.mark.parametrize("tamper", ["no_report", "wrong_disposition", "wrong_stop_cause", "effect_not_drained", "new_research"])
def test_successful_stop_does_not_excuse_missing_report_undrained_effect_or_new_research(tmp_path, tamper):
    data, final_id = reported_stop()
    report = next(row for row in data.attempts if row["attempt_id"] == final_id)
    if tamper == "no_report":
        data.attempts.remove(report)
    elif tamper == "wrong_disposition":
        data.transitions[-1]["state"] = "CANCELLED"
    elif tamper == "wrong_stop_cause":
        stop = next(row for row in data.transitions if row["command_type"] == "RECORD_GOAL_VERDICT")
        stop["payload_json"] = json.dumps({"stop_disposition": "CANCELLED"})
    elif tamper == "effect_not_drained":
        data.attempts[0].update(state="OPEN", ended_at=None, terminal_reason=None)
    else:
        rogue = copy.deepcopy(data.attempts[0])
        rogue.update(attempt_id="post-stop-research", started_at=report["started_at"] - 0.2, ended_at=report["started_at"] - 0.1)
        data.attempts.append(rogue)
        data.attempt_payloads[rogue["attempt_id"]] = copy.deepcopy(data.attempt_payloads[data.attempts[0]["attempt_id"]])
    assert report_issues(tmp_path, data)


@pytest.mark.parametrize("action", ["leader_aborted", "debate_aborted"])
@pytest.mark.parametrize("tamper", [None, "wrong_verdict", "before_stop", "active_state", "retained_version"])
def test_interaction_abort_joins_actual_successful_stop_without_claiming_round_completion(tmp_path, action, tamper):
    data = stopped_batch()
    stop, verdict = shared_goal_stop(data.facts, data.fact_payloads, data.transitions, "hypoloom-lifecycle-v9")
    # This narrow identity seam is followed by the existing Leader/Debate
    # original-Observer/reason checks. Full Host cancellation has a separate G3.
    row = {"revision": stop["revision"] + 1, "previous_state": "STOPPING", "state": "STOPPING"}
    event = {"action": action, "goal_verdict_id": verdict["verdict_id"]}
    version = "hypoloom-lifecycle-v9"
    if tamper == "wrong_verdict":
        event["goal_verdict_id"] = "another-verdict"
    elif tamper == "before_stop":
        row["revision"] = stop["revision"] - 1
    elif tamper == "active_state":
        row["state"] = row["previous_state"] = "ACTIVE"
    elif tamper == "retained_version":
        version = "hypoloom-lifecycle-v8"
    view = _InteractionAudit(tmp_path, data.attempts, data.attempt_payloads, data.facts,
        data.fact_payloads, data.transitions, version)
    if tamper is None:
        view.aborted(row, event)
        assert not any(item.get("action", "").endswith("finished") for item in (event,))
    else:
        with pytest.raises(ValueError, match="abortion"):
            view.aborted(row, event)


def pre_spawn_cancel_records():
    identity = "queued--process-1"
    request = {"attempt_id": identity, "attempt_ordinal": 1, "execution_id": "execution-queued",
        "argv": ["python", "experiment.py"], "cwd": "/output", "env": {}, "timeout_seconds": 30.,
        "terminate_grace_seconds": 0.5, "kill_grace_seconds": 1., "stdout_path": "stdout.log", "stderr_path": "stderr.log"}
    attempt = {"attempt_id": identity, "run_id": "run-1", "workflow_id": "queued", "experiment_id": "queued",
        "execution_id": "execution-queued", "command_id": "queued--cancel-effect", "attempt_ordinal": 1,
        "kind": "PROCESS", "state": "CLOSED", "terminal_reason": "CANCELLED", "process_group_id": None}
    observed = {key: attempt[key] for key in ("attempt_id", "attempt_ordinal", "execution_id", "terminal_reason")}
    observed.update(synthetic_before_spawn=True, process_request=request)
    commands = {attempt["command_id"]: {"command_type": "CANCEL", "run_id": "run-1", "workflow_id": "queued",
        "response_status": "accepted", "request_json": json.dumps({"synthetic_cancel": True}),
        "result_json": json.dumps({"attempt_id": identity})},
        "cancel-request": {"command_type": "CANCEL"}, "cancel-terminal": {"command_type": "CANCEL"}}
    transitions = [{"run_id": "run-1", "workflow_id": "queued", "workflow_kind": "EXPERIMENT", "revision": 4,
        "command_id": "cancel-request", "previous_state": "READY", "state": "EVALUATING"},
        {"run_id": "run-1", "workflow_id": "queued", "workflow_kind": "EXPERIMENT", "revision": 5,
        "command_id": "cancel-terminal", "previous_state": "EVALUATING", "state": "EVALUATING"}]
    payloads = {"queued@4": {}, "queued@5": {"attempt_id": identity, "synthetic": True}}
    context = {"contract": {"timeout_seconds": 30.}, "payload": {}, "resource": None,
        "source_path": "experiment.py", "execution_argv": ("python", "experiment.py"), "component_mounts": None}
    return attempt, observed, commands, transitions, payloads, context


@pytest.mark.parametrize("tamper", [None, "real_process", "wrong_owner", "wrong_result", "missing_cancel",
    "wrong_cancel_source", "wrong_execution", "second_attempt", "not_cancelled", "timeout"])
def test_queued_cancel_uses_actual_cancel_command_and_terminal_request_not_fictional_execute(tamper):
    from hypoloom.oracle import _audit_pre_spawn_cancel
    attempt, observed, commands, transitions, payloads, context = pre_spawn_cancel_records()
    if tamper == "real_process": attempt["process_group_id"] = 12345
    elif tamper == "wrong_owner": commands[attempt["command_id"]]["command_type"] = "EXECUTE"
    elif tamper == "wrong_result": commands[attempt["command_id"]]["result_json"] = json.dumps({"attempt_id": "other"})
    elif tamper == "missing_cancel": transitions.pop(0)
    elif tamper == "wrong_cancel_source": payloads["queued@5"]["attempt_id"] = "other"
    elif tamper == "wrong_execution": observed["process_request"]["execution_id"] = "other"
    elif tamper == "second_attempt": attempt["attempt_ordinal"] = 2
    elif tamper == "not_cancelled": attempt["terminal_reason"] = observed["terminal_reason"] = "SUCCEEDED"
    elif tamper == "timeout": observed["process_request"]["timeout_seconds"] = 60.
    issues = []
    _audit_pre_spawn_cancel(attempt, observed, commands, transitions, payloads, context, issues)
    assert bool(issues) is (tamper is not None), issues
