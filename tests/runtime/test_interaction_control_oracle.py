import json

import pytest

from hypoloom.oracle import _audit_participant_controls


def _records(action="pause"):
    source = "observer-attempt"
    selected = {"expert_interaction": {"action": "leader_selected", "source_attempt_id": source}}
    scope = {"role": "peer", "component_id": "primary.peer", "chain": None} if action == "pause" else {"role": None, "component_id": None, "chain": None}
    data = {"participant_control": {"action": action, "scope": scope, "reason": "leader:" + source}}
    if action == "pause":
        data["interaction_source_attempt_id"] = source
        request = {"command": "PAUSE_PARTICIPANT", "workflow_id": "run"}
    else:
        data["expert_interaction"] = {"action": "leader_round_finished", "source_attempt_id": source}
        request = data
    rows = [{"command_id": "selection", "workflow_id": "run", "run_id": "run", "workflow_kind": "RUN", "revision": 1,
             "state": "ACTIVE", "previous_state": "ACTIVE", "payload_json": json.dumps(selected)},
            {"command_id": "control", "workflow_id": "run", "run_id": "run", "workflow_kind": "RUN", "revision": 2,
             "state": "ACTIVE", "previous_state": "ACTIVE", "payload_json": json.dumps(data)}]
    commands = [{"command_id": "selection", "command_type": "ACCEPT_EXPERT_INTERACTION", "workflow_id": "run", "run_id": "run",
                 "request_json": json.dumps(selected), "response_status": "accepted", "accepted_revision": 1},
                {"command_id": "control", "command_type": action.upper() + "_PARTICIPANT", "workflow_id": "run", "run_id": "run",
                 "request_json": json.dumps(request), "response_status": "accepted", "accepted_revision": 2}]
    attempts = [{"attempt_id": source, "run_id": "run", "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED", "command_id": "observe"}]
    observations = {source: {"request": {"schema_version": 14, "run_id": "run", "command": "OBSERVER", "attempt_id": source,
        "command_id": "observe", "task_context": {"kind": "AUXILIARY"}}}}
    return commands, rows, attempts, observations


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_internal_leader_controls_bind_actual_accepted_observer_and_keep_operator_rules(action):
    issues = []
    _audit_participant_controls("hypoloom-lifecycle-v8", *_records(action), issues, roles=("leader", "peer"))
    assert not issues


@pytest.mark.parametrize("damage", ["missing-selection", "failed-source", "operator-forgery"])
def test_internal_reason_does_not_allow_forged_or_unaccepted_operator_controls(damage):
    commands, rows, attempts, observations = _records()
    if damage == "missing-selection": rows.pop(0)
    elif damage == "failed-source": attempts[0]["terminal_reason"] = "FAILED"
    else: commands[1]["request_json"] = json.dumps({"command": "PAUSE", "payload": {"run_id": "run"}})
    issues = []
    _audit_participant_controls("hypoloom-lifecycle-v8", commands, rows, attempts, observations, issues, roles=("leader", "peer"))
    assert {item.code for item in issues} == {"participant_control_binding_mismatch"}


def _debate_records(action):
    commands, rows, attempts, observations = _records("pause" if action == "pause" else "resume")
    source = "observer-attempt"
    selected = {"expert_interaction": {"action": "debate_proposed", "debate_id": source, "source_attempt_id": source}}
    rows[0]["payload_json"] = commands[0]["request_json"] = json.dumps(selected)
    data = json.loads(rows[1]["payload_json"])
    data["participant_control"]["reason"] = "debate:" + source
    if action == "pause": data["debate_id"] = source
    else:
        data["expert_interaction"] = {"action": action, "debate_id": source, "source_attempt_id": source}
        if action == "debate_verdict":
            data["expert_interaction"].update(source_attempt_id="judge", parent_observer_attempt_id=source)
            attempts.append({**attempts[0], "attempt_id": "judge", "command_id": "judge-command"})
            observations["judge"] = {"request": {**observations[source]["request"], "command": "DEBATE_JUDGE", "attempt_id": "judge", "command_id": "judge-command"}}
        commands[1]["request_json"] = json.dumps(data)
    rows[1]["payload_json"] = json.dumps(data)
    return commands, rows, attempts, observations


@pytest.mark.parametrize("action", ["pause", "debate_verdict", "debate_failed"])
def test_debate_pause_and_release_bind_the_actual_observer_or_judge(action):
    issues = []
    _audit_participant_controls("hypoloom-lifecycle-v8", *_debate_records(action), issues, roles=("leader", "peer"))
    assert not issues


@pytest.mark.parametrize("damage", ["wrong-judge-parent", "failed-judge", "missing-proposal"])
def test_debate_reason_does_not_authorize_a_foreign_or_failed_judge(damage):
    commands, rows, attempts, observations = _debate_records("debate_verdict")
    if damage == "failed-judge": attempts[-1]["terminal_reason"] = "FAILED"
    elif damage == "missing-proposal": rows.pop(0)
    else:
        data = json.loads(rows[1]["payload_json"]); data["expert_interaction"]["parent_observer_attempt_id"] = "another"
        rows[1]["payload_json"] = commands[1]["request_json"] = json.dumps(data)
    issues = []
    _audit_participant_controls("hypoloom-lifecycle-v8", commands, rows, attempts, observations, issues, roles=("leader", "peer"))
    assert {item.code for item in issues} == {"participant_control_binding_mismatch"}
