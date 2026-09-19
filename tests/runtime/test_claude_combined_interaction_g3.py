"""One native-CLI run: Leader, three-round Debate, six actual bwrap CPU effects.

Provider and web content are local fixtures. Explicit signals intentionally
include one genuine refutation; this does not test the unresolved shared-goal
interaction stop choice. No fake Host/session/writer replaces production.
"""
import json
import secrets
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import AgentRequest, WorkspaceArtifact, with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_debate_host_g3 import _DebateHostScenario, _ScriptedDebateWeb, _WEB_URL
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_leader_host_g3 import _LeaderHostScenario, _synthesis
from test_claude_multichain_g3 import _payload as native_payload
from test_combined_interaction_g3 import CombinedAgent, ROLES, _input
from test_expert_memory_g3 import _cycle
from test_full_cc_host_g3 import _MODEL, _new_host, _request_from_provider, _start_provider


RUN = "native-combined-interaction-g3"


def _one_json_tool_object(text):
    # Native Bash can prefix stdout with a harmless locale warning. Keep the
    # original output as evidence; compare its one complete emitted JSON row.
    values = [json.loads(line) for line in text.splitlines() if line.lstrip().startswith("{")]
    assert len(values) == 1, "native source reader must emit exactly one JSON object"
    return values[0]


def test_native_source_reader_keeps_full_json_after_shell_warning():
    expected = {"attempt_id": "actual-source", "response": {"typed_candidate": {"evidence": ["actual-outcome"]}}}
    text = "/bin/bash: warning: setlocale: LC_ALL: cannot change locale (C.UTF-8)\n" + json.dumps(expected) + "\n"
    assert _one_json_tool_object(text) == expected
    for invalid in ("warning without a JSON record", json.dumps(expected) + "\n" + json.dumps(expected)):
        with pytest.raises(AssertionError, match="exactly one JSON object"):
            _one_json_tool_object(invalid)


class _CombinedCandidates(CombinedAgent):
    """Deterministic provider output, evaluated once per immutable Attempt."""
    def __init__(self):
        super().__init__()
        self.cache = {}

    def invoke(self, request):
        if request.attempt_id in self.cache:
            return self.cache[request.attempt_id]
        response = super().invoke(request)
        if _synthesis(request):
            member = response.typed_candidate["hypotheses"][0]
            response = with_output_digest(replace(response, typed_candidate={"schema_version": 1,
                "hypotheses": [{**member, "chain_id": "chain-a"}]}))
        if request.command is AgentCommand.DEBATE:
            context = _input(request, "--interaction-context")
            response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                "evidence": [row["fact_id"] for row in context["facts"] if row["fact_type"] == "CanonicalOutcomeAccepted"],
                "web_sources": [_WEB_URL]}))
        self.cache[request.attempt_id] = response
        return response


class _CombinedScenario(_DebateHostScenario):
    def __init__(self, root):
        super().__init__(root)
        self.reviser = _CombinedCandidates()
        # Dedicated native Debate already exercises a killed OPEN round. This
        # bounded combination adds no second fault-injection campaign.
        self.debate_restart_completed = True
        self.loser_consumers = set()
        self.read_fallbacks = {}

    @staticmethod
    def _debate_source(request):
        context = _input(request, "--debate-context")
        ref = context["previous_opponent_attempt_id"] or context["original_attempt_id"]
        index = next(index for index, item in enumerate(request.input_artifacts) if item.ref == ref)
        row = json.loads(request.input_artifacts[index].content_bytes())
        return index, row

    def _expected_outcome(self, request):
        return "CLEAN_REFUTATION" if request.role == ROLES[2] else "SUCCESS"

    def record(self, document):
        # Reuse actual Leader/inner-loop/report tools but replace the standalone
        # Debate test's hard-coded cycle-two promotion assertion (Leader is now
        # cycle two; its post-Debate synthesis is cycle three).
        number = _LeaderHostScenario.record(self, document)
        request = _request_from_provider(document)
        step = self.steps[request.attempt_id]
        try:
            previous = None
            if request.command is AgentCommand.DEBATE:
                context = _input(request, "--debate-context")
                if step == 1 and context["round"] > 1:
                    assert self.debate_nonces[request.role, context["round"] - 1] in json.dumps(document["messages"])
                if step == 2:
                    result = _tool_results(document)["probe_" + request.attempt_id[-16:] + "__debate_context"]
                    if result.get("is_error"):
                        text = _result_text(result)
                        assert "exceeds maximum allowed size (256KB)" in text, result
                        self.read_fallbacks[request.attempt_id] = {"failed_read": text}
                        return number
                fallback = request.attempt_id in self.read_fallbacks
                effective_step = step - 1 if fallback and step >= 3 else step
                previous = ("debate_context_fallback" if fallback and step == 3 else
                    {2: "debate_context", 3: "debate_memory", 4: "debate_web", 5: "debate_readonly"}.get(effective_step))
            elif request.command is AgentCommand.DEBATE_JUDGE:
                previous = {2: "debate_judge_skill", 3: "debate_judge_records"}.get(step)
            elif _synthesis(request) and (request.role, _cycle(request)) in {(ROLES[0], 3), (ROLES[1], 2)}:
                promoted = any(item.ref.endswith("--session-promotion") for item in request.input_artifacts)
                assert promoted is (request.role == ROLES[0])
                native_history = json.dumps(document["messages"])
                assert (self.debate_nonces[request.role, 3] in native_history) is promoted
                (self.promoted_consumers if promoted else self.loser_consumers).add(request.attempt_id)
            if previous:
                result = _tool_results(document)["probe_" + request.attempt_id[-16:] + "__" + previous]
                assert not result.get("is_error"), result
                text = _result_text(result)
                self.debate_evidence[request.attempt_id + "/" + previous] = text
                if previous == "debate_web":
                    assert "SCRIPTED_DEBATE_WEB_NONCE" in text
                if previous == "debate_context_fallback":
                    _, row = self._debate_source(request)
                    source = json.loads(row["observation_json"])
                    # The native tool reads the complete original file, then
                    # emits its actual identity and complete response only.
                    emitted = _one_json_tool_object(text)
                    assert emitted == {"attempt_id": row["attempt_id"], "role": source["request"]["role"],
                        "task_context": source["request"]["task_context"], "response": source["response"]}
                    self.read_fallbacks[request.attempt_id]["fallback_output"] = text
                if previous == "debate_judge_records":
                    for row in _input(request, "--debate-judge-context")["rounds"]:
                        assert row["source_attempt_id"] in text
                        assert json.dumps(row["statement"]) in text
        except Exception as exc:
            self.errors.append(f"combined native provider: {type(exc).__name__}: {exc}")
        return number

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request is not None:
            step = self.steps[request.attempt_id]
            if request.command is AgentCommand.DEBATE and request.attempt_id in self.read_fallbacks:
                if step == 2:
                    index, _ = self._debate_source(request)
                    script = f"import json\nfrom pathlib import Path\nrow=json.loads(Path('/inputs/{index:04d}.txt').read_text())\n"
                    script += "assert row['state']=='CLOSED' and row['terminal_reason']=='SUCCEEDED'\nsource=json.loads(row['observation_json'])\n"
                    script += "print(json.dumps({'attempt_id':row['attempt_id'],'role':source['request']['role'],'task_context':source['request']['task_context'],'response':source['response']}))\n"
                    return (("debate_context_fallback", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
                # Only the scripted tool sequence gains one recovery step;
                # the actual invocation/transcript retains both tool calls.
                self.steps[request.attempt_id] -= 1
                try:
                    return super()._unscoped_tool_calls(number)
                finally:
                    self.steps[request.attempt_id] += 1
            if request.command is AgentCommand.OBSERVER and step == 1:
                action = self.responses[request.attempt_id].typed_candidate["action"]
                skill = "debate-trigger" if action == "debate" else "leader-trigger"
                return (("observer_skill", "Skill", {"skill": "hypoloom-grounding:" + skill}),)
            if request.command is AgentCommand.SELF_EVOLVE and _cycle(request) >= 2 and step >= 3:
                # Later native invocations read the already edited Skill and
                # honestly propose no further change, as the Leader helper does.
                return self._structured(request)
        return super()._unscoped_tool_calls(number)


def _payload(tmp_path):
    # Native helper supplies the admitted private /var/tmp target and actual
    # model identity; the combined topology matches the existing CPU rehearsal.
    import copy
    payload = native_payload(tmp_path, RUN)
    contract = payload["research_contract"]
    settings = contract["methodology"]["controls"]["product"]
    settings["goal"]["completion_mode"] = "explicit_signals"
    settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 3
    settings["experts"]["defaults"].update(model=_MODEL, max_forks=1, max_hypotheses_per_round=1)
    settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 1}
    settings["methodology"]["interaction"] = {"leader": {"enabled": True, "check_after": "expert_round", "min_rounds_before_leader": 1},
        "debate": {"enabled": True, "rounds": 3}}
    settings["methodology"]["graph_evolution"] = {"enabled": False}
    settings["safety"]["gpus"] = []
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
    contract["budget"].update(max_experiments=6, max_active_agents=3, max_active_processes=2, max_active_reviews=3)
    contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE", "target": "3", "display_only": True}
    return payload


def test_three_experts_native_leader_then_debate_six_cpu_and_final_report(tmp_path: Path, monkeypatch):
    executable = _installed_claude()
    scenario = _CombinedScenario(tmp_path)
    key = "host-only-combined-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    web = _ScriptedDebateWeb()
    host.grounding_web = web
    original_facts = None
    try:
        host.initialize()
        created = host.handle(ControlRequest(1, "create-native-combined", 0, "CREATE_RUN", _payload(tmp_path)))
        assert created.status == "accepted", created
        # The full native CLI/tool sequence can exceed 600s on a shared server;
        # this test-only allowance leaves every 300s node budget unchanged.
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if original_facts is None and host.interaction.debate.pending(RUN):
                original_facts = {row["fact_id"]: row["payload_json"] for row in host.store.list_facts(RUN)}
            if host.store.latest_transition(RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(.01)
        attempts = host.store.list_attempts()
        assert host.store.latest_transition(RUN)["state"] == "COMPLETED", attempts
        assert scenario.reviser.leader_selected and scenario.reviser.debate_selected
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 6
        expected = {(ROLES[0], "primary.validation_accuracy", cycle, "chain-a", 1) for cycle in (1, 2, 3)}
        expected |= {(ROLES[1], "primary.scientist-b", cycle, "chain-a", 1) for cycle in (1, 2)}
        expected.add((ROLES[2], "primary.scientist-c", 1, "chain-a", 1))
        assert scenario.local_tests == set(scenario.execution_results) == expected
        outcomes = host.store.list_facts(RUN, "CanonicalOutcomeAccepted")
        assert [row["payload"]["outcome"] for row in outcomes].count("SUCCESS") == 5
        assert [row["payload"]["outcome"] for row in outcomes].count("CLEAN_REFUTATION") == 1
        assert next(row for row in outcomes if row["payload"]["outcome"] == "CLEAN_REFUTATION")["payload"]["component_id"] == "primary.scientist-c"
        observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        events = [json.loads(row["payload_json"])["expert_interaction"] for row in host.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND json_type(payload_json,'$.expert_interaction')='object' ORDER BY rowid", (RUN,))]
        actions = [event["action"] for event in events]
        for action in ("leader_selected", "leader_round_started", "leader_round_finished", "debate_proposed", "debate_started", "debate_verdict", "debate_restarts_designated", "debate_restarts_finished"):
            assert actions.count(action) == 1, actions
        assert actions.index("leader_round_finished") < actions.index("debate_started")
        observers = [value for value in observations if value["request"]["command"] == "OBSERVER"]
        assert len(observers) == 6 and len({value["session"]["session_id"] for value in observers}) == 6
        assert all(value["session"]["launch_arguments"] == ["--session-id", value["session"]["session_id"]]
            and value["session"]["parent_session_id"] is None for value in observers)
        leaders = [value for value in observations if any(item["ref"].endswith("--leader-resources") for item in value["request"]["input_artifacts"])]
        assert len(leaders) == 1 and len(scenario.leader_evidence) >= 5
        assert set(leaders[0]["session"]["leader_resources"]["peer_memory_commits"]) == set(ROLES[1:])
        debates = [value for value in observations if value["request"]["command"] == "DEBATE"]
        assert len(debates) == len(web.calls) == 6
        # Each native Read now consumes the original Attempt or the bounded
        # preceding-opponent projection; recursive requests must not force a
        # size-error fallback in this three-round scenario.
        assert not scenario.read_fallbacks
        by_attempt = {value["request"]["attempt_id"]: value for value in observations}
        for observed in debates:
            request = AgentRequest.from_dict(observed["request"])
            context = _input(request, "--debate-context")
            ref = context["previous_opponent_attempt_id"] or context["original_attempt_id"]
            item = next(item for item in request.input_artifacts if item.ref == ref)
            assert len(item.content_bytes()) < 256 * 1024
            if context["previous_opponent_attempt_id"] is not None:
                source = by_attempt[ref]
                assert json.loads(item.content_bytes()) == {
                    "attempt_id": ref,
                    "command_id": source["request"]["command_id"],
                    "role": source["request"]["role"],
                    "command": source["request"]["command"],
                    "task_context": source["request"]["task_context"],
                    "typed_candidate": source["response"]["typed_candidate"],
                }
        assert len([value for value in observations if value["request"]["command"] == "DEBATE_JUDGE"]) == 1
        plans = next(event["plans"] for event in events if event["action"] == "debate_restarts_designated")
        assert {(plan["role"], plan["cycle"]) for plan in plans} == {(ROLES[0], 3), (ROLES[1], 2)}
        for plan in plans:
            role, cycle = plan["role"], plan["cycle"]
            own = [value for value in debates if value["request"]["role"] == role]
            own.sort(key=lambda value: _input(AgentRequest.from_dict(value["request"]), "--debate-context")["round"])
            assert {_input(AgentRequest.from_dict(value["request"]), "--debate-context")["round"] for value in own} == {1, 2, 3}
            fork = own[0]["session"]["session_id"]
            assert "--fork-session" in own[0]["session"]["launch_arguments"]
            assert all(value["session"]["session_id"] == fork and value["session"]["launch_arguments"] == ["--resume", fork] for value in own[1:])
            requests = [value for value in observations if value["request"]["role"] == role
                and value["request"].get("task_context", {}).get("key", {}).get("cycle") == cycle]
            assert not any(value["request"]["command"] == "LITERATURE_REVIEW" for value in requests)
            proposals = [value for value in requests if value["request"]["command"] == "HYPOTHESIS_PROPOSAL"]
            assert {value["request"]["task_context"]["key"]["subkind"] for value in proposals} == {"FORWARD", "BACKWARD", "SYNTHESIS"}
            synthesis = next(value for value in proposals if value["request"]["task_context"]["key"]["subkind"] == "SYNTHESIS")
            assert (synthesis["session"]["session_id"] == fork) is (role == ROLES[0])
            forward = next(value for value in proposals if value["request"]["task_context"]["key"]["subkind"] == "FORWARD")
            assert plan["source_literature_attempt_id"] + "--response" in {item["ref"] for item in forward["request"]["input_artifacts"]}
        assert scenario.promoted_consumers and scenario.loser_consumers
        actual = {row["fact_id"]: row["payload_json"] for row in host.store.list_facts(RUN)}
        assert original_facts and all(actual[identity] == contents for identity, contents in original_facts.items())
        final = [value for value in observations if value["request"]["command"] == "FINAL_REPORT"]
        assert len(final) == len(scenario.reports) == 1
        report = scenario.reports[final[0]["request"]["attempt_id"]]
        assert len(report.evidence) == 4
        artifacts = final[0]["response"]["proposed_workspace_artifacts"]
        assert len(artifacts) == 1 and WorkspaceArtifact.from_dict(artifacts[0]).content_bytes() == report.report.encode()
        assert (host.publisher.root / final[0]["session"]["transcript"]["path"] / "FINAL_REPORT.md").read_bytes() == report.report.encode()
        for value in observations:
            assert value["session"]["transcript_complete"] is True
            archive = host.publisher.root / value["session"]["transcript"]["path"]
            assert key.encode() not in (archive / "transcript.jsonl").read_bytes()
        assert not host._participant_controls(RUN).pauses and not host.interaction.pending(RUN)
        assert not host.running and not host.running_agents and not host.local_execution_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        audit = Oracle(host.store.database_path, host.publisher.root).audit(RUN)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 6 and audit.artifact_counts["TRANSCRIPT"] == len(observations)
        assert audit.open_attempt_count == audit.pending_effect_count == 0
        (tmp_path / "interactions.json").write_text(json.dumps(events, indent=2))
    finally:
        for name, value in {"provider-requests": scenario.requests, "provider-errors": scenario.errors,
                "leader-evidence": scenario.leader_evidence, "observer-evidence": scenario.observer_evidence,
                "debate-evidence": scenario.debate_evidence, "read-fallbacks": scenario.read_fallbacks,
                "attempts": host.store.list_attempts()}.items():
            (tmp_path / (name + ".json")).write_text(json.dumps(value, indent=2))
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
