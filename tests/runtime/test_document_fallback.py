from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.grounding_web import GroundingWebError
from hypoloom.institutional_access import InstitutionalAccess
from hypoloom.literature import FixedLiteratureIO, UnpaywallPdfLocator, OpenAlexResponse
from hypoloom.paper_sources import PaperWorkspace, web_tool
from tests.runtime.test_institutional_access import Backend, URL, document
from test_literature import FakeFetcher

OA = "https://repository.example.edu/accepted.pdf"
DOI = "10.1038/" + URL.rsplit("/", 1)[-1]


class Metadata:
    def __init__(self): self.calls = []
    def get(self, **kwargs):
        self.calls.append(kwargs)
        body = json.dumps({"doi": DOI, "is_oa": True,
                           "best_oa_location": {"url_for_pdf": OA}}).encode()
        return OpenAlexResponse(status_code=200, media_type="application/json", content_length=len(body), body=body)


@pytest.fixture
def system(tmp_path):
    request = replace(default_request(AgentCommand.GROUNDING), schema_version=20, required_output_schema="GroundingCandidate/v3")
    papers = PaperWorkspace(tmp_path, request)
    backend = Backend()
    access = InstitutionalAccess(backend, provider_modes={"Nature": "public_only"})
    metadata = Metadata()
    io = FixedLiteratureIO(FakeFetcher(), fallback_locator=UnpaywallPdfLocator(email="reader@example.org", transport=metadata))
    class Public:
        def __init__(self): self.calls = []; self.wrong = False; self.unsafe = False
        def document(self, url):
            self.calls.append(url)
            if self.unsafe: raise GroundingWebError("unsafe_url", "denied source")
            if url != OA: raise GroundingWebError("http_error", "HTTP 403")
            result = {**document(), "url": OA, "requested_url": OA}
            if self.wrong: result["pages"] = ("Different paper DOI 10.1038/wrong",)
            return result
    public = Public()
    host = SimpleNamespace(grounding_web=public, institutional_access=access, literature_io=io,
        running_agents={request.attempt_id: SimpleNamespace(tool_workspace=SimpleNamespace(papers=papers))},
        _agent_tool_request=lambda *a, **k: None, web_tool_results={})
    try:
        yield host, request, metadata, public, backend, papers
    finally:
        papers.close(); access.close()


def test_public_only_platform_still_fetches_exact_doi_oa_and_preserves_origin(system, tmp_path):
    host, request, metadata, public, backend, papers = system
    args = {"mode": "document", "url": URL}
    result = web_tool(host, request, "web_fetch", args)
    assert result["status"] == "available"
    assert result["url"] == OA and result["requested_url"] == URL
    assert "publisher version of record is not established" in result["version_note"]
    assert public.calls == [URL, OA] and len(metadata.calls) == 1 and not backend.calls
    assert web_tool(host, request, "web_fetch", args) == result
    assert public.calls == [URL, OA] and len(papers.sources) == 1
    archive = tmp_path / "archive"; archive.mkdir()
    papers.archive(archive)
    assert (archive / result["original_path"].lstrip("/")).read_bytes() == document()["content"]
    assert io_trace(host, request)[0]["events"][0]["stage"] == "fallback_lookup"


def io_trace(host, request):
    return host.web_tool_results[request.attempt_id][0]["literature_download_trace"]


def test_oa_identity_mismatch_is_not_admitted_and_repeat_does_not_relookup(system):
    host, request, metadata, public, backend, papers = system
    public.wrong = True
    for _ in range(3):
        result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
        assert result["status"] == "unavailable" and result["reason"] == "public_only"
    assert len(metadata.calls) == 1 and not papers.sources and not backend.calls
    assert public.calls == [URL, OA]


def test_original_source_security_rejection_precedes_oa_lookup(system):
    host, request, metadata, public, backend, papers = system
    public.unsafe = True
    result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
    assert result["error"]["code"] == "unsafe_url"
    assert not metadata.calls and not backend.calls and not papers.sources


def test_shared_pdf_slots_and_locator_rate_limit_do_not_start_more_work(system):
    host, request, metadata, public, backend, _ = system
    assert host.literature_io.pdf_slots.acquire(False)
    try:
        result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
        assert result["error"]["code"] == "paper_io_busy"
        assert not public.calls and not metadata.calls
    finally:
        host.literature_io.pdf_slots.release()
    host.literature_io._fallback_retry_at = host.literature_io._clock() + 60
    assert web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})["reason"] == "public_only"
    assert not metadata.calls and not backend.calls
