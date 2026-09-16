"""Actual two-expert Debate, six round outputs, recovery and fork promotion.

Model and web replies are scripted locally; all Claude sessions, Host writes,
Git, four CPU Processes and final reporting use their production paths.
"""
import copy
import json
import secrets
import sys
import time
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_leader_host_g3 import _LeaderCandidateAgent, _LeaderHostScenario, _synthesis
from test_claude_multichain_g3 import _payload
from test_expert_memory_g3 import _cycle
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


_ROLES = ("ml-researcher", "scientist-b")
_WEB_URL = "https://example.org/scripted-debate-record"


class _DebateCandidateAgent(_LeaderCandidateAgent):
    def invoke(self, request):
        if request.attempt_id in self.cache: return self.cache[request.attempt_id]
        response = super().invoke(request)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            response = with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(item.path, item.content_bytes().replace(b'"accuracy": 0.50', b'"accuracy": 0.55'))
                for item in response.proposed_workspace_artifacts)))
        if request.command is AgentCommand.OBSERVER and not self.selected:
            context = json.loads(next(item.content_bytes() for item in request.input_artifacts if item.ref.endswith("--interaction-context")))
            reports = [json.loads(item.content_bytes()) for item in request.input_artifacts if item.ref.endswith("--response")]
            if len([item for item in reports if item.get("typed_candidate", {}).get("task_kind") == "CYCLE_REPORT"]) == 2 and "debate" in context["allowed_actions"]:
                self.selected = True
                response = with_output_digest(replace(response, typed_candidate={"action": "debate", "leader": None,
                    "debate": {"participants": context["debate_participants"], "topic": "Compare the two accepted mechanisms without altering their scientific assessments."},
                    "rationale": "Both experts completed a supported round; compare their mechanisms in three source-bound rounds.",
                    "supporting_refs": [row["fact_id"] for row in context["facts"] if row["fact_type"] == "HypothesisAssessed"]}))
        if request.command is AgentCommand.DEBATE:
            context = json.loads(next(item.content_bytes() for item in request.input_artifacts if item.ref.endswith("--interaction-context")))
            response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                "evidence": [row["fact_id"] for row in context["facts"] if row["fact_type"] == "CanonicalOutcomeAccepted"],
                "web_sources": [_WEB_URL]}))
        self.cache[request.attempt_id] = response
        return response


class _ScriptedDebateWeb:
    def __init__(self): self.calls = []
    def web_fetch(self, url, *, max_chars=20000):
        assert url == _WEB_URL
        self.calls.append(url)
        return {"requested_url": url, "url": url, "title": "Local scripted Debate source", "content_type": "text/html",
            "text": "SCRIPTED_DEBATE_WEB_NONCE: discuss accepted outcomes, never mutate them.", "truncated": False}
    def web_search(self, *args, **kwargs): raise AssertionError("this fixture requests fetch only; no public network")


class _DebateHostScenario(_LeaderHostScenario):
    def __init__(self, root):
        super().__init__(root)
        self.reviser = _DebateCandidateAgent()
        self.debate_nonces = {(role, number): "native-debate-only-" + secrets.token_hex(16) for role in _ROLES for number in range(1, 4)}
        self.debate_evidence = {}
        self.debate_restart_ready = False
        self.debate_restart_completed = False
        self.promoted_consumers = set()

    def _expected_outcome(self, request): return "SUCCESS"

    def record(self, document):
        number = super().record(document)
        request = _request_from_provider(document); step = self.steps[request.attempt_id]
        try:
            previous = None
            if request.command is AgentCommand.DEBATE:
                context = json.loads(next(item.content_bytes() for item in request.input_artifacts if item.ref.endswith("--debate-context")))
                round_number = context["round"]
                if step == 1 and round_number > 1:
                    assert self.debate_nonces[(request.role, round_number - 1)] in json.dumps(document["messages"]), "own prior actual Debate tool output was not resumed"
                previous = {2: "debate_context", 3: "debate_memory", 4: "debate_web", 5: "debate_readonly"}.get(step)
                if step == 2 and round_number == 2 and request.role == _ROLES[0] and not self.debate_restart_completed:
                    self.debate_restart_ready = True
            elif request.command is AgentCommand.DEBATE_JUDGE:
                previous = {2: "debate_judge_skill", 3: "debate_judge_records"}.get(step)
            elif _synthesis(request) and _cycle(request) == 2:
                native_history = json.dumps(document["messages"])
                if request.role == _ROLES[0]:
                    assert self.debate_nonces[(request.role, 3)] in native_history, "winning actual Debate transcript was not promoted"
                    self.promoted_consumers.add(request.attempt_id)
                else:
                    assert self.debate_nonces[(request.role, 3)] not in native_history, "loser's discarded Debate fork was promoted"
            if previous:
                result = _tool_results(document)["probe_" + request.attempt_id[-16:] + "__" + previous]
                assert not result.get("is_error"), result
                self.debate_evidence[request.attempt_id + "/" + previous] = _result_text(result)
                if previous == "debate_web": assert "SCRIPTED_DEBATE_WEB_NONCE" in _result_text(result)
                if previous == "debate_judge_records":
                    rows = json.loads(next(item.content_bytes() for item in request.input_artifacts if item.ref.endswith("--debate-judge-context")))["rounds"]
                    text = _result_text(result)
                    for row in rows:
                        assert row["source_attempt_id"] in text
                        assert json.dumps(row["statement"]) in text
        except Exception as exc:
            self.errors.append(f"Debate native provider: {type(exc).__name__}: {exc}")
        return number

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request is None or self.errors: return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.OBSERVER and step == 1:
            return (("observer_skill", "Skill", {"skill": "hypoloom-grounding:debate-trigger"}),)
        if request.command is AgentCommand.DEBATE:
            context = json.loads(next(item.content_bytes() for item in request.input_artifacts if item.ref.endswith("--debate-context")))
            if step == 1:
                ref = context["previous_opponent_attempt_id"] or context["original_attempt_id"]
                index = next(index for index, item in enumerate(request.input_artifacts) if item.ref == ref)
                return (("debate_context", "Read", {"file_path": f"/inputs/{index:04d}.txt"}),)
            if step == 2:
                if context["round"] == 2 and request.role == _ROLES[0] and not self.debate_restart_completed:
                    return (("debate_before_restart", "Bash", {"command": "sleep 30", "timeout": 35000}),)
                return (("debate_memory", "Read", {"file_path": "/memory/MEMORY.md"}),)
            if step == 3: return (("debate_web", "mcp__host-tools__web_fetch", {"url": _WEB_URL}),)
            if step == 4:
                script = "import os\nfrom pathlib import Path\nassert os.statvfs('/workspace').f_flag & os.ST_RDONLY\nassert os.statvfs('/memory').f_flag & os.ST_RDONLY\nassert Path('/expert-plugin/skills/experiment-report/SKILL.md').is_file()\nassert not Path('/plugin-update').exists()\n"
                script += "print(" + repr(self.debate_nonces[(request.role, context["round"])]) + ")\n"
                return (("debate_readonly", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
            return self._structured(request)
        if request.command is AgentCommand.DEBATE_JUDGE:
            if step == 1: return (("debate_judge_skill", "Skill", {"skill": "hypoloom-grounding:debate-judge"}),)
            if step == 2:
                index = next(index for index, item in enumerate(request.input_artifacts) if item.ref.endswith("--debate-judge-context"))
                script = f"import json\nfrom pathlib import Path\nrows=json.loads(Path('/inputs/{index:04d}.txt').read_text())['rounds']\nassert len(rows)==6\n"
                script += "for row in rows:\n print(json.dumps({'attempt_id':row['source_attempt_id'],'role':row['participant']['scope']['role'],'candidate':row['statement']}))\n"
                return (("debate_judge_records", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
            return self._structured(request)
        return super()._unscoped_tool_calls(number)


def test_real_cli_debate_three_rounds_restart_both_hypotheses_and_promote_only_winner(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    executable = _installed_claude(); scenario = _DebateHostScenario(tmp_path)
    key = "host-only-debate-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key); base_url = f"http://127.0.0.1:{provider.server_port}"
    (tmp_path / "settings.json").write_text("{}\n"); (tmp_path / "settings.json").chmod(0o600)
    frozen_requests = {}
    original_decoder = _request_from_provider

    def decode_request(document):
        try:
            return original_decoder(document)
        except KeyError as exc:
            if str(exc) != "'content_base64'": raise
            for message in reversed(document["messages"]):
                content = message.get("content", [])
                if isinstance(content, str): content = [{"text": content}]
                for block in reversed(content):
                    text = block.get("text", ""); start = text.find('{"agent_request_metadata":')
                    if start < 0: continue
                    prompt, _ = json.JSONDecoder().raw_decode(text[start:])
                    request = frozen_requests[prompt["agent_request_metadata"]["attempt_id"]]
                    assert request.command is AgentCommand.DEBATE_JUDGE
                    assert prompt["agent_request_metadata"] == {name: value for name, value in request.to_dict().items() if name != "input_artifacts"}
                    manifests = [item for item in prompt["rendered_inputs"] if "path" in item]
                    assert len(manifests) == 1 and manifests[0]["ref"].endswith("--debate-judge-context")
                    index = next(index for index, item in enumerate(request.input_artifacts) if item.ref == manifests[0]["ref"])
                    assert manifests[0]["path"] == f"/inputs/{index:04d}.txt"
                    assert manifests[0]["size_bytes"] == len(request.input_artifacts[index].content_bytes())
                    return request
            raise AssertionError("file-backed Judge lacks its actual frozen request")

    for name in ("test_full_cc_host_g3", "test_expert_memory_g3", "test_claude_multichain_g3", "test_claude_final_report_g3", "test_claude_leader_host_g3", __name__):
        monkeypatch.setattr(sys.modules[name], "_request_from_provider", decode_request)

    def new_host():
        created_host = _new_host(tmp_path, executable, base_url, key)
        start = created_host.claude_agent_host.start
        def record_start(request, **kwargs):
            frozen_requests[request.attempt_id] = request
            return start(request, **kwargs)
        monkeypatch.setattr(created_host.claude_agent_host, "start", record_start)
        return created_host

    host = new_host(); web = _ScriptedDebateWeb(); host.grounding_web = web
    run_id = "native-debate-host-g3"; original_facts = None
    try:
        payload = _payload(tmp_path, run_id); contract = payload["research_contract"]
        settings = contract["methodology"]["controls"]["product"]
        # This interaction rehearsal must continue after one local success.
        assert settings["goal"]["completion_mode"] == "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 2
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        settings["methodology"]["interaction"] = {"leader": {"enabled": False},
            "observer": {"coalesce_reports": True},
            "debate": {"enabled": True, "rounds": 3, "min_interval_seconds": 1800, "min_new_outcomes": 2}}
        contract["protocols"]["shared-accuracy"] = {"result_kind": "metric", "requirements": {"required_metrics": ["accuracy"]}}
        contract["components"][0]["protocol"] = "shared-accuracy"
        contract["roles"][0]["protocols"] = {"default": "shared-accuracy", "allowed": ["shared-accuracy"]}
        role = _ROLES[1]
        contract["roles"].append({**copy.deepcopy(contract["roles"][0]), "id": role})
        component = copy.deepcopy(contract["components"][0]); component.update(id="primary." + role, name=role, owner=role, proposers=[role])
        contract["components"].append(component)
        settings["experts"]["experts"].append({**copy.deepcopy(settings["experts"]["experts"][0]), "name": role})
        contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE", "target": "2", "display_only": True}
        host.initialize()
        created = host.handle(ControlRequest(1, "create-native-debate", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        # Full-suite closure includes native CLI/report work; node budgets stay unchanged.
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            host.tick(); assert not scenario.errors, scenario.errors
            assert not host.dispatch_errors, host.dispatch_errors
            if original_facts is None and host.interaction.debate.pending(run_id):
                original_facts = {row["fact_id"]: row["payload_json"] for row in host.store.list_facts(run_id)}
            if scenario.debate_restart_ready and not scenario.debate_restart_completed:
                assert not host.running
                host.close(); scenario.debate_restart_completed = True
                host = new_host(); host.grounding_web = web; host.initialize()
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(.01)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED", host.store.list_attempts()
        assert scenario.debate_restart_completed and scenario.promoted_consumers
        attempts = host.store.list_attempts(); observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] in {"SUCCEEDED", "LOST"} for row in attempts)
        debates = [row for row in observations if row["request"]["command"] == "DEBATE" and "response" in row]
        assert len(debates) == 6 and len({row["request"]["command_id"] for row in debates}) == 6
        assert len([row for row in attempts if row["terminal_reason"] == "LOST"]) == 1
        assert len([row for row in observations if row["request"]["command"] == "DEBATE_JUDGE"]) == 1
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 4
        assert len(web.calls) == 6
        assert len(host.store.list_facts(run_id, "CanonicalOutcomeAccepted")) == 4
        assert {row["payload"]["assessment"] for row in host.store.list_facts(run_id, "HypothesisAssessed")} == {"SUPPORTED"}
        assert original_facts
        actual = {row["fact_id"]: row["payload_json"] for row in host.store.list_facts(run_id)}
        assert all(actual[identity] == contents for identity, contents in original_facts.items())
        for role, component in zip(_ROLES, ("primary.validation_accuracy", "primary.scientist-b"), strict=True):
            assert len(host._batch_history(run_id, component).batches) == 2
            assert not [row for row in observations if row["request"]["role"] == role and row["request"]["command"] == "LITERATURE_REVIEW" and row["request"]["task_context"].get("key", {}).get("cycle") == 2]
            fork_ids = {row["session"]["session_id"] for row in debates if row["request"]["role"] == role}
            assert len(fork_ids) == 1
            synthesis = next(row for row in observations if row["request"]["role"] == role and row["request"]["command"] == "HYPOTHESIS_PROPOSAL" and row["request"]["task_context"]["key"]["cycle"] == 2 and row["request"]["task_context"]["key"]["subkind"] == "SYNTHESIS")
            assert (synthesis["session"]["session_id"] in fork_ids) is (role == _ROLES[0])
        assert not host._participant_controls(run_id).pauses and not host.interaction.pending(run_id)
        assert not host.running and not host.running_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 4 and audit.artifact_counts["TRANSCRIPT"] == len(observations)
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        (tmp_path / "debate-evidence.json").write_text(json.dumps(scenario.debate_evidence, indent=2))
        host.close(); provider.shutdown(); provider.server_close(); thread.join(timeout=5)
