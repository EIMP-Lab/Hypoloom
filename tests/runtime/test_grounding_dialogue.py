import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.grounding_dialogue import GroundingDialogue
from hypoloom.lifecycle import Command, RunState
from hypoloom.monitor import build_status_projection
from hypoloom.state_store import HostWriterCapability, IdentityConflict, RevisionConflict, StateStore
from hypoloom.workflow_host import WorkflowHost


QUESTION = {"question": "Which split?", "rationale": "The project supports two protocols.",
    "options": ["Official", "Cross-validation"]}


class DialogueHost(SimpleNamespace):
    _transition_record = WorkflowHost._transition_record


@pytest.fixture
def dialogue(tmp_path):
    store = StateStore(tmp_path / "state", HostWriterCapability.issue()).initialize()
    host = DialogueHost(store=store)
    version = {key: "test" for key in ("workflow_definition_version", "research_contract_version",
        "evaluator_version", "agent_aci_version", "runner_version", "artifact_result_schema_version",
        "runtime_environment_id", "configuration_id")}
    version["sqlite_runtime_version"] = sqlite3.sqlite_version
    for run_id in ("dialogue", "other"):
        with store.transaction() as tx:
            tx.create_run(run_id, version)
            tx.append_transition(host._transition_record(tx, run_id, run_id, "RUN", Command.CREATE_RUN, RunState.CREATED))
            tx.append_transition(host._transition_record(tx, run_id, run_id, "RUN", Command.ACTIVATE, RunState.ACTIVE))
    yield GroundingDialogue(host)
    host.store.close()


def source(dialogue, *, ordinal=None):
    task = dialogue.current_task("dialogue")
    if ordinal is not None:
        task = replace(task, ordinal=ordinal)
    request = replace(default_request(AgentCommand.GROUNDING), schema_version=15, run_id="dialogue",
        owner_workflow_id="dialogue", task_context=task, command_id=task.command_id,
        attempt_id=f"grounding-{task.ordinal}", required_output_schema="GroundingCandidate/v2")
    with dialogue.host.store.transaction() as tx:
        tx.record_command({"command_id": request.command_id, "run_id": request.run_id,
            "workflow_id": request.run_id, "command_type": Command.REQUEST_AUXILIARY.value,
            "request": request.to_dict()})
        tx.append_transition(dialogue.host._transition_record(tx, request.run_id, request.run_id,
            "RUN", Command.REQUEST_AUXILIARY, RunState.ACTIVE, request.command_id))
        tx.open_attempt({"attempt_id": request.attempt_id, "run_id": request.run_id,
            "workflow_id": request.run_id, "command_id": request.command_id, "kind": "AGENT",
            "attempt_ordinal": 1, "host_generation": "offline-test", "started_at": 1,
            "payload": {"request": request.to_dict()}})
    return request


def ask(dialogue, *, question=QUESTION):
    request = source(dialogue)
    with dialogue.host.store.transaction() as tx:
        tx.close_attempt(request.attempt_id, terminal_reason="SUCCEEDED", ended_at=2)
        decision_id = dialogue.open_question(tx, request, question)
    return request, decision_id


def answer(dialogue, decision_id, *, text="  Keep both protocols.\nUse the official comparison.  ",
    identity="operator-answer", run_id="dialogue", revision=None):
    revision = dialogue.host.store.latest_transition(run_id)["revision"] if revision is None else revision
    request = ControlRequest(1, identity, revision, "ANSWER",
        {"run_id": run_id, "decision_id": decision_id, "answer": text})
    return request, dialogue.answer(request)


def test_two_rounds_reconstruct_exact_history_after_database_reopen(dialogue):
    first, decision_id = ask(dialogue)
    store = dialogue.host.store
    head = store.latest_transition("dialogue")
    assert head["state"] == "WAITING_HUMAN"
    assert not any(row["state"] == "OPEN" for row in store.list_attempts())
    status = build_status_projection(store, "dialogue", quiescent_for_backup=True)
    assert status["run_state"] == "WAITING_HUMAN" and status["open_attempts"] == 0
    assert status["grounding_dialogue"] == [{"decision_id": decision_id, "state": "OPEN",
        "resolution": None, "ordinal": 1, **QUESTION, "answer": None}]
    assert dialogue.current_task("dialogue").ordinal == 1
    store.close()
    dialogue.host.store = StateStore(store.state_root, HostWriterCapability.issue()).initialize()
    request, accepted = answer(dialogue, decision_id)
    assert accepted.result["run_state"] == "ACTIVE"
    assert dialogue.current_task("dialogue").ordinal == 2
    expected = {"run_id": "dialogue", "rounds": [{"decision_id": decision_id, "ordinal": 1,
        **QUESTION, "answer": request.payload["answer"]}]}
    assert dialogue.history("dialogue", 2) == expected
    status = build_status_projection(dialogue.host.store, "dialogue", quiescent_for_backup=True)
    assert status["run_state"] == "ACTIVE"
    assert status["grounding_dialogue"] == [{"decision_id": decision_id, "state": "RESOLVED",
        "resolution": "EDITED", "ordinal": 1, **QUESTION, "answer": request.payload["answer"]}]
    assert dialogue.history("dialogue", 1) == {"run_id": "dialogue", "rounds": []}
    second, second_id = ask(dialogue, question={**QUESTION, "question": "Which aggregation?", "options": []})
    assert first.command_id != second.command_id
    answer(dialogue, second_id, text="Five-seed mean, never best seed.", identity="second-answer")
    assert dialogue.current_task("dialogue").ordinal == 3
    assert [row["ordinal"] for row in dialogue.history("dialogue", 3)["rounds"]] == [1, 2]
    assert not dialogue.host.store.list_facts("dialogue")


def test_exact_replay_and_conflicting_second_answers(dialogue):
    _, decision_id = ask(dialogue)
    request, accepted = answer(dialogue, decision_id)
    assert dialogue.answer(request) == accepted
    with pytest.raises(IdentityConflict):
        dialogue.answer(replace(request, payload={**request.payload, "answer": "Different"}))
    with pytest.raises(IdentityConflict):
        answer(dialogue, decision_id, identity="different-request")
    assert len(dialogue.host.store.inspect("SELECT * FROM workflow_commands WHERE command_type='RESOLVE_CONTINUE'")) == 1


@pytest.mark.parametrize("fault", ["wrong-run", "stale", "missing-revision", "empty", "extra-field"])
def test_invalid_answers_leave_wait_and_question_unchanged(dialogue, fault):
    _, decision_id = ask(dialogue)
    head = dialogue.host.store.latest_transition("dialogue")
    request = ControlRequest(1, "bad-answer", head["revision"], "ANSWER",
        {"run_id": "dialogue", "decision_id": decision_id, "answer": "Official"})
    if fault == "wrong-run":
        request = replace(request, expected_revision=2, payload={**request.payload, "run_id": "other"})
    elif fault == "stale": request = replace(request, expected_revision=head["revision"] - 1)
    elif fault == "missing-revision": request = replace(request, expected_revision=None)
    elif fault == "empty": request = replace(request, payload={**request.payload, "answer": "  "})
    else: request = replace(request, payload={**request.payload, "approval": True})
    with pytest.raises((ValueError, RevisionConflict)):
        dialogue.answer(request)
    assert dialogue.host.store.latest_transition("dialogue") == head
    assert dialogue.host.store.inspect("SELECT state FROM workflow_human_decisions")[0]["state"] == "OPEN"
    assert not dialogue.host.store.inspect("SELECT * FROM workflow_commands WHERE command_id='bad-answer'")


def test_open_question_requires_closed_source_and_quiescent_owner(dialogue):
    request = source(dialogue)
    with pytest.raises(IdentityConflict, match="CLOSED source"):
        with dialogue.host.store.transaction() as tx:
            dialogue.open_question(tx, request, QUESTION)
    other = source(dialogue, ordinal=2)
    with pytest.raises(IdentityConflict, match="OPEN Attempt"):
        with dialogue.host.store.transaction() as tx:
            tx.close_attempt(request.attempt_id, terminal_reason="SUCCEEDED", ended_at=2)
            dialogue.open_question(tx, request, QUESTION)
    assert dialogue.host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
    assert dialogue.host.store.get_attempt(other.attempt_id)["state"] == "OPEN"
    assert not dialogue.host.store.inspect("SELECT * FROM workflow_human_decisions")


def test_failed_transaction_cannot_half_resolve_question(dialogue, monkeypatch):
    _, decision_id = ask(dialogue)
    def fail(*args, **kwargs):
        raise RuntimeError("interrupted before continuation")
    monkeypatch.setattr(dialogue.host, "_transition_record", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        answer(dialogue, decision_id)
    assert dialogue.host.store.latest_transition("dialogue")["state"] == "WAITING_HUMAN"
    assert dialogue.host.store.inspect("SELECT state FROM workflow_human_decisions")[0]["state"] == "OPEN"


def test_cancel_closes_pending_decision_without_manufacturing_answer(dialogue):
    _, decision_id = ask(dialogue)
    with dialogue.host.store.transaction() as tx:
        assert dialogue.cancel_pending(tx, "dialogue") == (decision_id,)
        tx.append_transition(dialogue.host._transition_record(tx, "dialogue", "dialogue", "RUN",
            Command.CANCEL, RunState.STOPPING))
    with dialogue.host.store.transaction() as tx:
        assert dialogue.cancel_pending(tx, "dialogue") == ()
    row = dialogue.host.store.inspect("SELECT * FROM workflow_human_decisions")[0]
    assert row["resolution"] == "CANCELLED"
    assert json.loads(row["resolution_json"]) == {"reason": "run_stopping"}
    with pytest.raises(IdentityConflict):
        answer(dialogue, decision_id)
    assert dialogue.history("dialogue", 1)["rounds"] == []


def test_state_store_resolution_once_uses_existing_decision_identity(dialogue):
    _, decision_id = ask(dialogue)
    with dialogue.host.store.transaction() as tx:
        fields = {"run_id": "dialogue", "resolution": "EDITED", "resolution_payload": {"answer": "Official"}, "resolved_at": 3}
        assert tx.resolve_human_decision(decision_id, **fields)
        assert not tx.resolve_human_decision(decision_id, **fields)
        with pytest.raises(IdentityConflict):
            tx.resolve_human_decision(decision_id, **{**fields, "resolution_payload": {"answer": "Different"}})
        with pytest.raises(ValueError, match="requested run"):
            tx.resolve_human_decision(decision_id, **{**fields, "run_id": "other"})
