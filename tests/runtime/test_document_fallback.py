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
LOOKUPS = ["https://api.openalex.org/works/https://doi.org/" + DOI, "https://api.crossref.org/works/" + DOI]


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
    assert public.calls == [URL, *LOOKUPS, OA] and len(metadata.calls) == 1 and not backend.calls
    assert web_tool(host, request, "web_fetch", args) == result
    assert public.calls == [URL, *LOOKUPS, OA] and len(papers.sources) == 1
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
    assert public.calls == [URL, *LOOKUPS, OA]


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


def test_host_discovers_public_pdf_without_locator_and_archives_exact_bytes(system, tmp_path):
    from tests.runtime.test_literature_probe import system as public_system, DOI as sample, PDF
    from tests.runtime.test_grounding_web import _response
    from tests.runtime.test_institutional_browser import _pdf
    host, request, _, _, backend, papers = system
    web, transport, _ = public_system(locations=(PDF.replace('https:', 'http:'),))
    url = 'https://doi.org/' + sample
    original = _pdf(sample)
    transport.responses[url] = _response(url, b'', status_code=403)
    transport.responses[PDF] = _response(PDF, original, media_type='application/pdf')
    host.grounding_web, host.literature_io = web, None
    result = web_tool(host, request, 'web_fetch', {'mode': 'document', 'url': url})
    assert result['status'] == 'available' and result['url'] == PDF
    assert result['requested_url'] == url and result['media_type'] == 'application/pdf'
    assert not backend.calls and len(papers.sources) == 1
    archive = tmp_path / 'public-archive'; archive.mkdir()
    papers.archive(archive)
    assert (archive / result['original_path'].lstrip('/')).read_bytes() == original
    assert sample in (archive / result['text_path'].lstrip('/')).read_text()
    before = list(transport.calls)
    assert web_tool(host, request, 'web_fetch', {'mode': 'document', 'url': url}) == result
    assert transport.calls == before


@pytest.mark.parametrize("lookup", ["openalex", "crossref"])
@pytest.mark.parametrize("outcome,status", [("HTTP_FAILURE", 429), ("FALLBACK_EMPTY", None)])
@pytest.mark.parametrize("wrong_pdf", [False, True])
def test_batch_locator_history_does_not_skip_independent_public_discovery(
        system, tmp_path, monkeypatch, lookup, outcome, status, wrong_pdf):
    from hypoloom.grounding_web import GroundingWeb
    from tests.runtime.test_grounding_web import Transport, _response
    from tests.runtime.test_institutional_browser import _pdf
    host, request, metadata, _, backend, papers = system
    trace = [{"bibliographic_identity": "doi:" + DOI, "events": [
        {"stage": "fallback_lookup", "outcome": outcome, "http_status": status}]}]
    monkeypatch.setattr("hypoloom.paper_sources.packet_download_trace", lambda *_: trace)
    work = {"doi": DOI, "display_name": "Controlled selected method", "locations": [
        {"is_oa": True, "pdf_url": OA}] if lookup == "openalex" else []}
    crossref = {"message": {"DOI": DOI, "title": ["Controlled selected method"],
        "link": [{"URL": OA, "content-type": "application/pdf"}]}}
    original = _pdf("10.1038/wrong" if wrong_pdf else DOI)
    transport = Transport({URL: _response(URL, b"denied", status_code=403),
        LOOKUPS[0]: _response(LOOKUPS[0], json.dumps(work).encode(), media_type="application/json"),
        LOOKUPS[1]: _response(LOOKUPS[1], json.dumps(crossref).encode(), media_type="application/json"),
        OA: _response(OA, original, media_type="application/pdf")})
    host.grounding_web = GroundingWeb(transport)
    result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
    assert not metadata.calls and not backend.calls  # The old locator is not retried.
    fetched = [call[1] for call in transport.calls if call[0] == "fetch"]
    assert fetched == [URL, *LOOKUPS[:1 if lookup == "openalex" else 2], OA]
    if wrong_pdf:
        assert result["status"] == "unavailable" and not papers.sources
    else:
        assert result["status"] == "available" and result["acquisition_source"] == "metadata"
        archive = tmp_path / "history-archive"; archive.mkdir()
        papers.archive(archive)
        assert (archive / result["original_path"].lstrip("/")).read_bytes() == original
        assert papers.sources[0]["acquisition_source"] == "metadata"
    before = list(transport.calls)
    assert web_tool(host, request, "web_fetch", {"mode": "document", "url": URL}) == result
    assert transport.calls == before


def test_challenged_pdf_is_not_retried_and_other_oa_copy_is_verified(monkeypatch):
    from time import monotonic
    from hypoloom.document_fallback import acquire_public_pdf
    from hypoloom.grounding_web import GroundingWeb
    from tests.runtime.test_grounding_web import _public_http_responses
    from tests.runtime.test_institutional_browser import _pdf
    from tests.runtime.test_literature_probe import DOI, METADATA, PDF

    challenged = "https://www.sciencedirect.com/science/article/pii/S2352711024000943/pdfft"
    work = {"doi": "https://doi.org/" + DOI, "display_name": "A controlled scholarly article",
            "locations": [{"is_oa": True, "pdf_url": url} for url in (challenged, PDF)]}
    metadata, original = json.dumps(work).encode(), _pdf(DOI)
    def response(content, media):
        return (f"HTTP/1.1 200 OK\r\nContent-Type: {media}\r\n"
                f"Content-Length: {len(content)}\r\n\r\n").encode() + content
    transport, calls = _public_http_responses(monkeypatch, {
        METADATA: response(metadata, "application/json"), PDF: response(original, "application/pdf"),
        challenged: b"HTTP/1.1 403 Forbidden\r\nCf-Mitigated: challenge\r\n"
                    b"Content-Length: 1207846\r\nContent-Type: text/html\r\n\r\n" + b" " * 1207846})
    events, budget = [], [12]
    document, _ = acquire_public_pdf(GroundingWeb(transport), "doi:" + DOI,
        supplied_urls=(challenged,), deadline=monotonic() + 5, http_calls=budget, events=events)
    assert document["content"] == original and document["url"] == PDF
    assert DOI in document["pages"][0]
    assert calls["requested"] == calls["closed"] == [METADATA, challenged, PDF]
    assert challenged not in calls["body_reads"] and budget == [9]
    assert events == [{"stage": "supplied", "reason": "challenge", "url": challenged,
                       "http_status": 403, "retrying": False}]


@pytest.mark.parametrize("initial_challenge", [False, True])
def test_host_trace_preserves_challenge_when_other_oa_copy_is_archived(system, tmp_path, initial_challenge):
    from tests.runtime.test_literature_probe import system as public_system, DOI as sample, PDF
    from tests.runtime.test_grounding_web import _response
    from tests.runtime.test_institutional_browser import _pdf
    host, request, _, _, backend, papers = system
    challenged = "https://dl.acm.org/doi/pdf/" + sample
    url = "https://dl.acm.org/doi/" + sample
    web, transport, _ = public_system(locations=(challenged, PDF))
    original = _pdf(sample)
    transport.responses[url] = _response(url, b"", status_code=403)
    transport.responses[PDF] = _response(PDF, original, media_type="application/pdf")
    fetch = transport.fetch
    def challenged_fetch(target, **kwargs):
        if target == challenged or initial_challenge and target == url:
            transport.calls.append(("fetch", target, kwargs))
            raise GroundingWebError("challenge", "web provider requires browser verification", http_status=403)
        return fetch(target, **kwargs)
    transport.fetch = challenged_fetch
    host.grounding_web, host.literature_io = web, None
    arguments = {"mode": "document", "url": url}
    result = web_tool(host, request, "web_fetch", arguments)
    assert result["status"] == "available" and result["url"] == PDF and not backend.calls
    trace = [event for item in io_trace(host, request) for event in item["events"]]
    direct = [event for event in trace if event["stage"] == "direct"]
    assert direct == ([{"stage": "direct", "outcome": "CHALLENGE", "source_host": "dl.acm.org",
                        "failure_code": "PDF_CHALLENGE", "http_status": 403}] if initial_challenge else [])
    challenged_event = next(event for event in trace
        if event.get("source_host") == "dl.acm.org" and event["stage"] == "fallback")
    assert challenged_event["outcome"] == "CHALLENGE"
    assert challenged_event["failure_code"] == "PDF_CHALLENGE"
    assert challenged_event["http_status"] == 403 and challenged_event["retrying"] is False
    before = list(transport.calls)
    assert sum(call[0] == "fetch" and call[1] == challenged for call in before) == 1
    assert sum(call[0] == "fetch" and call[1] == url for call in before) == 1
    assert web_tool(host, request, "web_fetch", arguments) == result and transport.calls == before
    archive = tmp_path / "challenge-fallback-archive"; archive.mkdir()
    papers.archive(archive)
    assert (archive / result["original_path"].lstrip("/")).read_bytes() == original


@pytest.mark.parametrize('redirected', [False, True])
def test_public_discovery_cannot_readmit_rejected_source(redirected):
    from time import monotonic
    from hypoloom.document_fallback import acquire_public_pdf
    from tests.runtime.test_literature_probe import system, DOI, PDF
    from tests.runtime.test_grounding_web import _response
    from tests.runtime.test_institutional_browser import _pdf
    web, transport, locator = system()
    rejected = 'https://repository.example.edu/rejected.pdf' if redirected else PDF
    transport.responses[PDF] = _response(PDF, b'', status_code=302, location=rejected) if redirected else _response(
        PDF, _pdf(DOI), media_type='application/pdf')
    transport.responses[rejected] = _response(rejected, _pdf(DOI), media_type='application/pdf')
    document, _ = acquire_public_pdf(web, 'doi:' + DOI, events=[], deadline=monotonic() + 5,
        http_calls=[12], reject=lambda url: url == rejected)
    assert document is None and not locator.calls
    if not redirected:
        assert all(call[1] != PDF for call in transport.calls)


@pytest.mark.parametrize('pdf_available', [True, False])
def test_readable_publisher_html_prefers_verified_pdf_but_survives_pdf_failure(system, pdf_available):
    from tests.runtime.test_literature_probe import system as public_system, DOI as sample, PDF
    from tests.runtime.test_grounding_web import _response
    from tests.runtime.test_institutional_browser import _pdf
    host, request, _, _, backend, papers = system
    web, transport, _ = public_system()
    url = 'https://doi.org/' + sample
    html = (f'<meta name="citation_doi" content="{sample}"><article><h2>Methods</h2>'
            + 'Detailed scientific method. ' * 200 + '<h2>References</h2></article>').encode()
    transport.responses[url] = _response(url, html)
    transport.responses[PDF] = _response(PDF, _pdf(sample) if pdf_available else b'',
        status_code=200 if pdf_available else 403, media_type='application/pdf')
    host.grounding_web, host.literature_io = web, None
    result = web_tool(host, request, 'web_fetch', {'mode': 'document', 'url': url})
    assert result['status'] == 'available'
    assert result['media_type'] == ('application/pdf' if pdf_available else 'text/html')
    assert result['url'] == (PDF if pdf_available else url)
    assert result['requested_url'] == url and len(papers.sources) == 1 and not backend.calls
