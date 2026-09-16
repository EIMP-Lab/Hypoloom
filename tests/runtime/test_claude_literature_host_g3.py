"""Native literature web results, accepted summary, downstream forks and CPU closure."""
import json
import secrets
import time
from dataclasses import replace

from hypoloom.control_protocol import ControlRequest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.grounding_web import GroundingWeb
from hypoloom.oracle import Oracle
from test_claude_file_tool_cli import _installed_claude, _tool_results, _result_text
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_multichain_g3 import _PruningBatchAgent, _payload
from test_full_cc_host_g3 import _new_host, _start_provider
from test_grounding_web import DOC, SEARCH, Transport, _response


def literature(request):
    return request.command is AgentCommand.LITERATURE_REVIEW and isinstance(request.task_context, ResearchNodeTask)


class LiteratureScenario(_FinalReportHostScenario):
    def __init__(self, root):
        super().__init__(root)
        self.reviser = _PruningBatchAgent()
        self.literature_nonce = "autonomous-literature-" + secrets.token_hex(16)
        self.full_text_nonce = "unforwarded-full-text-" + secrets.token_hex(16)
        self.web_reads = set()
        self.summary_consumers = set()
        self.hypothesis_reads = {}

    def record(self, document):
        number = super().record(document)
        request = self.current
        try:
            if literature(request):
                step = self.steps[request.attempt_id]
                previous = {2: "literature_memory", 3: "literature_search", 4: "literature_fetch"}.get(step)
                if previous:
                    result = next(value for key, value in _tool_results(document).items() if key.endswith("__" + previous))
                    assert not result.get("is_error"), result
                    if previous == "literature_fetch":
                        assert self.literature_nonce in _result_text(result)
                        self.web_reads.add(request.attempt_id)
                if step == 1:
                    assert self.literature_nonce not in json.dumps(document["messages"])
                if request.task_context.key.subkind.value == "MAIN" and step >= 4:
                    response = self.responses[request.attempt_id]
                    self.responses[request.attempt_id] = replace(response, typed_candidate={
                        **response.typed_candidate, "summary": "Actual retrieved prior work states " + self.literature_nonce,
                        "web_sources": [DOC]})
            elif request.command is AgentCommand.HYPOTHESIS_PROPOSAL:
                subkind = request.task_context.key.subkind.value
                text = json.dumps(document["messages"])
                assert self.full_text_nonce not in text, "raw literature was copied into hypothesis planning"
                assert (self.literature_nonce in text) is (subkind != "BACKWARD")
                previous = ("hypothesis_forward" if subkind == "SYNTHESIS" else "hypothesis_source")
                step = self.steps[request.attempt_id]
                if step == 3 and subkind == "SYNTHESIS": previous = "hypothesis_backward"
                if step >= 2:
                    result = next(value for key, value in _tool_results(document).items() if key.endswith("__" + previous))
                    assert not result.get("is_error"), result
                    content = _result_text(result)
                    assert self.full_text_nonce not in content
                    if subkind == "FORWARD" or previous == "hypothesis_forward":
                        assert self.literature_nonce in content
                        self.summary_consumers.add(request.attempt_id)
                    else:
                        assert self.literature_nonce not in content
                    self.hypothesis_reads[request.attempt_id + "/" + previous] = content
                if subkind == "FORWARD" and step >= 2:
                    response = self.responses[request.attempt_id]
                    self.responses[request.attempt_id] = replace(response, typed_candidate={
                        **response.typed_candidate, "summary": "Forward implications of the accepted literature summary: " + self.literature_nonce})
        except Exception as exc:
            self.errors.append(f"literature production provider {number}: {type(exc).__name__}: {exc}")
        return number

    def _unscoped_tool_calls(self, number):
        if self.current is not None and literature(self.current):
            step = self.steps[self.current.attempt_id]
            calls = {1: ("literature_memory", "Read", {"file_path": "/memory/MEMORY.md"}),
                2: ("literature_search", "mcp__host-tools__web_search", {"query": "project evaluation"}),
                3: ("literature_fetch", "mcp__host-tools__web_fetch", {"url": DOC})}
            return (calls[step],) if step in calls else self._structured(self.current)
        if self.current is not None and self.current.command is AgentCommand.HYPOTHESIS_PROPOSAL:
            request = self.current
            step = self.steps[request.attempt_id]
            subkind = request.task_context.key.subkind.value
            if (subkind in {"FORWARD", "BACKWARD"} and step == 1) or (subkind == "SYNTHESIS" and step <= 2):
                if subkind == "BACKWARD":
                    index = next(index for index, item in enumerate(request.input_artifacts)
                        if json.loads(item.content_bytes()).get("fact_type") == "GoalDefined")
                else:
                    expected = "LITERATURE_REVIEW" if subkind == "FORWARD" else "HYPOTHESIS_PROPOSAL"
                    options = [(index, json.loads(item.content_bytes())) for index, item in enumerate(request.input_artifacts)
                        if item.ref.endswith("--response")]
                    options = [(index, value) for index, value in options if value["command"] == expected]
                    index = options[0 if step == 1 else 1][0]
                identity = "hypothesis_source" if subkind != "SYNTHESIS" else "hypothesis_forward" if step == 1 else "hypothesis_backward"
                return ((identity, "Read", {"file_path": f"/inputs/{index:04d}.txt"}),)
            return self._structured(request)
        return super()._unscoped_tool_calls(number)


def test_native_literature_summary_reaches_hypotheses_and_cpu_oracle(tmp_path, monkeypatch):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    scenario = LiteratureScenario(tmp_path)
    secret = "host-only-literature-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, secret)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", secret)
    transport = Transport({SEARCH: _response(SEARCH, b'<a class="result__a" href="https://example.org/docs">Prior work</a>'),
        DOC: _response(DOC, f"<title>Prior work</title><p>{scenario.literature_nonce}</p><p>{scenario.full_text_nonce}</p>".encode())})
    host.grounding_web = GroundingWeb(transport)
    run_id = "native-literature-host-g3"
    try:
        payload = _payload(tmp_path, run_id)
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-literature", expected_revision=0,
            command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        assert len(scenario.web_reads) == 2 and len(scenario.summary_consumers) == 2
        assert len(scenario.hypothesis_reads) == 4
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        proposals = [row for row in observations if row["request"]["command"] == "HYPOTHESIS_PROPOSAL"]
        assert {row["request"]["task_context"]["key"]["subkind"] for row in proposals} == {"FORWARD", "BACKWARD", "SYNTHESIS"}
        main = next(row for row in observations if row["request"]["command"] == "LITERATURE_REVIEW"
            and row["request"]["task_context"].get("key", {}).get("subkind") == "MAIN")
        assert main["response"]["typed_candidate"]["web_sources"] == [DOC]
        assert [entry["tool"] for entry in main["web_tool_results"]] == ["web_search", "web_fetch"]
        for row in observations:
            transcript = host.publisher.root / row["session"]["transcript"]["path"] / "transcript.jsonl"
            assert secret.encode() not in transcript.read_bytes()
            if row["request"]["attempt_id"] in scenario.web_reads | scenario.summary_consumers:
                assert scenario.literature_nonce.encode() in transcript.read_bytes()
        assert len(host.store.list_facts(run_id, "CanonicalOutcomeAccepted")) == 4
        assert all(json.loads(row["payload_json"])["outcome"] == "SUCCESS" for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted"))
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 4
        final, = (row for row in observations if row["request"]["command"] == "FINAL_REPORT")
        final_source, = scenario.reports.values()
        final_archive = host.publisher.root / final["session"]["transcript"]["path"]
        assert (final_archive / "FINAL_REPORT.md").read_bytes() == final_source.report.encode()
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
        (tmp_path / "web-calls.json").write_text(json.dumps(transport.calls, indent=2))
        assert report.ok, report.to_dict()
        assert report.artifact_counts["TRANSCRIPT"] == len(observations)
        assert not host.running and not host.running_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        (tmp_path / "hypothesis-read-evidence.json").write_text(json.dumps(scenario.hypothesis_reads, indent=2))
        host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
