"""Three actual expert sessions, native Observer selection and Leader memory."""

import copy
import json
import secrets
import time
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_interaction import InteractionFacts
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _payload
from test_expert_batch_host import SharedGoalAgent
from test_expert_memory_g3 import _cycle
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


_ROLES = ("ml-researcher", "scientist-b", "scientist-c")


def _synthesis(request):
    return (request.command is AgentCommand.HYPOTHESIS_PROPOSAL
        and isinstance(request.task_context, ResearchNodeTask)
        and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS)


class _LeaderCandidateAgent(SharedGoalAgent):
    def __init__(self):
        super().__init__(all_refuted=False)
        self.cache = {}
        self.selected = False

    def invoke(self, request):
        if request.attempt_id in self.cache:
            return self.cache[request.attempt_id]
        response = super().invoke(request)
        if _synthesis(request):
            member = response.typed_candidate["hypotheses"][0]
            response = with_output_digest(replace(response, typed_candidate={"schema_version": 1,
                "hypotheses": [{**member, "chain_id": "chain-a"}]}))
        if request.command is AgentCommand.OBSERVER and not self.selected:
            context = json.loads(next(item for item in request.input_artifacts
                if item.ref.endswith("--interaction-context")).content_bytes())
            reports = [json.loads(item.content_bytes()) for item in request.input_artifacts if item.ref.endswith("--response")]
            completed = [item for item in reports if item.get("typed_candidate", {}).get("task_kind") == "CYCLE_REPORT"]
            # These are the Host's actual closed report inputs, not provider
            # counters or invented successful sessions. Both peers have bytes.
            if len(completed) == 3 and "leader" in context["allowed_actions"]:
                facts = InteractionFacts.from_rows(request.run_id, context["facts"])
                qualified = next(item for item in facts.leader_qualifications() if item.role == _ROLES[0])
                self.selected = True
                response = with_output_digest(replace(response, typed_candidate={
                    "action": "leader", "leader": qualified.role, "debate": None,
                    "supporting_refs": list(qualified.qualification_refs),
                    "rationale": "The accepted supported contribution can guide one bounded new round using both completed peer reports."}))
        self.cache[request.attempt_id] = response
        return response


class _LeaderHostScenario(_FinalReportHostScenario):
    def __init__(self, private_root):
        super().__init__(private_root)
        self.reviser = _LeaderCandidateAgent()
        self.restart_completed = True
        self.role_nonces = {role: "accepted-private-report-" + secrets.token_hex(16) for role in _ROLES}
        self.role_chain_nonces = {role: {chain: "private-chain-tool-" + secrets.token_hex(16)
            for chain in ("chain-a", "chain-b")} for role in _ROLES}
        self.role_evolve_nonces = {role: "local-self-evolution-" + secrets.token_hex(16) for role in _ROLES}
        self.leader_evidence = {}
        self.observer_evidence = {}

    def _identity(self, request):
        return request.role, request.component_id, _cycle(request), *super()._identity(request)

    def _expected_outcome(self, request):
        return "SUCCESS" if request.role == _ROLES[0] else "CLEAN_REFUTATION"

    def record(self, document):
        request = _request_from_provider(document)
        if request.role in self.role_nonces:
            self.nonce = self.role_nonces[request.role]
            self.chain_nonces = self.role_chain_nonces[request.role]
            self.evolve_nonce = self.role_evolve_nonces[request.role]
        number = super().record(document)
        step = self.steps[request.attempt_id]
        try:
            if request.command is AgentCommand.OBSERVER:
                previous = {2: "observer_skill", 3: "observer_facts", 4: "observer_isolation"}.get(step)
            elif _synthesis(request):
                designated = any(item.ref.endswith("--leader-resources") for item in request.input_artifacts)
                previous = ({2: "peer_b_index", 3: "peer_b_round", 4: "peer_c_index", 5: "peer_c_round", 6: "peer_readonly"}
                    if designated else {2: "ordinary_no_peer"}).get(step)
            else:
                previous = None
            if previous:
                result = _tool_results(document)["probe_" + request.attempt_id[-16:] + "__" + previous]
                assert not result.get("is_error"), result
                text = _result_text(result)
                target = self.observer_evidence if request.command is AgentCommand.OBSERVER else self.leader_evidence
                target[request.attempt_id + "/" + previous] = text
                if previous.startswith("peer_b_"):
                    assert self.role_nonces["scientist-b"] in text
                if previous.startswith("peer_c_"):
                    assert self.role_nonces["scientist-c"] in text
        except Exception as exc:
            self.errors.append(f"Leader provider {len(self.requests)}: {type(exc).__name__}: {exc}")
        return number

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request is None or self.errors:
            return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.OBSERVER:
            if step == 1:
                return (("observer_skill", "Skill", {"skill": "hypoloom-grounding:leader-trigger"}),)
            if step == 2:
                index = next(index for index, item in enumerate(request.input_artifacts) if item.ref.endswith("--interaction-context"))
                return (("observer_facts", "Read", {"file_path": f"/inputs/{index:04d}.txt"}),)
            if step == 3:
                return (("observer_isolation", "Bash", {"command": "python3 - <<'PY'\nimport os\nfrom pathlib import Path\nassert os.statvfs('/workspace').f_flag & os.ST_RDONLY\nassert not Path('/memory').exists()\nassert not Path('/peer-memory').exists()\nassert not Path('/plugin-update').exists()\nassert not Path('/state').exists()\nprint('STATELESS_OBSERVER_READONLY')\nPY", "timeout": 10000}),)
            return self._structured(request)
        if _synthesis(request):
            designated = any(item.ref.endswith("--leader-resources") for item in request.input_artifacts)
            if designated and step <= 4:
                peer, suffix = (("scientist-b", "MEMORY.md"), ("scientist-b", "rounds/1.md"),
                    ("scientist-c", "MEMORY.md"), ("scientist-c", "rounds/1.md"))[step - 1]
                identity = ("peer_b_index", "peer_b_round", "peer_c_index", "peer_c_round")[step - 1]
                return ((identity, "Read", {"file_path": f"/peer-memory/{peer}/{suffix}"}),)
            if designated and step == 5:
                script = "import errno, os\nfrom pathlib import Path\nassert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                script += "assert {path.name for path in Path('/peer-memory').iterdir()} == {'scientist-b', 'scientist-c'}\n"
                for peer in _ROLES[1:]:
                    script += f"assert {self.role_nonces[peer]!r} in Path('/peer-memory/{peer}/MEMORY.md').read_text()\n"
                script += "for peer in ['scientist-b', 'scientist-c']:\n    for suffix in ['MEMORY.md', 'rounds/1.md']:\n        path = '/peer-memory/' + peer + '/' + suffix\n        try:\n            fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n        except OSError as error:\n            assert error.errno == errno.EROFS, (path, error)\n        else:\n            os.close(fd)\n            raise AssertionError('peer memory writable')\nprint('ACTUAL_PEER_REPORTS_READONLY')\n"
                return (("peer_readonly", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
            if not designated and step == 1:
                return (("ordinary_no_peer", "Bash", {"command": "python3 - <<'PY'\nfrom pathlib import Path\nassert not Path('/peer-memory').exists()\nprint('ORDINARY_SYNTHESIS_NO_PEER_MOUNT')\nPY", "timeout": 10000}),)
            return self._structured(request)
        if request.command is AgentCommand.SELF_EVOLVE and _cycle(request) == 2 and step >= 3:
            return self._structured(request)
        return super()._unscoped_tool_calls(number)


def test_real_cli_three_experts_observer_selects_leader_and_next_synthesis_reads_actual_peer_reports(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    executable = _installed_claude()
    scenario = _LeaderHostScenario(tmp_path)
    key = "host-only-leader-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n"); (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    run_id = "native-leader-host-g3"
    try:
        payload = _payload(tmp_path, run_id)
        contract = payload["research_contract"]
        settings = contract["methodology"]["controls"]["product"]
        # This interaction rehearsal must continue after one local success.
        assert settings["goal"]["completion_mode"] == "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 2
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        settings["methodology"]["interaction"] = {"leader": {"enabled": True, "check_after": "cycle_report", "min_rounds_before_leader": 1}, "debate": {"enabled": False, "rounds": 3}}
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
        created = host.handle(ControlRequest(schema_version=1, request_id="create-native-leader", expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        # Leave bounded room for the final peer cycle and report in the full suite.
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED", host.store.list_attempts()
        expected = {(_ROLES[0], "primary.validation_accuracy", cycle, "chain-a", 1) for cycle in (1, 2)}
        expected |= {(role, "primary." + role, 1, "chain-a", 1) for role in _ROLES[1:]}
        assert scenario.local_tests == set(scenario.execution_results) == expected
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 4
        observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        observers = [row for row in observations if row["request"]["command"] == "OBSERVER"]
        assert len(observers) == 4 and len(scenario.observer_evidence) == 12
        assert len({row["session"]["session_id"] for row in observers}) == 4
        assert all(row["session"]["launch_arguments"] == ["--session-id", row["session"]["session_id"]] and row["session"]["parent_session_id"] is None for row in observers)
        assert [row["response"]["typed_candidate"]["action"] for row in observers].count("leader") == 1
        leaders = [(row, item) for row in observations for item in row["request"]["input_artifacts"] if item["ref"].endswith("--leader-resources")]
        assert len(leaders) == 1 and len(scenario.leader_evidence) == 8
        leader, item = leaders[0]
        assert leader["request"]["role"] == _ROLES[0] and leader["request"]["task_context"]["key"]["cycle"] == 2
        assert len(host._batch_history(run_id, "primary.validation_accuracy").batches) == 2
        assert all(len(host._batch_history(run_id, "primary." + role).batches) == 1 for role in _ROLES[1:])
        events = [json.loads(row["payload_json"])["expert_interaction"] for row in host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND json_type(payload_json,'$.expert_interaction')='object'", (run_id,))]
        assert [item["action"] for item in events if item["action"].startswith("leader_")] == ["leader_selected", "leader_round_started", "leader_round_finished"]
        assert not host._participant_controls(run_id).pauses
        assert not host.running and not host.running_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        for observed in observations:
            archive = host.publisher.root / observed["session"]["transcript"]["path"]
            assert observed["session"]["transcript_complete"] is True
            assert key.encode() not in (archive / "transcript.jsonl").read_bytes()
        audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 4 and audit.artifact_counts["TRANSCRIPT"] == len(observations)
        assert audit.open_attempt_count == audit.pending_effect_count == 0
        assert getattr(scenario, "count_token_requests", []), "the large fourth Observer source exercises actual CLI token-counting transport"
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        (tmp_path / "leader-native-evidence.json").write_text(json.dumps(scenario.leader_evidence, indent=2))
        (tmp_path / "observer-native-evidence.json").write_text(json.dumps(scenario.observer_evidence, indent=2))
        (tmp_path / "count-token-requests.json").write_text(json.dumps(getattr(scenario, "count_token_requests", []), indent=2))
        host.close(); provider.shutdown(); provider.server_close(); thread.join(timeout=5)
