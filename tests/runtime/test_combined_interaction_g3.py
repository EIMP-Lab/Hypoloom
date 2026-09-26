"""One three-expert Host run: Leader, then Debate, with six real CPU effects.

The model/session driver is explicitly fake-agent/4. It writes deterministic
test transcript records through real AgentSessions, not native Claude. Scientific
Host admission, bwrap execution, Git/memory publication and Oracle are unmodified.
"""

import copy
import json
import time

import pytest
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import AgentRequest, WorkspaceArtifact, with_output_digest
from hypoloom.agent_sessions import AgentSessions
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.execution_workspace import GitTargetBinding
from hypoloom.expert_interaction import InteractionFacts
from hypoloom.expert_memory import ExpertMemory
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.state_store import StateTransaction
from hypoloom.workflow_host import WorkflowHost

from test_agent_sessions import _transcript
from test_expert_loop_host import _pilot_payload
from test_expert_loop_host import mount_root as mount_root
from test_profile_v4 import documents


RUN = "combined-interaction-g3"
ROLES = ("ml-researcher", "scientist-b", "scientist-c")


def _input(request, suffix):
    return json.loads(next(item.content_bytes() for item in request.input_artifacts if item.ref.endswith(suffix)))


class CombinedAgent(FakeAgent):
    def __init__(self, negative=False):
        super().__init__()
        self.negative = negative
        self.leader_selected = self.debate_selected = False

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and (self.negative or request.role == ROLES[2]):
            # Explicit signals remain unsatisfied; never weaken shared-goal
            # closing to manufacture another interaction after success.
            response = with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(item.path, item.content_bytes().replace(b'"accuracy": 0.55', b'"accuracy": 0.50'))
                for item in response.proposed_workspace_artifacts)))
        if self.negative and request.command is AgentCommand.ADJUDICATE:
            response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "best_chain": None}))
        if request.command is AgentCommand.OBSERVER:
            context = _input(request, "--interaction-context")
            reports = [json.loads(item.content_bytes()) for item in request.input_artifacts if item.ref.endswith("--response")]
            reports = [item for item in reports if item.get("typed_candidate", {}).get("task_kind") == "CYCLE_REPORT"]
            facts = InteractionFacts.from_rows(request.run_id, context["facts"], agent_aci_version=request.schema_version)
            eligible = [item for item in facts.leader_qualifications() if item.role == ROLES[0]
                and item.component_id in context["eligible_leader_components"]
                and (request.schema_version < 21 or item.hypothesis_id in context["eligible_leader_hypotheses"])]
            if not self.leader_selected and len(reports) == 3 and "leader" in context["allowed_actions"] and eligible:
                qualified = eligible[0]
                # Exercise accepted fact IDs through Host, recovery and the
                # independent Oracle; other tests retain entity-ID coverage.
                refs = [row["fact_id"] for row in facts.rows
                    if (row["fact_type"] == "HypothesisProposed" and row["hypothesis_id"] == qualified.hypothesis_id)
                    or (row["fact_type"] == "HypothesisAssessed" and row["assessment_id"] in qualified.qualification_refs)]
                response = with_output_digest(replace(response, typed_candidate={
                    "action": "leader", "leader": ROLES[0], "debate": None,
                    "supporting_refs": refs,
                    "rationale": "Use both completed peer memories in one qualified bounded Leader round."}))
                self.leader_selected = True
            elif self.leader_selected and not self.debate_selected and "debate" in context["allowed_actions"]:
                participants = [item for item in context["debate_participants"] if item["scope"]["role"] in ROLES[:2]]
                # The Leader's completed second-cycle source must actually
                # exist in this frozen Observer context before choosing Debate.
                leader_hypotheses = {json.loads(row["payload_json"])["hypothesis_id"] for row in context["facts"]
                    if row["fact_type"] == "HypothesisProposed" and json.loads(row["payload_json"]).get("parent_hypothesis_id")}
                if len(participants) == 2 and any(item["hypothesis_id"] in leader_hypotheses for item in participants):
                    response = with_output_digest(replace(response, typed_candidate={
                        "action": "debate", "leader": None,
                        "debate": {"participants": participants, "topic": "Compare the two accepted mechanisms after the Leader round."},
                        "supporting_refs": [row["fact_id"] for row in context["facts"] if row["fact_type"] == "HypothesisAssessed"],
                        "rationale": "The actual completed Leader contribution can now be debated with its peer."}))
                    self.debate_selected = True
        return response


class FakeSessionHost(WorkflowHost):
    """Test-only model transport; publish context before ordinary Host close."""

    def _finish_fake_agent(self, request, raw):
        target = self._run_config(request.run_id)["execution"]["target"]
        binding = GitTargetBinding(checkout=Path(target["checkout"]), commit=target["commit"])
        workspace = AgentSessions(self.state_root, self.publisher).prepare(request, binding)
        try:
            if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                for item in raw.proposed_workspace_artifacts:
                    if item.path != "implementation.patch":
                        path = workspace.worktree_root / item.path
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(item.content_bytes())
            if request.command is AgentCommand.CYCLE_REPORT:
                for item in raw.proposed_workspace_artifacts:
                    path = workspace.memory_root / item.path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(item.content_bytes())
            if request.command is AgentCommand.SELF_EVOLVE:
                path = workspace.plugin_update.root / "skills/experiment-report/SKILL.md"
                path.write_bytes(path.read_bytes() + f"\nTest-driver lesson from {request.command_id}.\n".encode())
            if request.command is AgentCommand.FINAL_REPORT:
                for item in raw.proposed_workspace_artifacts:
                    (workspace.final_report_root / item.path).write_bytes(item.content_bytes())
            transcript = workspace.session_root / f"{workspace.binding['session_id']}.jsonl"
            _transcript(workspace, "FAKE_SESSION_DRIVER " + request.attempt_id, append=transcript.exists())
            self.agent_session_metadata[request.attempt_id] = workspace.finish_node()
            super()._finish_fake_agent(request, raw)
        finally:
            workspace.cleanup()
            self.agent_session_metadata.pop(request.attempt_id, None)


def _payload(tmp_path, mount_root):
    payload = _pilot_payload(tmp_path, mount_root, RUN)
    contract = payload["research_contract"]
    settings = project_profile_v4(**documents()).settings
    settings["goal"]["completion_mode"] = "explicit_signals"
    settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 3
    settings["experts"]["experts"][0]["name"] = ROLES[0]
    settings["experts"]["defaults"].update(max_forks=1, max_hypotheses_per_round=1)
    settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 1}
    settings["methodology"]["interaction"] = {"leader": {"enabled": True, "check_after": "cycle_report", "min_rounds_before_leader": 1},
        "debate": {"enabled": True, "rounds": 3}}
    settings["methodology"]["graph_evolution"]["enabled"] = False
    contract["protocols"]["shared-accuracy"] = {"result_kind": "metric", "requirements": {"required_metrics": ["accuracy"]}}
    contract["components"][0]["protocol"] = "shared-accuracy"
    contract["roles"][0]["protocols"] = {"default": "shared-accuracy", "allowed": ["shared-accuracy"]}
    contract["roles"][0]["budget"]["max_experiments"] = 3
    for role in ROLES[1:]:
        contract["roles"].append({**copy.deepcopy(contract["roles"][0]), "id": role})
        component = copy.deepcopy(contract["components"][0])
        component.update(id="primary." + role, name=role, owner=role, proposers=[role])
        contract["components"].append(component)
        settings["experts"]["experts"].append({**copy.deepcopy(settings["experts"]["experts"][0]), "name": role})
    contract["methodology"]["controls"]["product"] = settings
    contract["budget"].update(max_experiments=6, max_active_agents=3, max_active_processes=2, max_active_reviews=3)
    contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE", "target": "3", "display_only": True}
    return payload


@pytest.mark.parametrize("negative", [False, True], ids=["supported", "refuted-null-selection"])
def test_three_experts_one_run_leader_then_three_round_debate_real_cpu(tmp_path, monkeypatch, mount_root, negative):
    # This fixture exercises the retained exclusive-Leader round protocol.
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 22)
    # The test transport establishes the same ordinary OPEN binding that the
    # real adapter writes. It never edits a closed row or bypasses admission.
    original_open = StateTransaction.open_attempt

    def open_attempt(tx, attempt):
        intent_key = "observation" if "observation" in attempt else "payload"
        if attempt.get("kind") == "AGENT" and attempt.get(intent_key, {}).get("request"):
            attempt = copy.deepcopy(attempt)
            request = AgentRequest.from_dict(attempt[intent_key]["request"])
            attempt[intent_key]["session_binding"] = AgentSessions.binding(request)
        inserted = original_open(tx, attempt)
        if attempt.get("kind") == "AGENT":
            observed = json.loads(tx.get_attempt(attempt["attempt_id"])["observation_json"])
            assert observed["session_binding"] == AgentSessions.binding(AgentRequest.from_dict(observed["request"]))
        return inserted

    monkeypatch.setattr(StateTransaction, "open_attempt", open_attempt)
    agent = CombinedAgent(negative)
    host = FakeSessionHost(tmp_path / "state", local_agent=agent)
    try:
        payload = _payload(tmp_path, mount_root)
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-combined", expected_revision=0,
            command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        # Six CPU outcomes still require every pending Observer and final closure.
        deadline = time.monotonic() + 600
        recovered = set()
        while time.monotonic() < deadline:
            host.tick()
            if negative and not host.running and not host.local_execution_agents and not host.store.list_attempts(state="OPEN"):
                accepted = {row["action"] for row in host.store.inspect(
                    "SELECT json_extract(payload_json,'$.expert_interaction.action') AS action "
                    "FROM workflow_transitions WHERE run_id=? AND workflow_kind='RUN'", (RUN,))}
                boundary = next((stage for stage in ("leader_round_started", "debate_restarts_designated")
                    if stage in accepted and stage not in recovered), None)
                if boundary is not None:
                    # Restart the actual Host at two committed control boundaries;
                    # the model double retains only its scripted choice sequence.
                    host.close()
                    host = FakeSessionHost(tmp_path / "state", local_agent=agent)
                    host.initialize()
                    recovered.add(boundary)
            if host.store.latest_transition(RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.005)
        attempts = host.store.list_attempts()
        (tmp_path / "attempts.json").write_text(json.dumps(attempts, indent=2))
        assert host.store.latest_transition(RUN)["state"] == "COMPLETED", attempts
        assert agent.leader_selected and agent.debate_selected
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts), attempts
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 6
        observations = {row["attempt_id"]: json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"}
        events = [(row, json.loads(row["payload_json"])["expert_interaction"]) for row in host.store.inspect(
            "SELECT * FROM workflow_transitions WHERE run_id=? AND json_type(payload_json,'$.expert_interaction')='object' ORDER BY rowid", (RUN,))]
        (tmp_path / "interactions.json").write_text(json.dumps(events, indent=2))
        actions = [event["action"] for _, event in events]
        for action in ("leader_selected", "leader_round_started", "leader_round_finished", "debate_proposed", "debate_started", "debate_verdict", "debate_restarts_designated", "debate_restarts_finished"):
            assert actions.count(action) == (2 if action == "debate_restarts_designated" else 1), actions
        assert actions.index("leader_round_finished") < actions.index("debate_started")
        debate = [value for value in observations.values() if value["request"]["command"] == "DEBATE"]
        assert len(debate) == 6
        for role in ROLES[:2]:
            rounds = [value for value in debate if value["request"]["role"] == role]
            assert {_input(AgentRequest.from_dict(value["request"]), "--debate-context")["round"] for value in rounds} == {1, 2, 3}
            assert len({value["session"]["session_id"] for value in rounds}) == 1
        designated = {"plans": [plan for _, event in events if event["action"] == "debate_restarts_designated" for plan in event["plans"]]}
        assert {(plan["role"], plan["cycle"]) for plan in designated["plans"]} == {(ROLES[0], 3), (ROLES[1], 2)}
        for plan in designated["plans"]:
            requests = [value["request"] for value in observations.values() if value["request"]["role"] == plan["role"]
                and value["request"].get("task_context", {}).get("key", {}).get("cycle") == plan["cycle"]]
            assert not any(request["command"] == "LITERATURE_REVIEW" for request in requests)
            proposal = [request for request in requests if request["command"] == "HYPOTHESIS_PROPOSAL"]
            assert {request["task_context"]["key"]["subkind"] for request in proposal} == {"FORWARD", "BACKWARD", "SYNTHESIS"}
            forward = next(request for request in proposal if request["task_context"]["key"]["subkind"] == "FORWARD")
            assert plan["source_literature_attempt_id"] + "--response" in {item["ref"] for item in forward["input_artifacts"]}
        leader = [value for value in observations.values() if any(item["ref"].endswith("--leader-resources") for item in value["request"]["input_artifacts"])]
        assert len(leader) == 1
        assert set(leader[0]["session"]["leader_resources"]["peer_memory_commits"]) == set(ROLES[1:])
        for value in observations.values():
            request = AgentRequest.from_dict(value["request"])
            resources = next((item for item in request.input_artifacts if item.ref.endswith("--expert-resources")), None)
            if resources:
                resources = json.loads(resources.content_bytes())
                assert value["session"]["expert_resources"] == resources
                ExpertPluginStore(host.state_root).binding_at(RUN, request.role, resources["plugin"]["commit"], max_bytes=1048576)
                ExpertMemory(host.state_root).files(RUN, request.role, resources["memory_commit"], max_bytes=8388608)
            if request.command is AgentCommand.CYCLE_REPORT:
                archive = host.publisher.root / value["session"]["transcript"]["path"]
                for raw in value["response"]["proposed_workspace_artifacts"]:
                    artifact = WorkspaceArtifact.from_dict(raw)
                    assert (archive / "memory" / artifact.path).read_bytes() == artifact.content_bytes()
        assert len([value for value in observations.values() if value["request"]["command"] == "FINAL_REPORT"]) == 1
        assert not host._participant_controls(RUN).pauses
        assert not host.running and not host.running_agents and not host.local_execution_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        audit = Oracle(host.store.database_path, host.publisher.root).audit(RUN)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 6
        if negative:
            assert recovered == {"leader_round_started", "debate_restarts_designated"}
            assert {row["payload"]["outcome"] for row in host.store.list_facts(RUN, "CanonicalOutcomeAccepted")} == {"CLEAN_REFUTATION"}
            assert all(host._batch_history(RUN, str(component.component_id), component.owner_role).selected_tip is None
                for component in host._research_contract(RUN).components)
    finally:
        host.close()
