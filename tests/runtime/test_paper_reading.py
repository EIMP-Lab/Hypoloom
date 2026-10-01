import copy
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentACIError, AgentRequest, EmbeddedInput, validate_postflight
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, AttemptTerminalReason
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.full_cc_runtime import full_cc_instruction, host_tool_configuration
from hypoloom.grounding_web import GroundingWeb
from hypoloom.paper_sources import PaperWorkspace, source_paths
from hypoloom.profile_v4 import project_profile_v4, request_literature_tools
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_profile_v4 import documents
from tests.runtime.test_grounding_web import DOC, Transport, _response


def test_exact_methods_survive_source_reuse_and_child_first_archive(tmp_path):
    request = default_request(AgentCommand.PAPER_READ)
    parent = PaperWorkspace(tmp_path, request)
    child = PaperWorkspace(tmp_path, replace(request, attempt_id="child"))
    roots = (parent.root, child.root)
    try:
        source = parent.add({"content": b"%PDF-exact-original", "media_type": "application/pdf",
            "pages": ("Definitions: causal prefix only", "Methods: decay 0.83\nNo future utterances")},
            source_ref="attempt-1:literature-pdf-001", acquisition="attempt-1")
        copied = child.copy_from(parent.sources[0], parent.root, live=True)
        archive = tmp_path / "archive"; archive.mkdir()
        paths = child.archive(archive)
        assert set(paths) == set(source_paths(child.sources[0]))
        parent.close(); parent = None
        assert (archive / copied["original_path"].lstrip("/")).read_bytes() == b"%PDF-exact-original"
        assert "decay 0.83" in (archive / copied["text_path"].lstrip("/")).read_text()
        assert "Page 2:" in (archive / copied["index_path"].lstrip("/")).read_text()
        assert copied["source_ref"] == source["source_ref"]
        assert "fully_read" not in copied
    finally:
        if parent is not None: parent.close()
        child.close()
    assert all(not root.exists() for root in roots)


def test_reading_capability_is_opt_in_main_only_and_has_no_delegation():
    from hypoloom.expert_loop import ResearchNodeSubkind
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    task = replace(request.task_context, key=replace(request.task_context.key, subkind=ResearchNodeSubkind.MAIN))
    configured = EmbeddedInput.from_bytes("test--expert-config", "application/json",
        json.dumps({"node_runtime": {"literature": {"tools": ["web_search", "web_fetch", "read_papers"]}}}).encode())
    request = replace(request, schema_version=20, task_context=task, command_id=task.command_id,
                      input_artifacts=(*request.input_artifacts, configured))
    assert request_literature_tools(request) == ("web_search", "web_fetch", "read_papers")
    assert "read_papers" not in request_literature_tools(replace(request, schema_version=19))
    plan = replace(task, key=replace(task.key, subkind=ResearchNodeSubkind.QUERY_PLAN))
    from hypoloom.agent_aci import required_output_schema
    planned = replace(request, task_context=plan, command_id=plan.command_id,
        required_output_schema=required_output_schema(request.command, plan, schema_version=20))
    assert "read_papers" not in request_literature_tools(planned)
    reader = default_request(AgentCommand.PAPER_READ)
    assert host_tool_configuration(reader) == (None, ())
    from hypoloom.agent_capabilities import capabilities_for
    assert capabilities_for(AgentCommand.PAPER_READ, schema_version=20).built_in_tools == ("Read", "Glob", "Grep")
    assert "No Bash, edits, network" in full_cc_instruction(reader)
    assert not validate_postflight(reader, FakeAgent().invoke(reader)).workspace_files
    with pytest.raises(AgentACIError): replace(reader, schema_version=19)


@pytest.fixture
def reading_host(tmp_path, request):
    with tempfile.TemporaryDirectory(prefix="hypoloom-paper-test-", dir="/var/tmp") as target:
        payload = _pilot_payload(tmp_path, Path(target), "paper-test")
        contract = payload["research_contract"]
        product = project_profile_v4(**documents()).settings
        product["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        product["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        product["goal"]["completion_mode"] = "explicit_signals"
        product["methodology"]["literature"]["tools"].append("read_papers")
        product["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False, "rounds": 3}}
        product["methodology"]["graph_evolution"]["enabled"] = False
        contract["methodology"]["controls"]["product"] = product
        contract["budget"]["max_active_agents"] = 4
        count = 2 if getattr(request, "param", None) == "two-experts" else 1
        if count == 2:
            contract["protocols"]["reading-test"] = {"result_kind": "metric", "requirements": {"required_metrics": ["validation_accuracy"]}}
            contract["components"][0]["protocol"] = "reading-test"
            contract["roles"][0]["protocols"] = {"default": "reading-test", "allowed": ["reading-test"]}
            role = copy.deepcopy(contract["roles"][0]); role["id"] = "reader-peer"
            component = copy.deepcopy(contract["components"][0]); component.update(
                id="primary.peer", name="peer", owner=role["id"], proposers=[role["id"]])
            contract["roles"].append(role); contract["components"].append(component)
            product["experts"]["experts"].append({**copy.deepcopy(product["experts"]["experts"][0]), "name": role["id"]})
            contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE", "target": "2", "display_only": True}
            contract["budget"]["max_active_agents"] = 5
        host = WorkflowHost(tmp_path / "state"); host.initialize()
        held = {}; finish = host._finish_fake_agent
        def hold(request, response):
            if request.command is AgentCommand.PAPER_READ or (request.command is AgentCommand.LITERATURE_REVIEW
                    and getattr(getattr(getattr(request.task_context, "key", None), "subkind", None), "value", None) == "MAIN"):
                held[request.attempt_id] = (request, response)
            else: finish(request, response)
        host._finish_fake_agent = hold
        parent = None; papers = None
        try:
            result = host.handle(ControlRequest(schema_version=1, request_id="create-paper-test", expected_revision=0,
                command="CREATE_RUN", payload=payload))
            assert result.status == "accepted", result
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                host.tick()
                if len(held) == count: break
                time.sleep(0.01)
            assert len(held) == count, host.dispatch_errors
            parent = next(iter(held.values()))[0]
            papers = PaperWorkspace(host.state_root / "paper-staging", parent)
            ref = parent.attempt_id + ":literature-pdf-001"
            papers.add({"content": b"exact-source", "media_type": "text/plain", "pages": ("Method detail: 0.83",)},
                source_ref=ref, acquisition=parent.attempt_id)
            host.running_agents[parent.attempt_id] = SimpleNamespace(request=parent, tool_workspace=SimpleNamespace(papers=papers))
            yield host, parent, ref, held, finish
        finally:
            host.running_agents.clear()
            for row in host.store.list_attempts():
                if row["kind"] == "AGENT" and row["state"] == "OPEN":
                    request = AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
                    host._agent_failure(request, AttemptTerminalReason.CANCELLED, "test_cleanup", retryable=False, owner_transition=False)
            host.paper_reading.tick()
            if papers is not None: papers.close()
            host.close()


def _submit(host, parent, ref, count=5):
    return host.handle_agent_tool(parent.attempt_id, "read_papers", {"action": "submit", "readings": [
        {"reading_id": f"method-{i}", "paper_ref": ref, "question": f"Independent design question {i}: what assumption is required?"}
        for i in range(count)]})


def test_rejected_reading_calls_can_be_corrected_without_partial_admission(reading_host):
    from hypoloom.host_tools_server import _arguments
    host, parent, ref, held, finish = reading_host
    item = {"reading_id": "method-check", "paper_ref": DOC, "question": "Which coefficient is used?"}
    arguments = {"action": "submit", "readings": [item], "wait_for_completion": "private-value"}
    with pytest.raises(ValueError) as error:
        _arguments("read_papers", arguments)
    assert "wait_for_completion" in str(error.value) and "action=wait" in str(error.value)
    assert "private-value" not in str(error.value)
    arguments.pop("wait_for_completion")
    with pytest.raises(AgentACIError, match='web_fetch\\(mode="document"'):
        host.handle_agent_tool(parent.attempt_id, "read_papers", _arguments("read_papers", arguments))
    assert not host.paper_reading.rows(parent.attempt_id)
    arguments["readings"] = [{**item, "paper_ref": ref}, {**item, "reading_id": "second"}]
    with pytest.raises(AgentACIError):
        host.handle_agent_tool(parent.attempt_id, "read_papers", arguments)
    assert not host.paper_reading.rows(parent.attempt_id)
    arguments["readings"] = [{**item, "paper_ref": ref}]
    host.handle_agent_tool(parent.attempt_id, "read_papers", arguments)
    host.handle_agent_tool(parent.attempt_id, "read_papers", arguments)
    assert len(host.paper_reading.rows(parent.attempt_id)) == 1
    host.paper_reading.tick()
    readers = [pair for pair in held.values() if pair[0].command is AgentCommand.PAPER_READ]
    assert len(readers) == 1
    finish(*readers[0])
    status = host.paper_reading.status(parent)
    assert status["terminal"] and status["readings"][0]["status"] == "completed"


@pytest.mark.parametrize("change,message", [
    ({"reading_id": "not a valid ID"}, "reading_id must"),
    ({"question": " "}, "question must"),
    ({"unexpected": True}, "supply exactly"),
])
def test_reading_validation_identifies_the_field(reading_host, change, message):
    host, parent, ref, _, _ = reading_host
    with pytest.raises(AgentACIError, match=message):
        host.handle_agent_tool(parent.attempt_id, "read_papers", {"action": "submit", "readings": [
            {"reading_id": "method-check", "paper_ref": ref, "question": "Which coefficient?", **change}]})
    assert not host.paper_reading.rows(parent.attempt_id)


def test_quarantine_never_dispatches_queued_paper_readers(reading_host):
    host, parent, ref, held, finish = reading_host
    _submit(host, parent, ref, count=3)
    before = len(host.store.list_attempts())
    host.dispatch_errors[parent.run_id] = {"exception_type": "ValueError", "phase": "dispatch"}
    host.paper_reading.tick()
    assert len(host.store.list_attempts()) == before


def test_real_host_dynamic_capacity_idempotency_and_refill(reading_host):
    host, parent, ref, held, finish = reading_host
    assert _submit(host, parent, ref)["status"] == "running"
    command_count = len(host.paper_reading.rows())
    _submit(host, parent, ref)
    assert len(host.paper_reading.rows()) == command_count == 5
    with pytest.raises(AgentACIError, match="different source/question"):
        host.handle_agent_tool(parent.attempt_id, "read_papers", {"action": "submit", "readings": [
            {"reading_id": "method-0", "paper_ref": ref, "question": "Changed question"}]})
    assert host.paper_reading.tick()
    readers = [pair for pair in held.values() if pair[0].command is AgentCommand.PAPER_READ]
    assert len(readers) == 3  # Four shared Agent slots, including the waiting parent.
    assert sum(row["state"] == "OPEN" for row in host.store.list_attempts()) == 4
    assert all(req.role == parent.role and req.required_model_identity == parent.required_model_identity for req, _ in readers)
    assert all(req.budget.max_cost == parent.budget.max_cost for req, _ in readers)
    finish(*readers[0])
    assert host.paper_reading.tick()
    readers = [pair for pair in held.values() if pair[0].command is AgentCommand.PAPER_READ]
    assert len(readers) == 4
    charges = fold_agent_attempts(row for row in host.store.list_attempts() if row["run_id"] == parent.run_id)
    assert all(not charge.run_scoped and charge.role == parent.role for charge in charges if charge.attempt_id in {r.attempt_id for r, _ in readers})
    host.handle_agent_tool(parent.attempt_id, "read_papers", {"action": "cancel"})
    for request, response in readers[1:]: finish(request, response)
    state = host.paper_reading.status(parent)
    assert state["terminal"] and [r["status"] for r in state["readings"]].count("cancelled") == 4
    assert len(host.paper_reading.rows()) == 5


def test_document_fetch_reuse_and_shared_io_wait(reading_host):
    host, parent, ref, _, _ = reading_host
    transport = Transport({DOC: _response(DOC, b"<title>Method</title><p>Decay coefficient 0.83</p>")})
    host.grounding_web = GroundingWeb(transport)
    result = host.handle_agent_tool(parent.attempt_id, "web_fetch", {"mode": "document", "url": DOC})
    assert result["status"] == "available"
    papers = host.running_agents[parent.attempt_id].tool_workspace.papers
    assert "0.83" in (papers.root / result["text_path"].removeprefix("/papers/")).read_text()
    calls = list(transport.calls)
    reused = host.handle_agent_tool(parent.attempt_id, "web_fetch", {"mode": "document", "paper_ref": result["source_ref"]})
    assert reused == result and transport.calls == calls
    assert "content" not in host.web_tool_results[parent.attempt_id][-1]["result"]
    with pytest.raises(AgentACIError, match="not in this Attempt"):
        host.handle_agent_tool(parent.attempt_id, "web_fetch", {"mode": "document", "paper_ref": "other:literature-pdf-001"})
    slots = host.grounding_web.document_slots
    slots.acquire(); slots.acquire()
    try:
        assert host.handle_agent_tool(parent.attempt_id, "web_fetch", {"mode": "document", "url": DOC}) == result
        busy = host.handle_agent_tool(parent.attempt_id, "web_fetch", {"mode": "document", "url": DOC + "/uncached"})
        assert busy["status"] == "unavailable" and busy["error"]["code"] == "paper_io_busy"
        assert transport.calls == calls
    finally: slots.release(); slots.release()


def test_no_capacity_wait_returns_and_unstarted_cancel_is_terminal(reading_host, monkeypatch):
    host, parent, ref, held, _ = reading_host
    monkeypatch.setattr(host, "_at_capacity", lambda *a, **k: True)
    assert _submit(host, parent, ref)["status"] == "blocked"
    result = host.handle_agent_tool(parent.attempt_id, "read_papers", {"action": "wait", "max_wait_seconds": 0})
    assert result["wait_reason"] == "agent_capacity" and not result["terminal"]
    assert not host.paper_reading.tick() and len(held) == 1
    result = host.handle_agent_tool(parent.attempt_id, "read_papers", {"action": "cancel"})
    assert result["terminal"] and all(row["status"] == "cancelled" for row in result["readings"])


def test_parent_failure_drains_readers_and_preserves_unknown_reservations(reading_host):
    host, parent, ref, held, _ = reading_host
    _submit(host, parent, ref); host.paper_reading.tick()
    readers = [request for request, _ in held.values() if request.command is AgentCommand.PAPER_READ]
    facts = len(host.store.list_facts(parent.run_id))
    host._agent_failure(parent, AttemptTerminalReason.TIMED_OUT, "timed_out", retryable=False)
    assert host.store.get_attempt(parent.attempt_id)["state"] == "OPEN"
    assert parent.attempt_id in host.paper_reading.completions
    for child in readers:
        host._agent_failure(child, AttemptTerminalReason.CANCELLED, "cancelled", retryable=False)
    assert host.paper_reading.tick()
    assert host.store.get_attempt(parent.attempt_id)["terminal_reason"] == "TIMED_OUT"
    assert not host.paper_reading.completions
    assert len(host.store.list_facts(parent.run_id)) == facts
    charges = fold_agent_attempts(host.store.list_attempts())
    for child in readers:
        charge = next(c for c in charges if c.attempt_id == child.attempt_id)
        assert charge.source == "declared" and charge.tokens == child.budget.max_tokens
        assert not charge.run_scoped and charge.role == parent.role
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    assert host.paper_reading.status(parent)["terminal"]


@pytest.mark.parametrize("reading_host", ["two-experts"], indirect=True)
def test_competing_experts_get_slots_before_one_expert_refills(reading_host):
    host, parent, ref, held, _ = reading_host
    peer = next(req for req, _ in held.values() if req.attempt_id != parent.attempt_id)
    workspace = PaperWorkspace(host.state_root / "paper-staging", peer)
    peer_ref = peer.attempt_id + ":paper-1"
    workspace.add({"content": b"peer-only-source", "media_type": "text/plain", "pages": ("Peer method",)},
        source_ref=peer_ref, acquisition=peer.attempt_id)
    host.running_agents[peer.attempt_id] = SimpleNamespace(request=peer, tool_workspace=SimpleNamespace(papers=workspace))
    try:
        _submit(host, parent, ref, 4); _submit(host, peer, peer_ref, 4)
        with pytest.raises(AgentACIError, match="admitted source_ref"):
            _submit(host, parent, peer_ref, 1)
        assert host.paper_reading.tick()
        readers = [req for req, _ in held.values() if req.command is AgentCommand.PAPER_READ]
        assert len(readers) == 3 and [r.role for r in readers[:2]] == [parent.role, peer.role]
    finally:
        host.running_agents.pop(peer.attempt_id, None)
        workspace.close()


def test_provider_backoff_and_budget_exhaustion_do_not_launch_extra_calls(reading_host, monkeypatch):
    host, parent, ref, held, _ = reading_host
    _submit(host, parent, ref)
    with monkeypatch.context() as context:
        context.setattr(host, "_provider_retry_pending", lambda run_id: True)
        assert host.paper_reading.status(parent)["wait_reason"] == "provider_backoff"
        assert not host.paper_reading.tick() and len(held) == 1
    monkeypatch.setattr("hypoloom.paper_reading.remaining_agent_budget", lambda *a, **kw: None)
    assert host.paper_reading.status(parent)["wait_reason"] == "budget_capacity"
    assert not host.paper_reading.tick() and len(held) == 1


def test_same_candidate_recovery_loses_attempts_and_does_not_restart_old_readers(reading_host):
    from hypoloom.paper_sources import reconcile_sources
    host, parent, ref, held, _ = reading_host
    _submit(host, parent, ref); host.paper_reading.tick()
    original_ids = {row["attempt_id"] for row in host.store.list_attempts()}
    host.running_agents.clear()  # The fixture has no OS Agents; emulate their absent groups.
    host.generation = "reopened-test-generation"
    host._recover_abandoned_attempts()
    host.paper_reading.tick(); reconcile_sources(host)
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    assert host.store.get_attempt(parent.attempt_id)["terminal_reason"] == "LOST"
    assert host.paper_reading.status(parent)["terminal"]
    assert not host.paper_reading.tick()
    assert {row["attempt_id"] for row in host.store.list_attempts()} == original_ids
    assert not list((host.state_root / "paper-staging").iterdir())


def test_end_run_uses_its_stop_command_without_admitting_new_auxiliary_work(reading_host):
    host, parent, ref, held, _ = reading_host
    _submit(host, parent, ref); host.paper_reading.tick()
    reply = host.handle(ControlRequest(schema_version=1, request_id="stop-readers", expected_revision=None,
        command="CANCEL", payload={"run_id": parent.run_id, "cause": "test End run"}))
    assert reply.status == "accepted"
    host._agent_failure(parent, AttemptTerminalReason.CANCELLED, "run_stopping", retryable=False, owner_transition=False)
    for reader, _ in held.values():
        if reader.command is AgentCommand.PAPER_READ:
            host._agent_failure(reader, AttemptTerminalReason.CANCELLED, "run_stopping", retryable=False, owner_transition=False)
    host.paper_reading.tick()
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    assert host.paper_reading.status(parent)["terminal"]
    assert not host.store.inspect("SELECT 1 FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.previous_state='STOPPING' AND c.command_type='REQUEST_AUXILIARY'")


def test_surviving_reader_source_is_recovered_from_frozen_input_before_archive(tmp_path):
    from hypoloom.agent_sessions import AgentSessions
    from hypoloom.artifacts import ArtifactPublisher
    from hypoloom.paper_sources import restore_sources
    state = tmp_path / "state"; state.mkdir()
    publisher = ArtifactPublisher(state / "artifacts")
    source = {"source_ref": "original:paper-a", "acquisition_attempt_id": "original"}
    context = EmbeddedInput.from_bytes("test--paper-context", "application/json", json.dumps({"sources": [source]}).encode())
    request = replace(default_request(AgentCommand.PAPER_READ), input_artifacts=(context,), immutable_context_refs=(context.ref,))
    sessions = AgentSessions(state, publisher)
    workspace = sessions.prepare(request, None)
    papers = PaperWorkspace(state / "paper-staging", request)
    papers.add({"content": b"original methods", "pages": ("method coefficient 0.83",), "media_type": "text/plain"},
        source_ref=source["source_ref"], acquisition="original")
    papers.add({"content": b"unobserved web text", "pages": ("not admitted",), "media_type": "text/plain"},
        source_ref="attempt-1:unobserved", acquisition="attempt-1")
    (workspace.session_root / (workspace.binding["session_id"] + ".jsonl")).write_text(
        json.dumps({"sessionId": workspace.binding["session_id"], "type": "user", "message": {"content": "fixture interrupted"}}) + "\n")
    try:
        restore_sources(SimpleNamespace(state_root=state, publisher=publisher), workspace)
        assert [s["source_ref"] for s in workspace.papers.sources] == [source["source_ref"]]
        result = workspace.finish_node()
        archive = publisher.root / result["transcript"]["path"]
        assert (archive / "papers/source-0001/original.txt").read_bytes() == b"original methods"
        assert not (archive / "papers/source-0002").exists()
    finally:
        workspace.cleanup(); workspace.discard_papers()
