"""Advice uses accepted artifacts; it never grants a scientific round or peer control."""
import json

import pytest

from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.interaction_host import InteractionHost
from test_expert_interaction import _observer
from test_interaction_host import StoredHost, node
from test_interaction_oracle import RawRecords


@pytest.fixture
def host(tmp_path):
    value = StoredHost(tmp_path / "state", agent_version=23, rounds=1)
    yield value
    value.store.close()


def selected(host):
    view = InteractionHost(host)
    observer = view._triggers("run-1")[0][0]
    host.record(observer, candidate={**_observer(), "supporting_refs": ["hyp-2", "out-2"]},
        inputs=view.inputs("run-1", observer))
    view.drive("run-1")
    view.drive("run-1")
    return view, host.launched[-1][0]


def publish(host, view, task):
    inputs = view.guidance_task_inputs("run-1", task)
    result = host.record(task, inputs=inputs, candidate={"task_kind": "LEADER",
        "summary": "Measured only the declared protocol; adapt the control or reject this hypothesis independently.",
        "supporting_refs": [inputs[1].ref]})
    view.drive("run-1")
    return result


def proposal(wing, cycle=2):
    return node("HYPOTHESIS_PROPOSAL", "researcher", "primary.score", cycle,
        subkind=ResearchNodeSubkind(wing))


def test_advice_without_extra_experiment_budget_has_no_pause_priority_or_parent(host):
    view, task = selected(host)
    assert task.kind.value == "LEADER" and task.expert_slot == "critic"
    assert not view.blocks_next_cycle("run-1", "primary.score", 2)
    assert not view.blocks_next_cycle("run-1", "must.robustness", 2)
    assert view.priority_roles("run-1") == ()
    assert view.leader_round("run-1", "must.robustness") is None
    assert not any("participant_control" in json.loads(row["payload_json"]) for row in view._rows("run-1"))
    assert len(host._batch_history("run-1", "must.robustness", "critic").batches) == 1
    source = publish(host, view, task)
    # Reconstruct after publication; no second terminal effect or new advice from polling.
    for _ in range(3): InteractionHost(host).drive("run-1")
    finished = [event for _, event in view._events("run-1") if event.get("terminal_reason") == "GUIDANCE_ACCEPTED"]
    assert [item["guidance_attempt_id"] for item in finished] == [source["attempt_id"]]
    remaining = view._triggers("run-1")[0][0]
    context = json.loads(next(item.content_bytes() for item in view.inputs("run-1", remaining)
        if item.ref.endswith("--interaction-context")))
    assert "leader" not in context["allowed_actions"]
    assert RawRecords(host).audit() == []


def test_all_wings_and_retries_keep_first_request_advice_and_late_advice_waits(host):
    view, task = selected(host)
    first = proposal("FORWARD")
    assert view.guidance_inputs("run-1", first) == ()
    host.record(first, inputs=(), terminal="FAILED")
    source = publish(host, view, task)
    for wing in ("FORWARD", "BACKWARD", "SYNTHESIS"):
        assert InteractionHost(host).guidance_inputs("run-1", proposal(wing)) == ()
    new = proposal("FORWARD", 3)
    advice = view.guidance_inputs("run-1", new)
    assert len(advice) == 1 and advice[0].ref == source["attempt_id"] + "--leader-guidance"
    host.record(new, inputs=advice, terminal="FAILED")
    for wing in ("FORWARD", "BACKWARD", "SYNTHESIS"):
        target = proposal(wing, 3)
        assert InteractionHost(host).guidance_inputs("run-1", target) == advice
        host.record(target, inputs=advice)
    assert RawRecords(host).audit() == []


def test_guidance_retry_reuses_actual_sources_and_failure_is_local(host):
    view, task = selected(host)
    inputs = view.guidance_task_inputs("run-1", task)
    host.record(task, inputs=inputs, terminal="FAILED")
    host.finish_round("researcher", "primary.score", 2)
    assert InteractionHost(host).guidance_task_inputs("run-1", task) == inputs
    view.drive("run-1")
    assert view._active("run-1") is None
    assert not view.blocks_next_cycle("run-1", "primary.score", 2)
    assert view.guidance_inputs("run-1", proposal("FORWARD")) == ()
    event = next(event for _, event in view._events("run-1") if event["action"] == "leader_round_finished")
    assert event["terminal_reason"] == "AGENT_FINAL_FAILURE"
    assert RawRecords(host).audit() == []


def test_unavailable_advice_budget_closes_without_inventing_an_attempt(host):
    view = InteractionHost(host)
    observer = view._triggers("run-1")[0][0]
    host.record(observer, candidate={**_observer(), "supporting_refs": ["hyp-2", "out-2"]}, inputs=view.inputs("run-1", observer))
    view.drive("run-1")
    host._agent_budget_available = lambda *_: False
    view.drive("run-1")
    assert view._active("run-1") is None and not host.launched
    assert RawRecords(host).audit() == []


@pytest.mark.parametrize("damage", ["author", "content", "missing", "future", "duplicate-publication", "retry-source", "scheduling-authority"])
def test_independent_advice_audit_rejects_changed_sources_and_effects(host, damage):
    view, task = selected(host)
    source = publish(host, view, task)
    first = proposal("FORWARD")
    consumer = host.record(first, inputs=view.guidance_inputs("run-1", first))
    data = RawRecords(host)
    assert data.audit() == []
    identity = consumer["attempt_id"]
    if damage in {"author", "content"}:
        data.edit_input(identity, "--leader-guidance", lambda body: body.update(
            {"author": "other"} if damage == "author" else {"guidance": {"summary": "fabricated"}}))
    elif damage == "missing":
        data.observations[identity]["request"]["input_artifacts"] = []
    elif damage == "future":
        next(row for row in data.attempts if row["attempt_id"] == identity)["started_at"] = 0
    elif damage == "duplicate-publication":
        data.transitions.append(data.event("leader_round_finished"))
    elif damage == "retry-source":
        data.edit_input(source["attempt_id"], "--leader-context", lambda body: body.update(facts=[]))
    else:
        data.event("leader_round_started")["payload"]["expert_interaction"]["leader"]["cycle"] = 2
    assert data.audit()


def test_details_show_actual_advice_inputs_without_claiming_adoption(host):
    from hypoloom.observer_details import _interactions
    from hypoloom.observer_summary import _interactions as summary
    view, task = selected(host)
    source = publish(host, view, task)
    first = proposal("FORWARD")
    consumer = host.record(first, inputs=view.guidance_inputs("run-1", first))
    data = RawRecords(host)
    detail, = _interactions(data.transitions, {}, data.attempts)["items"]
    assert detail["mode"] == "experience_sharing" and detail["state"] == "FINISHED"
    assert detail["guidance"]["author"] == "critic"
    assert detail["guidance"]["attempt_id"] == source["attempt_id"]
    assert detail["provided_to"] == [{"attempt_id": consumer["attempt_id"], "role": "researcher",
        "component_id": "primary.score", "cycle": 2, "subkind": "FORWARD"}]
    leaders, _ = summary(data.transitions)
    assert next(iter(leaders.values()))["state"] == "completed"


def test_advice_failure_is_visible_and_preserves_operator_pause(host):
    from hypoloom.lifecycle import Command
    from hypoloom.observer_details import _interactions
    from hypoloom.observer_summary import _interactions as summary
    from hypoloom.participant_controls import fold_participant_controls, ParticipantScope
    view, task = selected(host)
    host.control(Command.PAUSE_PARTICIPANT, {"participant_control": {"action": "pause",
        "scope": {"role": "researcher", "component_id": None, "chain": None}, "reason": "operator"}}, "operator-pause")
    host.record(task, inputs=view.guidance_task_inputs("run-1", task), terminal="FAILED")
    view.drive("run-1")
    data = RawRecords(host)
    assert _interactions(data.transitions, {}, data.attempts)["items"][0]["state"] == "FAILED"
    assert next(iter(summary(data.transitions)[0].values()))["state"] == "failed"
    controls = fold_participant_controls(data.transitions, ("researcher", "critic"))
    assert controls.blocks(ParticipantScope("researcher", "primary.score"), "LEADER")
    assert controls.blocks(ParticipantScope("researcher", "primary.score"), "LITERATURE_REVIEW")
    assert not controls.blocks(ParticipantScope("critic", "must.robustness"), "LITERATURE_REVIEW")


def test_reopening_store_recovers_selection_task_and_publication_once(host):
    from hypoloom.state_store import HostWriterCapability, StateStore
    view, task = selected(host)
    original = view.guidance_task_inputs("run-1", task)
    host.store.close()
    host.store = StateStore(host.state_root, HostWriterCapability.issue()).initialize()
    view = InteractionHost(host)
    view.drive("run-1")
    assert host.launched[-1][0] == task
    assert view.guidance_task_inputs("run-1", task) == original
    host.record(task, inputs=original, candidate={"task_kind": "LEADER", "summary": "Actual accepted advice", "supporting_refs": []})
    # Crash after accepting the model output, before publishing its interaction effect.
    host.store.close()
    host.store = StateStore(host.state_root, HostWriterCapability.issue()).initialize()
    view = InteractionHost(host)
    view.drive("run-1")
    view.drive("run-1")
    assert len(host._expert_attempts(task)) == 1
    assert sum(event.get("terminal_reason") == "GUIDANCE_ACCEPTED" for _, event in view._events("run-1")) == 1
    assert RawRecords(host).audit() == []


def test_operator_stop_aborts_advice_without_publishing_late_output(host):
    from hypoloom.lifecycle import Command, RunState
    view, task = selected(host)
    inputs = view.guidance_task_inputs("run-1", task)
    source = host.record(task, inputs=inputs, terminal=None)
    with host.store.transaction() as tx:
        tx.record_command({"command_id": "operator-stop", "run_id": "run-1", "workflow_id": "run-1",
            "command_type": "CANCEL", "request": {}, "status": "accepted", "result": {}})
        tx.append_transition(host._transition_record(tx, "run-1", "run-1", "RUN", Command.CANCEL,
            RunState.STOPPING, command_id="operator-stop", payload={"cause": "operator request", "stop_disposition": "CANCELLED"}))
    view.stop_guidance("run-1")
    # A late successful response is retained as a response, never published as advice.
    import time
    with host.store.transaction() as tx:
        tx.close_attempt(source["attempt_id"], {"terminal_reason": "SUCCEEDED", "ended_at": time.time(),
            "observation": {"response": {"typed_candidate": {"task_kind": "LEADER", "summary": "Late response", "supporting_refs": []}}}})
    view.stop_guidance("run-1")
    assert view._active("run-1") is None
    assert view.guidance_inputs("run-1", proposal("FORWARD")) == ()
    events = [event for _, event in view._events("run-1")]
    assert sum(event["action"] == "leader_aborted" for event in events) == 1
    assert not any(event.get("terminal_reason") == "GUIDANCE_ACCEPTED" for event in events)
    assert RawRecords(host).audit() == []


def test_report_coalescing_cannot_drop_an_already_admitted_observer(tmp_path):
    import time
    host = StoredHost(tmp_path / "state", agent_version=23, pending_self=True,
        interaction_options={"observer": {"coalesce_reports": True}})
    try:
        def close_self(role, component):
            task = node("SELF_EVOLVE", role, component)
            attempt, = host._expert_attempts(task)
            with host.store.transaction() as tx:
                tx.close_attempt(attempt["attempt_id"], {"terminal_reason": "SUCCEEDED", "ended_at": time.time(),
                    "observation": {"response": {"typed_candidate": {"task_kind": "SELF_EVOLVE", "summary": "Closed", "supporting_refs": []},
                        "output_digest": attempt["attempt_id"]}}})
        close_self("critic", "must.robustness")
        view = InteractionHost(host)
        admitted, _ = view._triggers("run-1")[0]
        source = host.record(admitted, inputs=view.inputs("run-1", admitted), candidate=_observer("none"))
        # An older report's delayed self-evolution closes after this Observer ran.
        close_self("researcher", "primary.score")
        earlier, _ = view._triggers("run-1")[0]
        assert earlier.ordinal < admitted.ordinal
        host.record(earlier, inputs=view.inputs("run-1", earlier), candidate=_observer("none"))
        view.drive("run-1")
        report = next(report for report in view._reports("run-1") if view._task("run-1", report) == admitted)
        assert view._covered_report("run-1", report)
        assert admitted in [task for task, _ in view._triggers("run-1")]
        view.drive("run-1")
        assert sum(event.get("source_attempt_id") == source["attempt_id"] for _, event in view._events("run-1")) == 1
        assert RawRecords(host).audit() == []
    finally:
        host.store.close()
