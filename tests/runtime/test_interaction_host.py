from __future__ import annotations

import json
import copy
import time
from dataclasses import replace

import pytest

from hypoloom.expert_interaction import InteractionFacts
from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeKey, ResearchNodeKind, ResearchNodeSubkind, ResearchNodeTask
from hypoloom.interaction_host import InteractionHost, cited_leader_component
from hypoloom.lifecycle import Command, RunState
from hypoloom.participant_controls import ParticipantScope, fold_participant_controls
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.shared_acceptance_host import SharedAcceptanceHost
from hypoloom.state_store import HostWriterCapability, StateStore
from hypoloom.workflow_host import WorkflowHost
from test_expert_interaction import _facts, _observer
from test_profile_v4 import documents
from test_state_transactions import run_version


def test_leader_component_follows_its_actual_cited_contribution():
    facts = InteractionFacts.from_rows("run-1", _facts())
    assert cited_leader_component(_observer(), facts=facts) == "must.robustness"
    assert json.loads(json.dumps(_facts())) == _facts()


def node(kind, role="critic", component="must.robustness", cycle=1, subkind=ResearchNodeSubkind.MAIN, chain=None):
    return ResearchNodeTask(key=ResearchNodeKey(run_id="run-1", component_id=component,
        expert_slot=role, cycle=cycle, node_kind=ResearchNodeKind(kind), subkind=subkind, chain=chain,
        experiment_ordinal=1 if kind in {"RESULT_ANALYSIS", "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION"} else None))


class StoredHost:
    """Real SQLite/Git and Host transition helper; dispatch remains a unit seam."""
    _transition_record = WorkflowHost._transition_record
    _expert_success = WorkflowHost._expert_success
    _grounding_task = WorkflowHost._grounding_task
    _expert_attempts = WorkflowHost._expert_attempts
    _batch_history = WorkflowHost._batch_history
    _batch_creation_available = WorkflowHost._batch_creation_available
    _batch_enabled = WorkflowHost._batch_enabled
    _plugin_binding = WorkflowHost._plugin_binding

    def __init__(self, root, *, rounds=4, minimum=1, enabled=True, shared_goal=False, debate=False,
                 pending_self=False, check_after="cycle_report", interaction_options=None):
        self.state_root = root
        self.store = StateStore(root, HostWriterCapability.issue()).initialize()
        self.store.create_run("run-1", replace(run_version(), agent_aci_version="14"))
        rows = copy.deepcopy(_facts(accepted_rows=True))
        product = project_profile_v4(**documents()).settings
        product["methodology"]["interaction"]["leader"].update(
            enabled=enabled, min_rounds_before_leader=minimum, check_after=check_after)
        product["methodology"]["interaction"]["debate"]["enabled"] = debate
        for section, values in (interaction_options or {}).items():
            product["methodology"]["interaction"].setdefault(section, {}).update(values)
        product["goal"]["stop_conditions"]["max_rounds_per_expert"] = rounds
        product["goal"]["completion_mode"] = "shared_goal" if shared_goal else "explicit_signals"
        product["experts"]["experts"] = [{**product["experts"]["experts"][0], "name": role} for role in ("researcher", "critic")]
        document = rows[0]["payload"]["research_contract"]
        document["methodology"].setdefault("controls", {})["product"] = product
        document["budget"]["max_experiments"] = 8
        with self.store.transaction() as tx:
            for row in rows:
                if row["fact_type"] == "CanonicalOutcomeAccepted":
                    self.seed_artifact(tx, row["payload"])
                tx.append_fact(row)
            tx.append_transition(self._transition_record(tx, "run-1", "run-1", "RUN", Command.CREATE_RUN, RunState.CREATED))
            tx.append_transition(self._transition_record(tx, "run-1", "run-1", "RUN", Command.ACTIVATE, RunState.ACTIVE))
        self.contract = InteractionFacts.from_rows("run-1", self.store.list_facts("run-1")).contract
        self.shared_acceptance = SharedAcceptanceHost(self)
        self.launched = []
        self.record(AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id="run-1"))
        for role, component, hyp in (("researcher", "primary.score", "hyp-1"), ("critic", "must.robustness", "hyp-2")):
            self.control(Command.ACCEPT_HYPOTHESIS, {"component_id": component, "role": role,
                "node_command_id": role + "--syn", "agent_attempt_id": role + "--syn-a",
                "hypothesis_batch": {"cycle": 1, "chains": {"chain-a": hyp}}})
            self.control(Command.ACCEPT_ADJUDICATION, {"component_id": component, "role": role,
                "adjudication": {"cycle": 1, "batch_command_id": role + "--syn", "selected_tip": {"node_id": hyp, "chain_id": "chain-a"}}})
            self.finish_round(role, component, self_terminal=None if pending_self else "SUCCEEDED")

    @staticmethod
    def seed_artifact(tx, fact):
        identity = fact["experiment_id"] + "--process-fixture"
        tx.record_command({"command_id": identity, "run_id": "run-1", "workflow_id": fact["experiment_id"],
            "command_type": "EXECUTE", "request": {}, "status": "accepted", "result": {}})
        tx.open_attempt({"attempt_id": identity, "run_id": "run-1", "workflow_id": fact["experiment_id"],
            "command_id": identity, "kind": "PROCESS", "experiment_id": fact["experiment_id"],
            "execution_id": fact["execution_id"], "attempt_ordinal": 1, "host_generation": "unit-fixture", "started_at": 1.0})
        tx.close_attempt(identity, {"terminal_reason": "SUCCEEDED", "ended_at": 2.0, "observation": {"exit_code": 0}})
        tx.record_artifact({"artifact_id": fact["artifact_id"], "run_id": "run-1", "experiment_id": fact["experiment_id"],
            "execution_id": fact["execution_id"], "attempt_id": identity, "artifact_kind": "result", "schema_version": 1,
            "digest_algorithm": "sha256", "digest": fact["artifact_set_digest"],
            "path": f"run-1/{fact['experiment_id']}/{fact['execution_id']}/{identity}/result"})

    def _research_contract(self, run_id):
        assert run_id == "run-1"
        return self.contract

    def _run_config(self, run_id):
        return {"execution": {}}

    def control(self, command, payload, identity=None):
        with self.store.transaction() as tx:
            if identity:
                tx.record_command({"command_id": identity, "run_id": "run-1", "workflow_id": "run-1",
                    "command_type": command.value, "request": payload, "status": "accepted", "result": {}})
            tx.append_transition(self._transition_record(tx, "run-1", "run-1", "RUN", command, RunState.ACTIVE,
                command_id=identity, payload=payload))

    def record(self, task, *, candidate=None, inputs=(), terminal="SUCCEEDED", extra_observation=None):
        aux = isinstance(task, AuxiliaryTask)
        key = None if aux else task.key
        ordinal = len(self._expert_attempts(task)) + 1
        identity = f"attempt-{len(self.store.list_attempts()) + 1}"
        request = {"run_id": "run-1", "attempt_id": identity, "command_id": task.command_id,
            "schema_version": 14, "command": task.kind.value if aux else key.node_kind.value,
            "role": (task.expert_slot or "grounding") if aux else key.expert_slot,
            "component_id": (task.component_id or "grounding") if aux else key.component_id,
            "task_context": task.to_dict(), "required_adapter_version": "fake-agent/4",
            "input_artifacts": [item.to_dict() for item in inputs], "immutable_context_refs": [item.ref for item in inputs]}
        if ordinal == 1:
            self.control(Command.REQUEST_AUXILIARY if aux else Command.REQUEST_RESEARCH_NODE,
                {"task_context": task.to_dict()}, task.command_id)
        with self.store.transaction() as tx:
            tx.open_attempt({"attempt_id": identity, "run_id": "run-1", "workflow_id": "run-1", "command_id": task.command_id,
                "kind": "AGENT", "host_generation": "test-host", "attempt_ordinal": ordinal, "started_at": time.time(),
                "observation": {"request": request}})
            if terminal:
                tx.close_attempt(identity, {"terminal_reason": terminal, "ended_at": time.time(),
                    "observation": {"response": {"typed_candidate": candidate or {"summary": "Actual unit fixture report"}, "output_digest": identity}, **(extra_observation or {})}})
        return self.store.get_attempt(identity)

    def finish_round(self, role="critic", component="must.robustness", cycle=1, *, self_terminal="SUCCEEDED"):
        self.record(node("CYCLE_REPORT", role, component, cycle))
        self.record(node("SELF_EVOLVE", role, component, cycle), terminal=self_terminal)

    def _expert_final_failures(self, run_id):
        return tuple(json.loads(row["payload_json"]) for row in self.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND json_extract(payload_json,'$.agent_final_failure')=1", (run_id,)))

    def _expert_task_final_failure(self, run_id, task):
        return next((item for item in self._expert_final_failures(run_id) if item["failed_task_command_id"] == task.command_id), None)

    def _expert_task_exhausted(self, run_id, task):
        return bool(self._expert_attempts(task)) and self._expert_success(task) is None and all(item["state"] == "CLOSED" for item in self._expert_attempts(task))

    def _record_expert_final_failure(self, run_id, component, role, task, code):
        self.control(Command.AGENT_ATTEMPT_FINAL_FAILURE, {"agent_final_failure": True,
            "failed_task_command_id": task.command_id, "attempt_id": self._expert_attempts(task)[-1]["attempt_id"], "failure_code": code})

    def _expert_agent_step(self, run_id, owner, task, role, component):
        self.launched.append((task, role, component))


@pytest.fixture
def host(tmp_path):
    result = StoredHost(tmp_path / "state")
    yield result
    result.store.close()


def select(host, *, action="leader"):
    delegate = InteractionHost(host)
    task, _ = delegate._triggers("run-1")[0]
    row = host.record(task, candidate=_observer(action), inputs=delegate.inputs("run-1", task))
    delegate.drive("run-1")
    return delegate, row, task


def test_observer_coalesces_closed_reports_it_actually_received_and_survives_reopen(tmp_path):
    host = StoredHost(tmp_path / "state", interaction_options={"observer": {"coalesce_reports": True}})
    try:
        view = InteractionHost(host)
        assert len(view._triggers("run-1")) == 2
        task = view._triggers("run-1")[0][0]
        host.record(task, candidate=_observer("none"), inputs=view.inputs("run-1", task))
        view.drive("run-1")
        assert InteractionHost(host)._triggers("run-1") == []
        assert not view.blocks_next_cycle("run-1", "primary.score", 2)
        host.finish_round("researcher", "primary.score", 2)
        assert len(InteractionHost(host)._triggers("run-1")) == 1
    finally:
        host.store.close()


def test_observer_cannot_coalesce_a_peer_that_closed_after_its_first_frozen_input(tmp_path):
    host = StoredHost(tmp_path / "state", pending_self=True,
        interaction_options={"observer": {"coalesce_reports": True}})
    try:
        view = InteractionHost(host)
        own = host._expert_attempts(node("SELF_EVOLVE", "researcher", "primary.score"))[0]
        with host.store.transaction() as tx:
            tx.close_attempt(own["attempt_id"], terminal_reason="SUCCEEDED", ended_at=time.time(), observation={})
        task = view._triggers("run-1")[0][0]
        inputs = view.inputs("run-1", task)
        host.record(task, inputs=inputs, terminal="FAILED")
        peer = host._expert_attempts(node("SELF_EVOLVE", "critic", "must.robustness"))[0]
        with host.store.transaction() as tx:
            tx.close_attempt(peer["attempt_id"], terminal_reason="SUCCEEDED", ended_at=time.time(), observation={})
        host.record(task, candidate=_observer("none"), inputs=inputs)
        view.drive("run-1")
        remaining = InteractionHost(host)._triggers("run-1")
        assert len(remaining) == 1 and remaining[0][1][1]["role"] == "critic"
    finally:
        host.store.close()


def test_failed_observer_does_not_coalesce_reports(tmp_path):
    host = StoredHost(tmp_path / "state", interaction_options={"observer": {"coalesce_reports": True}})
    try:
        view = InteractionHost(host)
        task = view._triggers("run-1")[0][0]
        host.record(task, inputs=view.inputs("run-1", task), terminal="FAILED")
        assert len(InteractionHost(host)._triggers("run-1")) == 2
    finally:
        host.store.close()


def reasons(host, role="researcher", component="primary.score"):
    rows = host.store.inspect("SELECT * FROM workflow_transitions WHERE run_id='run-1'")
    return fold_participant_controls(rows, host.contract.role_ids).reasons_for(ParticipantScope(role, component))


def test_report_observer_uses_actual_revision_background_and_closed_peer_records(host):
    from hypoloom.profile_v4 import request_node_runtime
    view = InteractionHost(host)
    task, source = view._triggers("run-1")[0]
    assert task.ordinal == host.store.inspect("SELECT revision FROM workflow_transitions WHERE command_id=?", (source[1]["command_id"],))[0]["revision"]
    inputs = view.inputs("run-1", task)
    assert request_node_runtime(inputs)["model"] == "configured-model"
    context = json.loads(next(item.content_bytes() for item in inputs if item.ref.endswith("--interaction-context")))
    assert context["source_report_ref"] == source[0]["attempt_id"] + "--response"
    assert context["allowed_actions"] == ["none", "leader"]
    assert context["eligible_leader_components"] == ["must.robustness", "primary.score"]
    assert len(context["facts"]) == len(host.store.list_facts("run-1"))
    assert len([item for item in inputs if item.ref.endswith("--response")]) == 3
    assert not any("expert-resources" in item.ref for item in inputs)
    view.drive("run-1")
    assert host.launched == [(task, "grounding", "grounding")]
    assert view.blocks_next_cycle("run-1", source[1]["component_id"], 2)


def test_none_acceptance_is_idempotent_and_restart_continues_next_observer(host):
    view, _, task = select(host, action="none")
    assert view._handled("run-1", task)
    assert len(view._events("run-1")) == 1 and not reasons(host)
    restarted = InteractionHost(host)
    restarted.drive("run-1")
    assert host.launched[-1][0] != task
    assert len(view._events("run-1")) == 1


@pytest.mark.parametrize("check_after", ["expert_round", "cycle_report"])
@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED"])
def test_report_waits_for_own_self_close_then_observer_recovers_exactly_once(tmp_path, check_after, terminal):
    host = StoredHost(tmp_path / "state", pending_self=True, check_after=check_after)
    try:
        view = InteractionHost(host)
        report = host._expert_success(node("CYCLE_REPORT"))
        evolve = node("SELF_EVOLVE")
        source = host._expert_attempts(evolve)[0]
        peer = host._expert_attempts(node("SELF_EVOLVE", "researcher", "primary.score"))[0]
        assert report is not None and source["state"] == peer["state"] == "OPEN"
        assert view._triggers("run-1") == []
        view.drive("run-1")
        assert host.launched == []
        report_item = next(item for item in view._reports("run-1") if item[0]["attempt_id"] == report["attempt_id"])
        observer = view._task("run-1", report_item)
        with pytest.raises(ValueError, match="round closure"):
            view.inputs("run-1", observer)
        with host.store.transaction() as tx:
            tx.close_attempt(source["attempt_id"], terminal_reason=terminal, ended_at=time.time(),
                observation={"response": {"typed_candidate": {"summary": "Actual self close"}, "output_digest": source["attempt_id"]}})
        if terminal == "FAILED":
            # A retryable failure is not the end of the expert round.
            assert view._triggers("run-1") == []
            host._record_expert_final_failure("run-1", "must.robustness", "critic", evolve, "self_exhausted")
        assert [task for task, _ in view._triggers("run-1")] == [observer]
        assert view.blocks_next_cycle("run-1", "must.robustness", 2)
        assert host.store.get_attempt(peer["attempt_id"])["state"] == "OPEN"
        inputs = view.inputs("run-1", observer)
        context = json.loads(next(item.content_bytes() for item in inputs if item.ref.endswith("--interaction-context")))
        assert context["source_report_ref"] == report["attempt_id"] + "--response"
        completed = host.record(observer, candidate=_observer("none"), inputs=inputs)
        original_facts = host.store.list_facts("run-1")
        original_attempts = host.store.list_attempts()
        # Crash boundary: the Agent closed, but the interaction is not accepted.
        host.store.close()
        host.store = StateStore(host.state_root, HostWriterCapability.issue()).initialize()
        restarted = InteractionHost(host)
        assert [task for task, _ in restarted._triggers("run-1")] == [observer]
        assert restarted.inputs("run-1", observer) == inputs
        restarted.drive("run-1")
        events = restarted._events("run-1")
        assert len(events) == 1 and events[0][1]["action"] == "observed"
        assert events[0][1]["source_attempt_id"] == completed["attempt_id"]
        assert events[0][1]["node_command_id"] == observer.command_id
        # A second reconstruction cannot replay the accepted source/control.
        host.store.close()
        host.store = StateStore(host.state_root, HostWriterCapability.issue()).initialize()
        recovered = InteractionHost(host)
        recovered.drive("run-1")
        assert recovered._events("run-1") == events
        assert recovered._triggers("run-1") == [] and not recovered.pending("run-1")
        assert not recovered.blocks_next_cycle("run-1", "must.robustness", 2)
        assert host.store.list_facts("run-1") == original_facts
        assert host.store.list_attempts() == original_attempts and host.launched == []
    finally:
        host.store.close()


def test_leader_pauses_only_peers_and_restarts_from_durable_selection(host):
    host.control(Command.PAUSE_PARTICIPANT, {"participant_control": {"action": "pause",
        "scope": {"role": "researcher", "component_id": None, "chain": None}, "reason": "operator"}})
    before = host.store.list_facts("run-1")
    view, source, _ = select(host)
    assert reasons(host) == ("leader:" + source["attempt_id"], "operator")
    assert not reasons(host, "critic", "must.robustness")
    assert view.priority_roles("run-1") == ("critic",)
    assert view.blocks_next_cycle("run-1", "must.robustness", 2)
    restarted = InteractionHost(host)
    restarted.drive("run-1")
    plan = restarted.leader_round("run-1", "must.robustness")
    assert plan["cycle"] == 2 and plan["parent_hypothesis_id"] == "hyp-2"
    assert set(plan["peer_memory_commits"]) == {"researcher"}
    assert not restarted.blocks_next_cycle("run-1", "must.robustness", 2)
    assert restarted.blocks_next_cycle("run-1", "must.robustness", 3)
    host.finish_round(cycle=2)
    restarted.drive("run-1")
    assert reasons(host) == ("operator",)
    assert restarted.leader_round("run-1", "must.robustness") is None
    assert host.store.list_facts("run-1") == before
    count = len(restarted._events("run-1"))
    restarted.drive("run-1")
    assert len(restarted._events("run-1")) == count


def test_leader_peer_memory_is_frozen_and_only_exact_next_synthesis_gets_it(host):
    view, source, _ = select(host)
    view.drive("run-1")
    synthesis = node("HYPOTHESIS_PROPOSAL", cycle=2, subkind=ResearchNodeSubkind.SYNTHESIS)
    supplied = view.leader_inputs("run-1", synthesis)
    descriptor = json.loads(supplied[0].content_bytes())
    assert descriptor["source_attempt_id"] == source["attempt_id"]
    assert descriptor["component_id"] == "must.robustness"
    assert supplied == InteractionHost(host).leader_inputs("run-1", synthesis)
    for wrong in (node("CYCLE_REPORT", cycle=2), node("HYPOTHESIS_PROPOSAL", cycle=1, subkind=ResearchNodeSubkind.SYNTHESIS),
                  node("HYPOTHESIS_PROPOSAL", role="researcher", component="primary.score", cycle=2, subkind=ResearchNodeSubkind.SYNTHESIS)):
        assert not view.leader_inputs("run-1", wrong)


def test_open_peer_agent_waits_without_spending_or_dropping_leader(host):
    view, source, _ = select(host)
    peer = host.record(node("RESULT_ANALYSIS", "researcher", "primary.score"), terminal=None)
    view.drive("run-1")
    assert view.leader_round("run-1", "must.robustness") is None
    assert view._active("run-1")["source_attempt_id"] == source["attempt_id"]
    assert not any(event["action"] == "leader_not_started" for _, event in view._events("run-1"))
    with host.store.transaction() as tx:
        tx.close_attempt(peer["attempt_id"], terminal_reason="SUCCEEDED", ended_at=3.0, observation={})
    view.drive("run-1")
    assert view.leader_round("run-1", "must.robustness") is not None


def test_already_frozen_target_cycle_closes_before_any_peer_memory_is_added(host, monkeypatch):
    waiting = node("LITERATURE_REVIEW", cycle=2, subkind=ResearchNodeSubkind.MAIN)
    host.record(waiting, terminal=None)
    view, _, _ = select(host)
    view.drive("run-1")
    assert view.leader_round("run-1", "must.robustness") is None
    assert not view.blocks_next_cycle("run-1", "must.robustness", 2)
    assert view.blocks_next_cycle("run-1", "must.robustness", 3)
    assert not view.leader_inputs("run-1", node("HYPOTHESIS_PROPOSAL", cycle=2, subkind=ResearchNodeSubkind.SYNTHESIS))


@pytest.mark.parametrize("reason", ["round_budget_exhausted", "experiment_budget_exhausted"])
def test_permanent_budget_exhaustion_releases_own_reason_without_new_round(tmp_path, reason, monkeypatch):
    host = StoredHost(tmp_path / "state", rounds=1 if reason == "round_budget_exhausted" else 4)
    view, _, _ = select(host)
    if reason == "experiment_budget_exhausted":
        monkeypatch.setattr(host, "_batch_creation_available", lambda *args: False)
    view.drive("run-1")
    assert view._active("run-1") is None and not reasons(host)
    assert view._events("run-1")[-1][1]["reason"] == reason
    host.store.close()


def test_designated_leader_final_failure_releases_pause_without_fabricated_report(host):
    view, _, _ = select(host)
    view.drive("run-1")
    task = node("LITERATURE_REVIEW", cycle=2, subkind=ResearchNodeSubkind.MAIN)
    row = host.record(task, terminal="FAILED")
    host._record_expert_final_failure("run-1", "must.robustness", "critic", task, "literature_exhausted")
    view.drive("run-1")
    assert not reasons(host) and view._active("run-1") is None
    assert view._events("run-1")[-1][1]["attempt_id"] == row["attempt_id"]
    assert not host._expert_success(node("CYCLE_REPORT", cycle=2))


def test_minimum_rounds_are_frozen_before_observer_and_disabled_has_no_effects(tmp_path):
    host = StoredHost(tmp_path / "state", minimum=2)
    view = InteractionHost(host)
    task = view._triggers("run-1")[0][0]
    context = json.loads(next(item.content_bytes() for item in view.inputs("run-1", task) if item.ref.endswith("--interaction-context")))
    assert context["allowed_actions"] == ["none"] and context["eligible_leader_components"] == []
    host.store.close()
    disabled = StoredHost(tmp_path / "disabled", enabled=False)
    view = InteractionHost(disabled)
    before = disabled.store.list_attempts()
    view.drive("run-1")
    assert not view.pending("run-1") and not view._events("run-1")
    assert disabled.store.list_attempts() == before
    disabled.store.close()


def test_shared_goal_support_suppresses_first_observer_and_leader(tmp_path):
    host = StoredHost(tmp_path / "state", shared_goal=True)
    view = InteractionHost(host)
    triggers = view._triggers("run-1")
    assert triggers
    for task, _ in triggers:
        inputs = view.inputs("run-1", task)
        context = json.loads(next(item.content_bytes() for item in inputs if item.ref.endswith("--interaction-context")))
        assert context["allowed_actions"] == ["none"]
    view.drive("run-1")
    assert not view.pending("run-1")
    assert not view._events("run-1") and not host.launched
    host.store.close()


def test_success_stop_aborts_selected_leader_without_faking_round_completion(host):
    # Controller unit seam: G3 and Oracle tests separately establish the verdict.
    view, source, _ = select(host)
    view.drive("run-1")
    host.control(Command.PAUSE_PARTICIPANT, {"participant_control": {
        "action": "pause", "scope": {"role": "researcher", "component_id": None, "chain": None}, "reason": "operator"}})
    facts = host.store.list_facts("run-1")
    with host.store.transaction() as tx:
        tx.append_transition(host._transition_record(tx, "run-1", "run-1", "RUN", Command.RECORD_GOAL_VERDICT,
            RunState.STOPPING, payload={"stop_disposition": "COMPLETED"}))
    view.stop_for_goal("run-1", "unit-verdict")
    view.stop_for_goal("run-1", "unit-verdict")
    restored = InteractionHost(host)
    assert restored._active("run-1") is None
    assert reasons(host) == ("operator",)
    events = [event for _, event in restored._events("run-1")]
    assert [event["action"] for event in events].count("leader_aborted") == 1
    assert not any(event["action"] == "leader_round_finished" for event in events)
    assert events[-1]["source_attempt_id"] == source["attempt_id"]
    assert events[-1]["goal_verdict_id"] == "unit-verdict"
    assert host.store.list_facts("run-1") == facts
