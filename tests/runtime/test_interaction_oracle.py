from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeSubkind
from hypoloom.interaction_oracle import audit_interactions
from hypoloom.state_store import HostWriterCapability, StateStore
from test_interaction_host import StoredHost, node, select


@pytest.mark.parametrize("damage", [None, "foreign-parent", "uncited-parent", "premature-cycle"])
@pytest.mark.parametrize("fact_refs", [False, True])
def test_negative_leader_requires_its_exact_closed_contribution(tmp_path, damage, fact_refs):
    from hypoloom.interaction_host import InteractionHost
    from test_expert_interaction import _observer
    host = StoredHost(tmp_path / "state", agent_version=21, selected=False, negative=True)
    try:
        view = InteractionHost(host)
        task = view._triggers("run-1")[0][0]
        refs = ["fact-hyp-2", "fact-assessment-2"] if fact_refs else ["hyp-2", "out-2"]
        host.record(task, inputs=view.inputs("run-1", task),
            candidate={**_observer(), "supporting_refs": refs})
        view.drive("run-1")
        view.drive("run-1")
        data = RawRecords(host)
    finally:
        host.store.close()
    assert data.audit() == []
    plan = data.event("leader_round_started")["payload"]["expert_interaction"]["leader"]
    if damage == "foreign-parent":
        plan["parent_hypothesis_id"] = "hyp-1"
    elif damage == "uncited-parent":
        data.event("leader_selected")["payload"]["expert_interaction"]["proposal"]["supporting_refs"].remove(refs[0])
    elif damage == "premature-cycle":
        plan["cycle"] += 1
    if damage is not None:
        assert data.audit()


class RawRecords:
    def __init__(self, host):
        self.root = host.state_root
        self.attempts = list(host.store.list_attempts())
        self.observations = {row["attempt_id"]: json.loads(row["observation_json"] or "{}") for row in self.attempts}
        self.facts = list(host.store.list_facts("run-1"))
        self.payloads = {row["fact_id"]: row["payload"] for row in self.facts}
        self.transitions = list(host.store.inspect("SELECT t.*,c.command_type FROM workflow_transitions t JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.run_id='run-1' ORDER BY t.revision"))
        for row in self.transitions:
            row["payload"] = json.loads(row["payload_json"])

    def audit(self):
        return audit_interactions(self.root, self.attempts, self.observations, self.facts, self.payloads, self.transitions)

    def observer_id(self):
        return next(identity for identity, value in self.observations.items() if value.get("request", {}).get("command") == "OBSERVER")

    def event(self, action):
        return next(row for row in self.transitions if row["payload"].get("expert_interaction", {}).get("action") == action)

    def edit_input(self, identity, suffix, mutate):
        request = self.observations[identity]["request"]
        item = next(item for item in request["input_artifacts"] if item["ref"].endswith(suffix))
        content = json.loads(EmbeddedInput.from_dict(item).content_bytes())
        mutate(content)
        item.update(EmbeddedInput.from_bytes(item["ref"], "application/json", json.dumps(content).encode()).to_dict())


def fixture(tmp_path, *, finished=False, none=False):
    host = StoredHost(tmp_path / "state")
    delegate, source, _ = select(host, action="none" if none else "leader")
    if not none:
        delegate.drive("run-1")
        task = node("HYPOTHESIS_PROPOSAL", cycle=2, subkind=ResearchNodeSubkind.SYNTHESIS)
        host.record(task, inputs=delegate.leader_inputs("run-1", task))
        if finished:
            host.finish_round(cycle=2)
            delegate.drive("run-1")
    raw = RawRecords(host)
    host.store.close()
    return raw


@pytest.mark.parametrize("state", ["none", "started", "finished"])
def test_raw_sqlite_git_interaction_rows_are_independently_readable(tmp_path, state):
    data = fixture(tmp_path, none=state == "none", finished=state == "finished")
    before = copy.deepcopy((data.attempts, data.observations, data.facts, data.transitions))
    assert data.audit() == []
    assert (data.attempts, data.observations, data.facts, data.transitions) == before


@pytest.mark.parametrize("damage", [None, "missing", "duplicate", "unadmitted", "changed", "clarification", "later"])
def test_observer_binds_final_grounding_after_an_unreferenced_clarification(tmp_path, damage):
    host = StoredHost(tmp_path / "state")
    try:
        task = AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id="run-1")
        earlier = host._expert_success(task)
        final = host.record(task, candidate={"summary": "Final clarified background"})
        select(host, action="none")
        data = RawRecords(host)
    finally:
        host.store.close()
    data.observations[earlier["attempt_id"]]["response"]["typed_candidate"] = {
        "clarification": {"question": "Which evaluation scope?"},
    }
    identity = data.observer_id()
    request = data.observations[identity]["request"]
    ref = final["attempt_id"] + "--response"
    item = next(item for item in request["input_artifacts"] if item["ref"] == ref)
    assert earlier["attempt_id"] + "--response" not in request["immutable_context_refs"]
    if damage == "missing":
        request["input_artifacts"].remove(item)
    elif damage == "duplicate":
        request["input_artifacts"].append(copy.deepcopy(item))
    elif damage == "unadmitted":
        request["immutable_context_refs"].remove(ref)
    elif damage == "changed":
        data.edit_input(identity, ref, lambda value: value.update(typed_candidate={"summary": "Another background"}))
    elif damage == "clarification":
        response = data.observations[earlier["attempt_id"]]["response"]
        data.edit_input(identity, ref, lambda value: value.update(response))
        data.observations[final["attempt_id"]]["response"] = copy.deepcopy(response)
    elif damage == "later":
        observer = next(row for row in data.attempts if row["attempt_id"] == identity)
        next(row for row in data.attempts if row["attempt_id"] == final["attempt_id"])["ended_at"] = observer["started_at"] + 1
    issues = data.audit()
    if damage is None:
        assert issues == []
    else:
        assert any(code == "interaction_source_invalid" for code, _, _ in issues), issues


def closed_round_fixture(tmp_path, *, check_after="expert_round", terminal="SUCCEEDED"):
    host = StoredHost(tmp_path / "state", pending_self=True, check_after=check_after)
    try:
        task = node("SELF_EVOLVE")
        source = host._expert_attempts(task)[0]
        with host.store.transaction() as tx:
            tx.close_attempt(source["attempt_id"], terminal_reason=terminal, ended_at=time.time(),
                observation={"response": {"typed_candidate": {"summary": "Actual self close"}, "output_digest": source["attempt_id"]}})
        if terminal == "FAILED":
            host._record_expert_final_failure("run-1", "must.robustness", "critic", task, "self_exhausted")
        # Read the same durable closure after reopening the actual SQLite store.
        host.store.close()
        host.store = StateStore(host.state_root, HostWriterCapability.issue()).initialize()
        select(host, action="none")
        return RawRecords(host), source["attempt_id"]
    finally:
        host.store.close()


@pytest.mark.parametrize("check_after", ["expert_round", "cycle_report"])
@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED"])
def test_observer_actual_own_round_closure_survives_store_reopen(tmp_path, check_after, terminal):
    data, source_id = closed_round_fixture(tmp_path, check_after=check_after, terminal=terminal)
    assert data.audit() == []
    source = next(row for row in data.attempts if row["attempt_id"] == source_id)
    assert source["state"] == "CLOSED" and source["terminal_reason"] == terminal
    peer = next(row for row in data.attempts if row["kind"] == "AGENT"
        and data.observations[row["attempt_id"]]["request"]["command"] == "SELF_EVOLVE"
        and data.observations[row["attempt_id"]]["request"]["role"] == "researcher")
    assert peer["state"] == "OPEN"  # A global barrier is not the product contract.


@pytest.mark.parametrize("tamper", ["open_self", "later_self", "other_role", "other_component", "other_cycle", "other_report"])
def test_observer_cannot_substitute_unclosed_or_unrelated_round(tmp_path, tamper):
    data, source_id = closed_round_fixture(tmp_path)
    assert data.audit() == []
    source = next(row for row in data.attempts if row["attempt_id"] == source_id)
    request = data.observations[source_id]["request"]
    observer_id = data.observer_id()
    observer = next(row for row in data.attempts if row["attempt_id"] == observer_id)
    if tamper == "open_self":
        source.update(state="OPEN", ended_at=None, terminal_reason=None)
    elif tamper == "later_self":
        source["ended_at"] = observer["started_at"] + 1
    elif tamper == "other_role":
        request["role"] = request["task_context"]["key"]["expert_slot"] = "researcher"
    elif tamper == "other_component":
        request["component_id"] = request["task_context"]["key"]["component_id"] = "primary.score"
    elif tamper == "other_cycle":
        request["task_context"]["key"]["cycle"] = 2
    else:
        peer_report = next(identity for identity, value in data.observations.items()
            if value.get("request", {}).get("command") == "CYCLE_REPORT" and value["request"]["role"] == "researcher")
        data.edit_input(observer_id, "--interaction-context", lambda value: value.update(source_report_ref=peer_report + "--response"))
    issues = data.audit()
    assert any(code == "interaction_source_invalid" and "closed expert round" in message
        for code, message, _ in issues), issues


@pytest.mark.parametrize("tamper", ["missing_final_failure", "other_failure_source", "later_final_failure"])
def test_failed_self_requires_real_pre_observer_final_failure_control(tmp_path, tamper):
    data, source_id = closed_round_fixture(tmp_path, terminal="FAILED")
    assert data.audit() == []
    failure = next(row for row in data.transitions
        if row["payload"].get("agent_final_failure") and row["payload"]["attempt_id"] == source_id)
    if tamper == "missing_final_failure":
        data.transitions.remove(failure)
    elif tamper == "other_failure_source":
        failure["payload"]["attempt_id"] = next(identity for identity, value in data.observations.items()
            if value.get("request", {}).get("command") == "SELF_EVOLVE" and value["request"]["role"] == "researcher")
    else:
        observer = next(row for row in data.attempts if row["attempt_id"] == data.observer_id())
        failure["created_at"] = datetime.fromtimestamp(observer["started_at"] + 1, timezone.utc).isoformat()
    issues = data.audit()
    assert any(code == "interaction_source_invalid" and
        ("closed expert round" in message or "real failed Attempt" in message)
        for code, message, _ in issues), issues


@pytest.mark.parametrize("field,value", [("component_id", "primary.score"), ("role", "researcher")])
def test_accepted_leader_cannot_change_its_qualified_component_or_owner(tmp_path, field, value):
    data = fixture(tmp_path)
    data.event("leader_selected")["payload"]["expert_interaction"]["leader"][field] = value
    assert data.audit()


@pytest.mark.parametrize("tamper", ["fake_fact", "stale_report", "minimum_round", "disabled_action", "plugin_owner", "plugin_commit"])
def test_observer_frozen_sources_are_not_candidate_claims(tmp_path, tamper):
    data = fixture(tmp_path)
    identity = data.observer_id()
    if tamper == "fake_fact":
        data.edit_input(identity, "--interaction-context", lambda value: value["facts"][0].update(payload_json="{}"))
    elif tamper == "stale_report":
        data.edit_input(identity, "--interaction-context", lambda value: value.update(source_report_ref="absent--response"))
    elif tamper == "minimum_round":
        data.edit_input(identity, "--interaction-context", lambda value: value.update(eligible_leader_components=[]))
    elif tamper == "disabled_action":
        data.edit_input(identity, "--interaction-context", lambda value: value.update(allowed_actions=["none"]))
    elif tamper == "plugin_owner":
        data.edit_input(identity, "--grounding-resources", lambda value: value["plugin"].update(role="critic"))
    else:
        data.edit_input(identity, "--grounding-resources", lambda value: value["plugin"].update(commit="a" * 40))
    assert data.audit()


@pytest.mark.parametrize("tamper", ["parent", "cycle", "peer", "commit", "source", "duplicate", "budget"])
def test_designated_round_requires_actual_parent_budget_and_peer_git(tmp_path, tamper):
    data = fixture(tmp_path)
    row = data.event("leader_round_started")
    event = row["payload"]["expert_interaction"]
    plan = event["leader"]
    if tamper == "parent":
        plan["parent_hypothesis_id"] = "hyp-1"
    elif tamper == "cycle":
        plan["cycle"] = 3
    elif tamper == "peer":
        plan["peer_memory_commits"]["critic"] = plan["peer_memory_commits"]["researcher"]
    elif tamper == "commit":
        plan["peer_memory_commits"]["researcher"] = "a" * 40
    elif tamper == "source":
        event["source_attempt_id"] = "invented-observer"
    elif tamper == "duplicate":
        data.transitions.append(copy.deepcopy(row))
    else:
        goal = next(value for value in data.payloads.values() if value["fact_type"] == "GoalDefined")
        goal["research_contract"]["methodology"]["controls"]["product"]["goal"]["stop_conditions"]["max_rounds_per_expert"] = 1
    assert data.audit()


@pytest.mark.parametrize("tamper", ["pause_reason", "pause_target", "missing_pause", "release_reason", "missing_report", "duplicate_release"])
def test_pause_and_release_are_source_owned_and_wait_for_actual_round(tmp_path, tamper):
    data = fixture(tmp_path, finished=True)
    pauses = [row for row in data.transitions if row["payload"].get("interaction_source_attempt_id")]
    if tamper == "pause_reason":
        pauses[0]["payload"]["participant_control"]["reason"] = "operator"
    elif tamper == "pause_target":
        pauses[0]["payload"]["participant_control"]["scope"].update(role="critic", component_id="must.robustness")
    elif tamper == "missing_pause":
        data.transitions.remove(pauses[0])
    elif tamper == "release_reason":
        data.event("leader_round_finished")["payload"]["participant_control"]["reason"] = "operator"
    elif tamper == "missing_report":
        identity = next(identity for identity, value in data.observations.items()
            if value.get("request", {}).get("command") == "CYCLE_REPORT" and value["request"]["task_context"]["key"]["cycle"] == 2)
        next(row for row in data.attempts if row["attempt_id"] == identity)["terminal_reason"] = "FAILED"
    else:
        data.transitions.append(copy.deepcopy(data.event("leader_round_finished")))
    assert data.audit()


@pytest.mark.parametrize("tamper", ["another_node", "another_owner", "another_commit", "session_differs", "missing_binding"])
def test_peer_memory_mount_is_only_the_actual_designated_synthesis(tmp_path, tamper):
    data = fixture(tmp_path)
    identity = next(identity for identity, value in data.observations.items() if value.get("request", {}).get("command") == "HYPOTHESIS_PROPOSAL")
    observation = data.observations[identity]
    request = observation["request"]
    if tamper == "another_node":
        request["task_context"]["key"]["subkind"] = "FORWARD"
    elif tamper == "another_owner":
        request["role"] = "researcher"
    elif tamper == "another_commit":
        data.edit_input(identity, "--leader-resources", lambda value: value["peer_memory_commits"].update(researcher="b" * 40))
    elif tamper == "session_differs":
        observation["session"] = {"leader_resources": {}}
    else:
        request["input_artifacts"] = []
    assert data.audit()


@pytest.mark.parametrize("tamper", [None, "source", "component", "cycle", "parent", "missing_selection", "missing_binding"])
def test_batch_reopening_requires_an_actual_leader_control_not_terminated_update_rewrite(tmp_path, tamper):
    from hypoloom.batch_oracle import _Audit
    data = fixture(tmp_path)
    request = next(value["request"] for value in data.observations.values() if value.get("request", {}).get("command") == "HYPOTHESIS_PROPOSAL")
    if tamper in {"source", "component", "cycle", "parent"}:
        event = data.event("leader_round_started")["payload"]["expert_interaction"]
        if tamper == "source":
            event["source_attempt_id"] = "absent"
        else:
            field, value = {"component": ("component_id", "primary.score"), "cycle": ("cycle", 3), "parent": ("parent_hypothesis_id", "hyp-1")}[tamper]
            event["leader"][field] = value
    elif tamper == "missing_selection":
        data.transitions.remove(data.event("leader_selected"))
    elif tamper == "missing_binding":
        request["input_artifacts"] = []
    audit = _Audit("run-1", data.attempts, data.observations, data.facts, data.payloads, data.transitions, ())
    if tamper == "missing_binding":
        assert not audit.leader_reopen(request, "hyp-2", len(data.transitions) + 1)
    elif tamper:
        with pytest.raises(ValueError):
            audit.leader_reopen(request, "hyp-2", len(data.transitions) + 1)
    else:
        assert audit.leader_reopen(request, "hyp-2", len(data.transitions) + 1)
