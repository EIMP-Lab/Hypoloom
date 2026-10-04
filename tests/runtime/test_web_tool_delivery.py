"""Real socket completion and native receipt are distinct web-tool boundaries."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.artifacts import AgentArtifactKey, ArtifactPublisher
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.grounding_background import validate_web_sources
from hypoloom.host_tools_server import AgentToolServer, call_tool
from hypoloom.workflow_host import WorkflowHost
from test_grounding_background import web_transcript
from test_host_tools_server import _finish


URL = "https://example.org/slow-source"


@pytest.mark.parametrize('delivered', [False, True])
def test_download_trace_does_not_change_native_delivery_identity(delivered):
    from hypoloom.grounding_background import delivered_web_metadata
    record = {'tool': 'web_fetch', 'arguments': {'mode': 'document', 'url': URL},
        'status': 'success', 'result': {'status': 'available', 'url': URL, 'source_ref': 'owned-source'}}
    host = {**record, 'literature_download_trace': [{'events': [{'stage': 'fallback_lookup'}]}]}
    result = delivered_web_metadata([host], [record] if delivered else [])
    assert result[0]['status'] == ('success' if delivered else 'error')
    assert result[0]['literature_download_trace'] == host['literature_download_trace']
    if not delivered:
        assert result[0]['error']['code'] == 'response_not_observed'


def test_internal_document_waits_keep_diagnostics_under_the_actual_native_call():
    from hypoloom.grounding_background import delivered_web_metadata
    call = {'tool': 'web_fetch', 'arguments': {'mode': 'document', 'url': URL}, 'status': 'success'}
    pending = {**call, 'result': {'status': 'pending', 'remaining_seconds': 10},
        'literature_download_trace': [{'events': [{'stage': 'fallback_lookup'}]}]}
    final = {**call, 'result': {'status': 'available', 'url': URL, 'source_ref': 'owned-source'}}
    assert delivered_web_metadata([pending, final], [final]) == [
        {**final, 'literature_download_trace': pending['literature_download_trace']}]
    assert delivered_web_metadata([pending], []) == []


def test_expired_queued_web_call_never_starts_read_or_records_success(tmp_path):
    reads = []
    class Web:
        def web_fetch(self, url, **kwargs):
            reads.append(url)
            return {"url": url, "text": "late read"}
    request = replace(default_request(AgentCommand.LITERATURE_REVIEW), schema_version=14)
    host, _ = _host(request, Web())
    endpoint = AgentToolServer(tmp_path / "web.sock", request.attempt_id, host.handle_agent_tool, scope="literature")
    try:
        with ThreadPoolExecutor(max_workers=1) as client:
            future = client.submit(call_tool, str(endpoint.socket_path), "web_fetch", {"url": URL})
            deadline = time.monotonic() + 2
            while endpoint._calls.empty() and time.monotonic() < deadline:
                time.sleep(0.002)
            pending = endpoint._calls.get_nowait()
            pending.deadline_at = time.monotonic() - 1
            endpoint._calls.put(pending)
            with pytest.raises(ValueError, match="expired"):
                _finish(endpoint, future)
            assert reads == [] and host.web_tool_results == {}
    finally:
        endpoint.close()


def _host(request, web):
    row = {"kind": "AGENT", "state": "OPEN", "host_generation": 1,
        "observation_json": json.dumps({"request": request.to_dict()})}
    host = object.__new__(WorkflowHost)
    host.generation = 1
    host.store = SimpleNamespace(get_attempt=lambda identity: row if identity == request.attempt_id else None,
        list_attempts=lambda **where: [])
    host.claude_agent_host = object()
    host.running_agents = {}
    host.grounding_web = web
    host.web_tool_results = {}
    host.agent_session_metadata = {}
    return host, row


@pytest.mark.parametrize("close_attempt", (False, True))
def test_slow_web_read_leaves_host_thread_free_and_late_closed_result_inert(tmp_path, close_attempt):
    owner = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    threads = []

    class Web:
        def web_fetch(self, url, **kwargs):
            threads.append(threading.get_ident())
            entered.set()
            assert release.wait(2)
            return {"url": url, "text": "actual source text"}

    request = replace(default_request(AgentCommand.LITERATURE_REVIEW), schema_version=14)
    host, row = _host(request, Web())
    endpoint = AgentToolServer(tmp_path / "web.sock", request.attempt_id, host.handle_agent_tool, scope="literature")
    timer = threading.Timer(0.4, release.set)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call_tool, str(endpoint.socket_path), "web_fetch", {"url": URL})
            deadline = time.monotonic() + 2
            while endpoint._calls.empty() and time.monotonic() < deadline:
                time.sleep(0.005)
            timer.start()
            started = time.monotonic()
            endpoint.service()
            assert time.monotonic() - started < 0.2, "slow web IO blocked the owning Host tick"
            assert entered.wait(1)
            assert threads and threads[0] != owner
            assert host.web_tool_results == {}
            if close_attempt:
                row["state"] = "CLOSED"
            release.set()
            if close_attempt:
                with pytest.raises(ValueError, match="current OPEN"):
                    _finish(endpoint, future)
                assert host.web_tool_results == {}
            else:
                assert _finish(endpoint, future)["text"] == "actual source text"
                assert host.web_tool_results[request.attempt_id][0]["result"]["url"] == URL
    finally:
        release.set(); timer.cancel(); endpoint.close()


@pytest.mark.parametrize("delivered", (False, True))
def test_current_native_archive_not_host_completion_controls_citation(tmp_path, delivered):
    request = replace(default_request(AgentCommand.LITERATURE_REVIEW), schema_version=14)
    host, _ = _host(request, None)
    host.publisher = ArtifactPublisher(tmp_path / "artifacts")
    stage = tmp_path / "stage"; stage.mkdir()
    native = web_transcript(result={"url": URL, "text": "actual source text"})
    native[0]["message"]["content"][0]["input"] = {"url": URL}
    if not delivered:
        native[-1]["message"]["content"][0] = {"type": "tool_result", "tool_use_id": "web-1",
            "is_error": True, "content": "timed out"}
    (stage / "transcript.jsonl").write_bytes(b"\n".join(json.dumps(item).encode() for item in native))
    key = AgentArtifactKey(run_id=request.run_id, attempt_id=request.attempt_id)
    publication = host.publisher.publish(key, stage).to_dict()
    publication["path"] = "/".join(key.as_tuple())
    host.agent_session_metadata[request.attempt_id] = {"session_id": "session-1", "transcript": publication}
    host.web_tool_results[request.attempt_id] = [{"tool": "web_fetch", "arguments": {"url": URL},
        "status": "success", "result": {"url": URL}}]
    assert host._grounding_retrieved_urls(request) == ({URL} if delivered else set())
    if not delivered:
        with pytest.raises(ValueError, match="actually retrieved"):
            validate_web_sources([URL], host._grounding_retrieved_urls(request))
    records = host._grounding_completed_web_metadata(request)["web_tool_results"]
    assert records[0]["status"] == ("success" if delivered else "error")
    assert records[0]["result"] == {"url": URL}
    if not delivered:
        assert records[0]["error"]["code"] == "response_not_observed"
