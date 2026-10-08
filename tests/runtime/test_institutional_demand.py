"""Demand-driven browser work, bounded native waiting and ordinary source admission."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.grounding_background import delivered_web_metadata, web_result_metadata
from hypoloom.grounding_web import GroundingWebError
from hypoloom.host_tools_server import AgentToolServer, call_tool
from hypoloom.institutional_access import InstitutionalAccess, MAX_REQUESTS
from hypoloom.literature import FixedLiteratureIO, LITERATURE_PACKET_MEDIA_TYPE
from hypoloom.paper_sources import PaperWorkspace, prepare_sources, web_tool
from tests.runtime.test_institutional_access import ALL_PROVIDERS_AUTO, Backend, SCOPE, URL, PDF, pump
from tests.runtime.test_literature import FakeFetcher, _papers, _query


@pytest.mark.parametrize("mode", ["auto", "public_only"])
def test_metadata_packet_does_not_eagerly_queue_unselected_papers(tmp_path, mode):
    candidates = [replace(p, landing_uri=URL, source_uri=URL, pdf_uri=None) for p in _papers()]
    retrieval = FixedLiteratureIO(FakeFetcher(papers=candidates,
        resolutions={"www.nature.com": ("93.184.216.34",)})).retrieve(_query())
    assert not retrieval.pdf_artifacts
    request = replace(default_request(AgentCommand.GROUNDING), schema_version=20,
        required_output_schema="GroundingCandidate/v3", input_artifacts=(EmbeddedInput.from_bytes(
            "test--literature-packet", LITERATURE_PACKET_MEDIA_TYPE, retrieval.packet.to_bytes()),))
    backend = Backend()
    access = InstitutionalAccess(backend, provider_modes=dict.fromkeys(ALL_PROVIDERS_AUTO, mode))
    workspace = SimpleNamespace(request=request)
    try:
        prepare_sources(SimpleNamespace(state_root=tmp_path, institutional_access=access), workspace, retrieval)
        access.tick({request.run_id})
        assert access.status(request.run_id)["pending"] == 0
        assert not backend.calls and not access._items
        index = (workspace.papers.root / 'INDEX.md').read_text()
        choices = [json.loads(line[2:]) for line in index.splitlines() if line.startswith('- {')]
        assert len(choices) == 3 and not workspace.papers.sources
        assert all(c['institutional_mode'] == mode and c['url'] == URL
                   and c['batch_full_text'] == 'PDF_UNAVAILABLE' and 'source_ref' not in c for c in choices)
    finally:
        if hasattr(workspace, "papers"): workspace.papers.close()
        access.close()


@pytest.mark.parametrize("already_available", [False, True])
def test_explicit_peer_requests_share_fetch_but_not_scope_or_identity(already_available):
    backend = Backend(); backend.state = "available"
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    peer = (SCOPE[0], "peer", "peer-component")
    try:
        access.request(SCOPE, URL)
        if already_available:
            pump(access, lambda: access.status(SCOPE[0])["available"] == 1)
        assert access.peek(peer, URL) is None
        access.request(peer, URL)
        pump(access, lambda: "content" in access.peek(peer, URL))
        assert len(backend.calls) == 1 and access.peek(peer, URL)["content"] == PDF
        assert access.peek(peer, URL)["acquisition_source"] == "browser"
        assert access.request(("another-run", *peer[1:]), URL)["status"] == "pending"
        wrong = access.request((SCOPE[0], "wrong", "component"), URL, identity="doi:10.1038/another-paper")
        assert wrong["reason"] == "identity_unconfirmed" and "content" not in wrong
    finally:
        access.close()


def test_preferences_do_not_evict_verified_bytes_before_failures():
    backend = Backend(); backend.state = "available"
    access = InstitutionalAccess(backend, provider_modes={"Springer Nature": "auto", "ScienceDirect": "public_only"})
    try:
        access.request(SCOPE, URL)
        pump(access, lambda: access.status(SCOPE[0])["available"] == 1)
        for n in range(MAX_REQUESTS * 2):
            assert access.request(SCOPE, f"https://doi.org/10.1016/example.{n}")["reason"] == "public_only"
        assert access.peek(SCOPE, URL)["content"] == PDF
        assert len(access._items) == MAX_REQUESTS and len(backend.calls) == 1
    finally:
        access.close()


def test_manually_imported_bytes_are_not_reused_across_experts():
    backend = Backend(); access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    try:
        access.request(SCOPE, URL)
        access.control(SCOPE[0], "Springer Nature", "import", url=URL, path="/fixture/authorized.pdf")
        pump(access, lambda: access.status(SCOPE[0])["available"] == 1)
        assert access.peek(SCOPE, URL)["acquisition_source"] == "manual_import"
        assert access.request((SCOPE[0], "peer", "component"), URL)["status"] == "pending"
    finally:
        access.close()


def test_peer_reuse_preserves_existing_cache_byte_limit(monkeypatch):
    from tests.runtime.test_institutional_access import document
    doc = document()
    size = len(doc['content']) + sum(len(t.encode()) for t in doc['pages'])
    monkeypatch.setattr('hypoloom.institutional_access.MAX_CACHE_BYTES', size)
    backend = Backend(); backend.state = 'available'
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    try:
        access.request(SCOPE, URL)
        pump(access, lambda: access.status(SCOPE[0])['available'] == 1)
        reply = access.request((SCOPE[0], 'peer', 'component'), URL)
        assert reply['status'] == 'unavailable' and reply['reason_code'] == 'cache_limit'
        assert len(backend.calls) == 1 and access.peek(SCOPE, URL)['content'] == PDF
    finally:
        access.close()


@pytest.mark.parametrize('mode', ['document', 'text'])
@pytest.mark.parametrize("backend_state", ["available", "verification_required"])
def test_one_mcp_call_waits_then_delivers_or_finishes_without_human_input(tmp_path, backend_state, mode):
    backend = Backend(); backend.state = backend_state
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    request = replace(default_request(AgentCommand.GROUNDING), schema_version=20,
                      required_output_schema="GroundingCandidate/v3")
    papers = PaperWorkspace(tmp_path / "papers", request)
    class Public:
        def document(self, url):
            raise GroundingWebError("http_error", "HTTP 403")
    host = SimpleNamespace(grounding_web=Public(), institutional_access=access, literature_io=None,
        running_agents={request.attempt_id: SimpleNamespace(tool_workspace=SimpleNamespace(papers=papers))},
        _agent_tool_request=lambda *a, **k: None, web_tool_results={})
    args = {"mode": "document", "url": URL} if mode == 'document' else {"url": URL}
    server = AgentToolServer(tmp_path / "tools.sock", request.attempt_id,
        lambda attempt, tool, arguments: web_tool(host, request, tool, arguments, defer=True), scope="literature")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call_tool, str(server.socket_path), "web_fetch", args)
            deadline = time.monotonic() + 5
            while not future.done() and time.monotonic() < deadline:
                access.tick({request.run_id})
                start = time.monotonic()
                server.service()
                assert time.monotonic() - start < .2
                time.sleep(.005)
            result = future.result(timeout=.5)
        assert len(backend.calls) == 1
        if backend_state == "available" and mode == 'document':
            assert result["status"] == "available" and len(papers.sources) == 1
            assert result["acquisition_source"] == papers.sources[0]["acquisition_source"] == "browser"
            archive = tmp_path / "archive"; archive.mkdir()
            papers.archive(archive)
            assert (archive / result["original_path"].lstrip("/")).read_bytes() == PDF
        elif backend_state == 'available':
            assert result['text_scope'] == 'pdf_physical_pages' and 'Methods:' in result['text']
            assert not papers.sources
        else:
            assert result["status"] == "unavailable" and result["reason"] == "verification_required"
            assert not papers.sources
        # Only the final native result establishes delivery; internal wait polls
        # must not appear as lost successful responses or additional model calls.
        records = delivered_web_metadata(host.web_tool_results[request.attempt_id],
            [{"tool": "web_fetch", "arguments": args, "status": "success", "result": web_result_metadata(result)}])
        assert sum(r["status"] == "success" for r in records) == 1
        assert not any(r["status"] == "error" for r in records)
        assert '"content":' not in json.dumps(records)
    finally:
        server.close(); papers.close(); access.close()
