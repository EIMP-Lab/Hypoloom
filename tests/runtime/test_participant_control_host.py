import json
import tempfile
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.control_protocol import ControlRequest
from hypoloom.workflow_host import WorkflowHost
from hypoloom.oracle import Oracle
from tests.runtime.test_expert_loop_host import _pilot_payload, _ReviseOnceAgent
from tests.runtime.test_host_experiment_tools import _until


def control(host, command, *, identity, role=None, component_id=None, chain=None, text=None):
    return host.handle(ControlRequest(schema_version=1, request_id=identity, expected_revision=None,
        command=command, payload={"run_id": "participant-host", "scope": {"role": role,
            "component_id": component_id, "chain": chain}, "reason": "operator",
            **({"text": text} if text is not None else {})}))


@pytest.fixture
def controlled(tmp_path):
    with tempfile.TemporaryDirectory(prefix="hypoloom-control-target-", dir="/var/tmp") as target:
        host = WorkflowHost(tmp_path / "state")
        host.initialize()
        payload = _pilot_payload(tmp_path, Path(target), "participant-host")
        accepted = host.handle(ControlRequest(schema_version=1, request_id="create-participant", expected_revision=0,
            command="CREATE_RUN", payload=payload))
        assert accepted.status == "accepted", accepted
        host.tick()
        try: yield host
        finally: host.close()


def test_pause_is_idempotent_and_prevents_new_work_without_terminal_verdict(controlled):
    host = controlled
    accepted = control(host, "PAUSE", identity="operator-pause")
    assert accepted.status == "accepted", accepted
    assert control(host, "PAUSE", identity="operator-pause") == accepted
    for _ in range(12): host.tick()
    assert host.store.latest_transition("participant-host")["state"] == "ACTIVE"
    assert not host.store.list_facts("participant-host", "GoalVerdictRecorded")
    assert not host.store.list_workflow_heads("participant-host", "EXPERIMENT")
    requests = [json.loads(row["observation_json"])["request"] for row in host.store.list_attempts() if row["kind"] == "AGENT"]
    assert all(request["task_context"].get("kind") == "AUXILIARY" for request in requests)
    status = host.handle(ControlRequest(schema_version=1, request_id="status", expected_revision=None,
        command="STATUS", payload={"run_id": "participant-host"}))
    assert status.result["participant_controls"]["pause_requested"]


def test_control_target_and_reason_are_validated_before_persistence(controlled):
    host = controlled
    before = len(host.store.inspect("SELECT * FROM workflow_commands"))
    for changes in ({"role": "unknown"}, {"component_id": "absent"}, {"chain": "unsafe/slash"}):
        assert control(host, "PAUSE", identity="bad-" + next(iter(changes)), **changes).status == "rejected"
    request = ControlRequest(schema_version=1, request_id="clear-leader", expected_revision=None, command="RESUME",
        payload={"run_id": "participant-host", "scope": {"role": None, "component_id": None, "chain": None}, "reason": "leader:1"})
    assert host.handle(request).status == "rejected"
    assert len(host.store.inspect("SELECT * FROM workflow_commands")) == before


def test_policy_paused_first_participant_does_not_starve_peer_or_finish_run():
    from dataclasses import replace
    from hypoloom.policy import ProposalSnapshot, ResearchPolicy
    from tests.runtime.test_component_policy import component, snapshot
    first = replace(component("a"), proposals=(ProposalSnapshot(role="ml", dispatch_paused=True),))
    peer = component("b", owner="systems", proposers=("systems",))
    decision = ResearchPolicy().next_command(snapshot(first, peer))
    assert decision.payload["role"] == "systems"
    assert ResearchPolicy().next_command(snapshot(first)) is None


def test_chain_pause_allows_expert_planning_but_blocks_direct_design_and_creation(controlled):
    host = controlled
    component = host._research_contract("participant-host").components[0]
    assert control(host, "PAUSE", identity="pause-chain", role=component.owner_role,
        component_id=str(component.component_id), chain="1").status == "accepted"
    _until(host, lambda: bool(host.store.list_facts("participant-host", "HypothesisProposed")))
    for _ in range(4): host.tick()
    assert not host.store.list_workflow_heads("participant-host", "EXPERIMENT")
    assert not any(json.loads(row["observation_json"])["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"
        for row in host.store.list_attempts() if row["kind"] == "AGENT")
    from hypoloom.expert_loop import ResearchNodeKey, ResearchNodeKind
    # Current batch dispatch selects an explicit chain. The whole-run view
    # intentionally cannot manufacture a legacy, chain-less CREATE decision.
    key = ResearchNodeKey(run_id="participant-host", component_id=str(component.component_id),
        expert_slot=component.owner_role, cycle=1, chain="1", experiment_ordinal=1,
        node_kind=ResearchNodeKind.EXPERIMENT_EXECUTION)
    snapshot = host._policy_snapshot(host.store.latest_transition("participant-host"), chain_context=key)
    from hypoloom.policy import ResearchPolicy
    unpaused = replace(snapshot, components=tuple(replace(item, creation_paused=False) for item in snapshot.components))
    decision = ResearchPolicy().next_command(unpaused)
    assert decision.command == "CREATE_EXPERIMENT"
    decision = replace(decision, payload={**decision.payload, "chain": key.chain})
    assert host._create_experiment("participant-host", decision) is None
    assert control(host, "RESUME", identity="resume-chain", role=component.owner_role,
        component_id=str(component.component_id), chain="1").status == "accepted"
    _until(host, lambda: host.store.latest_transition("participant-host")["state"] == "COMPLETED")
    report = Oracle(host.store.database_path, host.publisher.root).audit("participant-host")
    assert report.ok, report.to_dict()


def test_pause_does_not_stop_an_already_started_literature_cycle(controlled):
    host = controlled
    _until(host, lambda: any(json.loads(row["observation_json"])["request"]["task_context"].get("kind") == "RESEARCH_NODE"
        for row in host.store.list_attempts() if row["kind"] == "AGENT"))
    assert control(host, "PAUSE", identity="pause-during-planning").status == "accepted"
    _until(host, lambda: bool(host.store.list_facts("participant-host", "HypothesisProposed")))
    assert not host.store.list_workflow_heads("participant-host", "EXPERIMENT")
    report = Oracle(host.store.database_path, host.publisher.root).audit("participant-host")
    assert report.ok, report.to_dict()


def test_pause_between_execution_open_and_initial_submit_keeps_host_responsive(controlled):
    host = controlled
    _until(host, lambda: bool(host.local_execution_agents))
    assert control(host, "PAUSE", identity="pause-before-submit").status == "accepted"
    for _ in range(4): host.tick()
    assert not host.store.list_workflow_heads("participant-host", "EXPERIMENT")
    assert host.local_execution_agents
    assert control(host, "RESUME", identity="resume-before-submit").status == "accepted"
    _until(host, lambda: host.store.latest_transition("participant-host")["state"] == "COMPLETED")
    assert Oracle(host.store.database_path, host.publisher.root).audit("participant-host").ok


def test_pause_during_real_cpu_survives_host_recovery_and_drains_analysis_before_next_cycle(tmp_path):
    with tempfile.TemporaryDirectory(prefix="hypoloom-pause-cpu-", dir="/var/tmp") as target:
        agent = _ReviseOnceAgent()
        for name in ("model_identity", "adapter_version", "permission_profile"): setattr(agent, name, getattr(agent.delegate, name))
        host = WorkflowHost(tmp_path / "state", local_agent=agent)
        host.initialize()
        payload = _pilot_payload(tmp_path, Path(target), "participant-host")
        payload["execution"]["delay_seconds"] = 0.3
        assert host.handle(ControlRequest(schema_version=1, request_id="create-cpu", expected_revision=0,
            command="CREATE_RUN", payload=payload)).status == "accepted"
        recovered = None
        try:
            _until(host, lambda: bool(host.running))
            running_id = next(iter(host.running))
            assert control(host, "PAUSE", identity="pause-running").status == "accepted"
            assert control(host, "INJECT", identity="note-analysis", text="Explain the observed baseline before revising.").status == "accepted"
            host.close()
            recovered = WorkflowHost(tmp_path / "state", local_agent=agent)
            recovered.initialize()
            assert recovered.store.get_attempt(running_id)["terminal_reason"] == "LOST"
            _until(recovered, lambda: any(json.loads(row["observation_json"])["request"]["command"] == "CYCLE_REPORT"
                and row["terminal_reason"] == "SUCCEEDED" for row in recovered.store.list_attempts() if row["kind"] == "AGENT"))
            for _ in range(8): recovered.tick()
            assert recovered.store.latest_transition("participant-host")["state"] == "ACTIVE"
            outcomes = recovered.store.list_facts("participant-host", "CanonicalOutcomeAccepted")
            assert len(outcomes) == 1 and outcomes[0]["payload"]["outcome"] == "CLEAN_REFUTATION"
            requests = [json.loads(row["observation_json"])["request"] for row in recovered.store.list_attempts() if row["kind"] == "AGENT"]
            assert all(item["task_context"].get("key", {}).get("cycle", 1) == 1 for item in requests)
            notes = [(item["command_id"], item["command"]) for item in requests
                if any(source["ref"] == "note-analysis" for source in item["input_artifacts"])]
            assert notes and len({item[0] for item in notes}) == 1
            assert control(recovered, "RESUME", identity="resume-running").status == "accepted"
            _until(recovered, lambda: recovered.store.latest_transition("participant-host")["state"] == "COMPLETED")
            assert len(recovered.store.list_facts("participant-host", "CanonicalOutcomeAccepted")) == 2
            report = Oracle(recovered.store.database_path, recovered.publisher.root).audit("participant-host")
            assert report.ok, report.to_dict()
        finally:
            (recovered or host).close()


def test_injected_note_is_frozen_across_lost_retry_and_new_note_waits_for_next_task(controlled):
    host = controlled
    from hypoloom.agent_aci import AgentRequest
    assert control(host, "INJECT", identity="before-node", text="Read this once before proposing.").status == "accepted"
    original_crash = host._crash
    def crash(point):
        if point == "after_agent_intent_before_start":
            row = host.store.list_attempts()[-1]
            if json.loads(row["observation_json"])["request"]["task_context"].get("kind") == "RESEARCH_NODE":
                raise KeyboardInterrupt("fresh fixture Host loss")
    host._crash = crash
    try:
        for _ in range(8): host.tick()
    except KeyboardInterrupt:
        pass
    else:
        pytest.fail("fixture did not cross its durable OPEN Agent boundary")
    source = host.store.list_attempts()[-1]
    frozen = json.loads(source["observation_json"])["request"]
    assert any(item["ref"] == "before-node" for item in frozen["input_artifacts"])
    host._crash = original_crash
    host.close()
    recovered = WorkflowHost(host.state_root)
    try:
        recovered.initialize()
        assert control(recovered, "INJECT", identity="after-node", text="Use this on the following node.").status == "accepted"
        _until(recovered, lambda: len(recovered.store.list_attempts(command_id=source["command_id"])) == 2)
        rows = recovered.store.list_attempts(command_id=source["command_id"])
        retry = json.loads(rows[-1]["observation_json"])["request"]
        assert retry["immutable_context_refs"] == frozen["immutable_context_refs"]
        assert retry["input_artifacts"] == frozen["input_artifacts"]
        _until(recovered, lambda: any(any(item["ref"] == "after-node" for item in json.loads(row["observation_json"])["request"]["input_artifacts"])
            for row in recovered.store.list_attempts() if row["kind"] == "AGENT"))
        task = AgentRequest.from_dict(retry).task_context
        assert not any(item.ref == "after-node" for item in recovered._operator_note_inputs("participant-host", task, retry["role"], retry["component_id"]))
    finally:
        recovered.close()


@pytest.mark.parametrize("damage,code", [("note-bytes", "operator_note_binding_mismatch"),
    ("pause-window", "participant_paused_dispatch"), ("control-owner", "participant_control_binding_mismatch")])
def test_independent_oracle_rejects_rehashed_control_and_input_drift(controlled, tmp_path, damage, code):
    from hypoloom.agent_aci import AgentRequest, AgentResponse, EmbeddedInput, request_digest, with_output_digest
    host = controlled
    assert control(host, "PAUSE", identity="initial-pause").status == "accepted"
    assert control(host, "INJECT", identity="inspect-baseline", text="Inspect the baseline sources.").status == "accepted"
    assert control(host, "RESUME", identity="initial-resume").status == "accepted"
    _until(host, lambda: host.store.latest_transition("participant-host")["state"] == "COMPLETED")
    assert Oracle(host.store.database_path, host.publisher.root).audit("participant-host").ok
    source = sqlite3.connect(f"file:{host.store.database_path}?mode=ro", uri=True)
    copy = sqlite3.connect(tmp_path / "tampered.sqlite")
    source.backup(copy); source.close()
    copy.execute("DROP TRIGGER execution_attempt_update_rules")
    copy.execute("DROP TRIGGER immutable_workflow_transitions_update")
    copy.execute("DROP TRIGGER immutable_workflow_commands_update")
    if damage == "note-bytes":
        for identity, raw in copy.execute("SELECT attempt_id,observation_json FROM execution_attempts WHERE kind='AGENT'"):
            observation = json.loads(raw); request = AgentRequest.from_dict(observation["request"])
            note = next((item for item in request.input_artifacts if item.ref == "inspect-baseline"), None)
            if note is None: continue
            changed = EmbeddedInput.from_bytes(note.ref, note.media_type,
                json.dumps({**json.loads(note.content_bytes()), "operator_instruction": "Invented replacement."}).encode())
            request = replace(request, input_artifacts=tuple(changed if item.ref == note.ref else item for item in request.input_artifacts))
            observation["request"] = request.to_dict()
            observation["response"] = with_output_digest(replace(AgentResponse.from_dict(observation["response"]), input_digest=request_digest(request))).to_dict()
            copy.execute("UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?", (json.dumps(observation), identity))
    elif damage == "pause-window":
        row = copy.execute("SELECT payload_json FROM workflow_transitions WHERE command_id='initial-resume'").fetchone()
        payload = json.loads(row[0]); payload["participant_control"]["action"] = "pause"
        copy.execute("UPDATE workflow_transitions SET payload_json=? WHERE command_id='initial-resume'", (json.dumps(payload),))
        request = json.loads(copy.execute("SELECT request_json FROM workflow_commands WHERE command_id='initial-resume'").fetchone()[0])
        request["command"] = "PAUSE"
        copy.execute("UPDATE workflow_commands SET request_json=?,command_type='PAUSE_PARTICIPANT' WHERE command_id='initial-resume'", (json.dumps(request),))
    else:
        copy.execute("UPDATE workflow_commands SET workflow_id='another-owner' WHERE command_id='inspect-baseline'")
    copy.commit(); copy.close()
    report = Oracle(tmp_path / "tampered.sqlite", host.publisher.root).audit("participant-host")
    assert code in {issue.code for issue in report.issues}, report.to_dict()
    assert not any("digest" in issue.code or issue.code == "agent_postflight_invalid" for issue in report.issues), report.to_dict()
