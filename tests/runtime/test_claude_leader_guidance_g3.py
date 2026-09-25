"""Native CLI/local model, three independent researchers and real CPU outcomes."""
import copy
import json
import secrets
import time
from dataclasses import replace

import pytest

from hypoloom.agent_aci import with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_interaction import InteractionFacts
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude, _tool_results, _result_text
from test_claude_leader_host_g3 import _LeaderHostScenario, _LeaderCandidateAgent, _ROLES
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _payload
from test_expert_memory_g3 import _cycle
from test_full_cc_host_g3 import _new_host, _start_provider


class AdviceAgent(_LeaderCandidateAgent):
    def __init__(self):
        super().__init__()
        self.all_refuted = True

    def invoke(self, request):
        # Select from the first completed contribution, without waiting for peers.
        if request.attempt_id in self.cache:
            return self.cache[request.attempt_id]
        prior = self.selected
        self.selected = True
        result = super().invoke(request)
        self.selected = prior
        if request.command is AgentCommand.OBSERVER and not self.selected:
            context = json.loads(next(item.content_bytes() for item in request.input_artifacts
                if item.ref.endswith("--interaction-context")))
            if "leader" in context["allowed_actions"]:
                facts = InteractionFacts.from_rows(request.run_id, context["facts"], agent_aci_version=request.schema_version)
                qualified = next(item for item in facts.leader_qualifications()
                    if item.hypothesis_id in context["eligible_leader_hypotheses"])
                result = with_output_digest(replace(result, typed_candidate={
                    "action": "leader", "leader": qualified.role, "debate": None,
                    "supporting_refs": [qualified.hypothesis_id, *qualified.qualification_refs],
                    "rationale": "Share the measured control and its protocol limits while peers continue."}))
                self.selected = True
        if request.command is AgentCommand.LEADER:
            result = with_output_digest(replace(result, typed_candidate={"task_kind": "LEADER",
                "summary": "A controlled measurement is available. Adopt, adapt or reject its implementation; test the declared evaluator before generalizing.",
                "supporting_refs": [item.ref for item in request.input_artifacts if item.ref.endswith("--leader-context")]}))
        if request.command is AgentCommand.HYPOTHESIS_UPDATE:
            result = with_output_digest(replace(result, typed_candidate={**result.typed_candidate,
                "update_disposition": "REVISE_NEXT_CYCLE" if _cycle(request) < 3 else "TERMINATE_LINEAGE"}))
        if request.command is AgentCommand.HYPOTHESIS_PROPOSAL and _cycle(request) > 1:
            candidate = copy.deepcopy(result.typed_candidate)
            treatment = dict(zip(_ROLES, ("Adopt the control", "Adapt the control", "Reject the proposed mechanism based on the local refutation")))[request.role]
            if "summary" in candidate: candidate["summary"] = treatment
            for member in candidate.get("hypotheses", []):
                member["candidate"]["claim"] += " " + treatment
            result = with_output_digest(replace(result, typed_candidate=candidate))
        self.cache[request.attempt_id] = result
        return result


class AdviceScenario(_LeaderHostScenario):
    def __init__(self, root):
        super().__init__(root)
        self.reviser = AdviceAgent()
        self.advice_evidence = {}

    def _expected_outcome(self, request):
        return "CLEAN_REFUTATION"

    def record(self, document):
        number = super().record(document)
        request = self.current
        if request.command is AgentCommand.LEADER and self.steps[request.attempt_id] == 2:
            result = _tool_results(document)["probe_" + request.attempt_id[-16:] + "__advice_sources"]
            assert not result.get("is_error"), result
            self.advice_evidence[request.attempt_id] = _result_text(result)
        return number

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request.command is AgentCommand.LEADER:
            if self.steps[request.attempt_id] == 1:
                script = ("import json, os, time\nfrom pathlib import Path\n"
                    "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                    "assert not Path('/peer-memory').exists()\nassert not Path('/memory').exists()\n"
                    "sources = [json.loads(p.read_text()) for p in Path('/inputs').glob('*.txt')]\n"
                    "assert any('source_observer_attempt_id' in s and s['facts'] for s in sources)\n"
                    "assert any(s.get('typed_candidate', {}).get('task_kind') == 'CYCLE_REPORT' for s in sources)\n"
                    "print('ACTUAL_REPORTS_AND_FACTS_READONLY')\ntime.sleep(5)\n")
                return (("advice_sources", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 15000}),)
            return self._structured(request)
        if request.command is AgentCommand.SELF_EVOLVE and _cycle(request) >= 2 and self.steps[request.attempt_id] >= 3:
            return self._structured(request)
        return super()._unscoped_tool_calls(number)


@pytest.mark.parametrize("cancel", [False, True], ids=["complete", "operator-stop"])
def test_native_advice_keeps_three_experts_independent_and_records_real_consumers(tmp_path, cancel):
    executable = _installed_claude()
    scenario = AdviceScenario(tmp_path)
    key = "local-only-advice-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    run_id = "native-advice-g3"
    try:
        payload = _payload(tmp_path, run_id)
        contract = payload["research_contract"]
        settings = contract["methodology"]["controls"]["product"]
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 3
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        settings["methodology"]["interaction"] = {"leader": {"enabled": True, "check_after": "cycle_report", "min_rounds_before_leader": 1},
            "debate": {"enabled": False, "rounds": 3}, "observer": {"coalesce_reports": True}}
        contract["budget"].update(max_experiments=9, max_active_agents=4)
        contract["protocols"]["shared-accuracy"] = {"result_kind": "metric", "requirements": {"required_metrics": ["accuracy"]}}
        contract["components"][0]["protocol"] = "shared-accuracy"
        contract["roles"][0]["protocols"] = {"default": "shared-accuracy", "allowed": ["shared-accuracy"]}
        for role in _ROLES[1:]:
            contract["roles"].append({**copy.deepcopy(contract["roles"][0]), "id": role})
            component = copy.deepcopy(contract["components"][0])
            component.update(id="primary." + role, name=role, owner=role, proposers=[role])
            contract["components"].append(component)
            settings["experts"]["experts"].append({**copy.deepcopy(settings["experts"]["experts"][0]), "name": role})
        contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE", "target": "3", "display_only": True}
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-advice", expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 750
        stop_sent = False
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if cancel and not stop_sent and any(handle.request.command is AgentCommand.LEADER
                    and scenario.steps.get(handle.request.attempt_id, 0) >= 1 for handle in host.running_agents.values()):
                stopped = host.handle(ControlRequest(schema_version=1, request_id="stop-advice",
                    expected_revision=host.store.latest_transition(run_id)["revision"], command="CANCEL",
                    payload={"run_id": run_id, "cause": "operator cancellation fixture"}))
                assert stopped.status == "accepted", stopped
                stop_sent = True
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(0.01)
        if cancel:
            assert stop_sent and host.store.latest_transition(run_id)["state"] == "CANCELLED"
            attempts = host.store.list_attempts()
            assert all(row["state"] == "CLOSED" for row in attempts)
            events = [json.loads(row["payload_json"]).get("expert_interaction", {}) for row in host.store.inspect(
                "SELECT payload_json FROM workflow_transitions WHERE run_id=?", (run_id,))]
            assert sum(event.get("action") == "leader_aborted" and event.get("reason") == "RUN_STOPPING" for event in events) == 1
            assert not any(event.get("terminal_reason") == "GUIDANCE_ACCEPTED" for event in events)
            assert host.interaction._active(run_id) is None
            assert not host.running and not host.running_agents
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
            assert audit.ok, audit.to_dict()
            assert audit.open_attempt_count == audit.pending_effect_count == 0
            assert not list(host.state_root.rglob('*.sock'))
            return
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
        leader, observed = next((row, value) for row, value in agents if value["request"]["command"] == "LEADER")
        assert sum(value["request"]["command"] == "LEADER" for _, value in agents) == 1
        assert len(scenario.advice_evidence) == 1
        assert observed["session"]["parent_session_id"] is None
        peers = set(_ROLES) - {observed["request"]["role"]}
        progressed = {value["request"]["role"] for row, value in agents
            if leader["started_at"] < row["started_at"] < leader["ended_at"]
            and value["request"]["task_context"]["kind"] == "RESEARCH_NODE"}
        assert peers <= progressed
        consumers = [value for _, value in agents if any(item["ref"] == leader["attempt_id"] + "--leader-guidance"
            for item in value["request"]["input_artifacts"])]
        assert {value["request"]["role"] for value in consumers} == set(_ROLES)
        for role in _ROLES:
            assert {value["request"]["task_context"]["key"]["subkind"] for value in consumers if value["request"]["role"] == role} == {"FORWARD", "BACKWARD", "SYNTHESIS"}
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 9
        assert len(scenario.local_tests) == len(scenario.execution_results) == 9
        events = [json.loads(row["payload_json"]) for row in host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=?", (run_id,))]
        assert not any("participant_control" in event for event in events)
        assert sum(event.get("expert_interaction", {}).get("terminal_reason") == "GUIDANCE_ACCEPTED" for event in events) == 1
        assert not host.running and not host.running_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 9 and audit.open_attempt_count == audit.pending_effect_count == 0
        assert not list(host.state_root.rglob('*.sock'))
    finally:
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        (tmp_path / "advice-evidence.json").write_text(json.dumps(scenario.advice_evidence, indent=2))
        host.close(); provider.shutdown(); provider.server_close(); thread.join(timeout=5)
