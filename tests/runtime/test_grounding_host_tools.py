import json
import shutil
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentACIError, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.grounding_web import GroundingWeb
from hypoloom.oracle import Oracle, _audit_grounding_sources
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_grounding_web import Transport, SEARCH, DOC, _response
from tests.runtime.test_host_experiment_tools import _until


class ExploringAgent(FakeAgent):
    host = None
    grounding_request = None
    consumed = False

    def __init__(self):
        super().__init__()
        self.literature_requests = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.GROUNDING:
            self.grounding_request = request
            found = self.host.handle_agent_tool(request.attempt_id, "web_search", {"query": "project evaluation"})
            assert found["results"][0]["url"] == DOC
            page = self.host.handle_agent_tool(request.attempt_id, "web_fetch", {"url": DOC})
            assert "python evaluate.py" in page["text"]
            for tool in ("submit_experiment", "list_my_experiments"):
                with pytest.raises(AgentACIError, match="execution node"):
                    self.host.handle_agent_tool(request.attempt_id, tool, {})
            with pytest.raises(AgentACIError, match="unsafe_url"):
                self.host.handle_agent_tool(request.attempt_id, "web_fetch", {"url": "https://user:secret@example.org"})
            candidate = dict(response.typed_candidate)
            candidate["background"] = {**candidate["background"], "entrypoints": ["evaluate.py"],
                "how_to_run_experiment": "python evaluate.py --fixed-split"}
            candidate["web_sources"] = [DOC]
            response = with_output_digest(replace(response, typed_candidate=candidate))
        elif request.command is AgentCommand.LITERATURE_REVIEW and self.grounding_request is not None:
            inputs = [json.loads(item.content_bytes()) for item in request.input_artifacts
                if item.ref.endswith("--response") and item.media_type == "application/json"]
            background = next((item["typed_candidate"].get("background") for item in inputs
                if isinstance(item.get("typed_candidate"), dict) and item["typed_candidate"].get("task_kind") == "GROUNDING"), None)
            assert background is not None
            assert background["entrypoints"] == ["evaluate.py"]
            assert background["how_to_run_experiment"] == "python evaluate.py --fixed-split"
            self.consumed = True
            assert request.schema_version >= 14
            page = self.host.handle_agent_tool(request.attempt_id, "web_fetch", {"url": DOC})
            assert "python evaluate.py" in page["text"]
            self.literature_requests.append(request)
            if request.task_context.key.subkind is ResearchNodeSubkind.MAIN:
                response = with_output_digest(replace(response, typed_candidate={
                    **response.typed_candidate, "web_sources": [DOC]}))
        return response


class RepairingCitationAgent(ExploringAgent):
    def invoke(self, request):
        if request.command is AgentCommand.GROUNDING and request.attempt_ordinal > 1:
            response = FakeAgent.invoke(self, request)
            return with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                "background": {**response.typed_candidate["background"], "entrypoints": ["evaluate.py"],
                    "how_to_run_experiment": "python evaluate.py --fixed-split"}, "web_sources": [DOC]}))
        response = super().invoke(request)
        if request.command is AgentCommand.GROUNDING:
            response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                "web_sources": ["https://example.org/not-retrieved"]}))
        return response


@pytest.fixture
def grounded(tmp_path, request):
    with tempfile.TemporaryDirectory(prefix="hypoloom-grounding-target-", dir="/var/tmp") as target:
        transport = Transport({SEARCH: _response(SEARCH, b'<a class="result__a" href="https://example.org/docs">Documentation</a>'),
            DOC: _response(DOC, b'<title>Evaluation</title><p>python evaluate.py --fixed-split</p>')})
        agent = RepairingCitationAgent() if getattr(request, "param", None) == "repair" else ExploringAgent()
        host = WorkflowHost(tmp_path / "state", local_agent=agent, grounding_web=GroundingWeb(transport))
        agent.host = host
        host.initialize()
        try:
            result = host.handle(ControlRequest(schema_version=1, request_id="create-grounding", expected_revision=0,
                command="CREATE_RUN", payload=_pilot_payload(tmp_path, Path(target), "grounding-host")))
            assert result.status == "accepted", result
            _until(host, lambda: host.store.latest_transition("grounding-host")["state"] == "COMPLETED")
            yield host, agent
        finally:
            host.close()


def test_grounding_tools_feed_next_expert_and_real_cpu_closure(grounded):
    host, agent = grounded
    assert agent.consumed
    row = host.store.get_attempt(agent.grounding_request.attempt_id)
    records = json.loads(row["observation_json"])["web_tool_results"]
    assert [item["status"] for item in records] == ["success", "success", "error"]
    assert "text" not in records[1]["result"]
    assert agent.literature_requests
    for request in agent.literature_requests:
        assert request.attempt_id != agent.grounding_request.attempt_id
        observation = json.loads(host.store.get_attempt(request.attempt_id)["observation_json"])
        assert observation["request"]["attempt_id"] == request.attempt_id
        assert observation["request"]["command"] == "LITERATURE_REVIEW"
        literature_records = observation["web_tool_results"]
        assert len(literature_records) == 1
        assert literature_records[0]["tool"] == "web_fetch"
        assert literature_records[0]["status"] == "success"
        assert literature_records[0]["arguments"] == {"url": DOC}
        assert "text" not in literature_records[0]["result"]
    assert len(host.store.list_facts("grounding-host", "CanonicalOutcomeAccepted")) == 1
    report = Oracle(host.store.database_path, host.publisher.root).audit("grounding-host")
    assert report.ok, report.to_dict()
    with pytest.raises(AgentACIError, match="current OPEN"):
        host.handle_agent_tool(agent.grounding_request.attempt_id, "web_fetch", {"url": DOC})


@pytest.mark.parametrize("version", [13, 14])
def test_literature_web_permission_is_versioned_and_records_only_its_attempt(version):
    request = replace(default_request(AgentCommand.LITERATURE_REVIEW), schema_version=version)
    row = {"kind": "AGENT", "state": "OPEN", "host_generation": 1,
        "observation_json": json.dumps({"request": request.to_dict()})}
    host = object.__new__(WorkflowHost)
    host.generation = 1
    host.store = SimpleNamespace(get_attempt=lambda attempt_id: row if attempt_id == request.attempt_id else None)
    host.running_agents = {}
    host.web_tool_results = {}
    host.grounding_web = GroundingWeb(Transport({DOC: _response(DOC, b"<p>Actual literature source</p>")}))
    if version == 13:
        with pytest.raises(AgentACIError, match="Grounding invocation"):
            host.handle_agent_tool(request.attempt_id, "web_fetch", {"url": DOC})
        assert host.web_tool_results == {}
    else:
        assert "Actual literature source" in host.handle_agent_tool(request.attempt_id, "web_fetch", {"url": DOC})["text"]
        assert set(host.web_tool_results) == {request.attempt_id}
        assert host.web_tool_results[request.attempt_id][0]["status"] == "success"


@pytest.mark.parametrize("grounded", ["repair"], indirect=True)
def test_unobserved_citation_is_rejected_and_same_task_retry_reuses_observed_web_context(grounded):
    host, agent = grounded
    rows = host.store.list_attempts(workflow_id=agent.grounding_request.owner_workflow_id,
        command_id=agent.grounding_request.command_id)
    assert [row["terminal_reason"] for row in rows] == ["FAILED", "SUCCEEDED"]
    failed, repaired = [json.loads(row["observation_json"]) for row in rows]
    assert failed["failure_code"] == "invalid_candidate" and failed["retry_class"] == "MODEL"
    assert failed["web_tool_results"]
    assert "web_tool_results" not in repaired
    assert repaired["response"]["typed_candidate"]["web_sources"] == [DOC]
    assert agent.consumed
    report = Oracle(host.store.database_path, host.publisher.root).audit("grounding-host")
    assert report.ok, report.to_dict()


def test_oracle_rehashed_grounding_citation_tamper_is_not_an_artifact_digest_error(grounded, tmp_path):
    from hypoloom.agent_aci import AgentResponse
    host, agent = grounded
    destination = tmp_path / "copy.sqlite"
    source = sqlite3.connect(f"file:{host.store.database_path}?mode=ro", uri=True)
    copied = sqlite3.connect(destination)
    source.backup(copied)
    source.close()
    copied.execute("DROP TRIGGER execution_attempt_update_rules")
    raw = json.loads(copied.execute("SELECT observation_json FROM execution_attempts WHERE attempt_id=?", (agent.grounding_request.attempt_id,)).fetchone()[0])
    response = AgentResponse.from_dict(raw["response"])
    raw["response"] = with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
        "web_sources": ["https://example.org/invented"]})).to_dict()
    copied.execute("UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?", (json.dumps(raw), agent.grounding_request.attempt_id))
    copied.commit(); copied.close()
    artifact_copy = tmp_path / "artifacts"
    shutil.copytree(host.publisher.root, artifact_copy)
    report = Oracle(destination, artifact_copy).audit("grounding-host")
    assert "grounding_sources_unobserved" in {issue.code for issue in report.issues}
    assert not any("digest" in issue.code for issue in report.issues)


def test_oracle_prior_lost_sources_are_same_logical_task_only():
    from tests.runtime.test_grounding_aci import background
    rows = [{"attempt_id": "lost", "kind": "AGENT", "state": "CLOSED", "terminal_reason": "LOST", "workflow_id": "run", "command_id": "grounding", "attempt_ordinal": 1},
            {"attempt_id": "accepted", "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED", "workflow_id": "run", "command_id": "grounding", "attempt_ordinal": 2}]
    request = {"run_id": "run", "role": "expert", "command": "GROUNDING", "schema_version": 13}
    payloads = {"lost": {"request": request, "web_tool_results": [{"tool": "web_fetch", "arguments": {"url": DOC}, "status": "success", "result": {"url": DOC}}]},
        "accepted": {"request": request, "response": {"typed_candidate": {"background": background(), "web_sources": [DOC]}}}}
    issues = []
    _audit_grounding_sources(Path("/not-used"), rows, payloads, issues)
    assert not issues
    rows[0]["command_id"] = "another-task"
    _audit_grounding_sources(Path("/not-used"), rows, payloads, issues)
    assert [issue.code for issue in issues] == ["grounding_sources_unobserved"]


@pytest.mark.parametrize("damage", [None, "wrong-task", "archive-bytes"])
def test_host_and_oracle_restore_lost_native_web_result_from_existing_archive(tmp_path, damage):
    from types import SimpleNamespace
    from hypoloom.artifacts import AgentArtifactKey, ArtifactPublisher, ArtifactIntegrityError
    from tests.runtime.test_grounding_aci import grounding_exchange
    from tests.runtime.test_grounding_background import web_transcript
    request, response = grounding_exchange()
    previous = replace(request, attempt_id="lost-grounding-1", attempt_ordinal=1)
    request = replace(request, attempt_id="retry-grounding-2", attempt_ordinal=2)
    publisher = ArtifactPublisher(tmp_path / "artifacts")
    stage = tmp_path / "stage"; stage.mkdir()
    records = web_transcript(result={"url": DOC, "text": "actual native MCP observation"})
    (stage / "transcript.jsonl").write_bytes(b"\n".join(json.dumps(record).encode() for record in records))
    key = AgentArtifactKey(run_id=request.run_id, attempt_id=previous.attempt_id)
    publication = publisher.publish(key, stage).to_dict()
    publication["path"] = "/".join(key.as_tuple())
    closed = {"request": previous.to_dict(), "session": {"session_id": "session-1", "transcript": publication}}
    row = {"attempt_id": previous.attempt_id, "kind": "AGENT", "state": "CLOSED", "terminal_reason": "LOST",
        "workflow_id": request.owner_workflow_id, "command_id": request.command_id, "attempt_ordinal": 1,
        "observation_json": json.dumps(closed)}
    if damage == "wrong-task": row["command_id"] = "another-task"
    if damage == "archive-bytes": (publisher.path_for(key) / "transcript.jsonl").write_bytes(b"changed")
    host = object.__new__(WorkflowHost)
    host.publisher = publisher; host.web_tool_results = {}
    host.store = SimpleNamespace(list_attempts=lambda **where: [row] if row["command_id"] == where["command_id"] else [])
    if damage == "archive-bytes":
        with pytest.raises(ArtifactIntegrityError): host._grounding_retrieved_urls(request)
    else:
        assert host._grounding_retrieved_urls(request) == (set() if damage else {DOC})
    accepted = {**row, "attempt_id": request.attempt_id, "command_id": request.command_id, "terminal_reason": "SUCCEEDED", "attempt_ordinal": 2}
    payloads = {previous.attempt_id: closed, request.attempt_id: {"request": request.to_dict(),
        "response": {**response.to_dict(), "typed_candidate": {**response.typed_candidate, "web_sources": [DOC]}}}}
    issues = []
    _audit_grounding_sources(publisher.root, [row, accepted], payloads, issues)
    assert {issue.code for issue in issues} == ({"grounding_transcript_source_invalid", "grounding_sources_unobserved"}
        if damage == "archive-bytes" else {"grounding_sources_unobserved"} if damage else set())
