from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import hypoloom.grounding_web as web_module
from hypoloom.grounding_web import GroundingWeb, GroundingWebError, PublicHttpsTransport


def test_opt_in_academic_fallback_preserves_primary_failure_and_auth_boundary():
    from hypoloom.literature import OpenAlexLiteratureFetcher, OpenAlexResponse

    calls = []
    class Api:
        def get(self, **kwargs):
            calls.append(kwargs)
            body = json.dumps({"results": [{"id": "https://openalex.org/W1",
                "display_name": "Causal emotion recognition", "publication_year": 2024,
                "best_oa_location": {"landing_page_url": "https://arxiv.org/abs/2401.00001"}}]}).encode()
            return OpenAlexResponse(status_code=200, media_type="application/json",
                                    content_length=len(body), body=body)
    class Unreachable(Transport):
        def fetch(self, url, **kwargs):
            raise OSError(101, "Network is unreachable")

    fallback = OpenAlexLiteratureFetcher(api_key="fixture-key", transport=Api())
    fallback._last_retry_trace = ({"sentinel": "prior literature read"},)
    result = GroundingWeb(Unreachable({}), search_fallback=fallback).web_search("causal emotion recognition", max_results=3)
    assert result["provider"] == "openalex" and result["search_scope"] == "academic_literature"
    assert result["primary_error"] == {"provider": "duckduckgo-html", "code": "transport_failed"}
    assert result["results"][0]["url"] == "https://arxiv.org/abs/2401.00001"
    assert "Full text has not been fetched" in result["results"][0]["snippet"]
    assert len(calls) == 1 and calls[0]["host"] == "api.openalex.org"
    assert calls[0]["headers"]["Authorization"] == "Bearer fixture-key"
    assert parse_qs(urlsplit(calls[0]["target"]).query)["search"] == ["causal emotion recognition"]
    assert calls[0]["timeout_seconds"] <= 10
    assert "fixture-key" not in json.dumps(result)
    assert fallback._last_retry_trace == ({"sentinel": "prior literature read"},)
    with pytest.raises(GroundingWebError, match="transport_failed"):
        GroundingWeb(Unreachable({})).web_search("query")
    assert len(calls) == 1


def test_explicit_academic_scope_skips_web_transport_and_preserves_metadata():
    class Academic:
        calls = []

        def web_search(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return {"provider": "openalex", "query": query,
                    "search_scope": "academic_literature", "results": [],
                    "source_is_untrusted": True}

    transport = Transport({})
    academic = Academic()
    result = GroundingWeb(transport, search_fallback=academic).web_search(
        "speaker generalization", scope="academic", max_results=4)
    assert result["provider"] == "openalex" and "primary_error" not in result
    assert academic.calls[0][0] == "speaker generalization"
    assert academic.calls[0][1]["max_results"] == 4
    assert transport.calls == []
    with pytest.raises(GroundingWebError, match="academic_unavailable"):
        GroundingWeb(transport).web_search("speaker generalization", scope="academic")
    assert transport.calls == []


def test_document_extraction_retains_methods_without_summary_headings():
    from hypoloom.literature import PopplerPdfExtractor, LiteratureValidationError

    original = b"%PDF-1.4\nfixture bytes\n%%EOF"
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs["input"]))
        output = (b"Pages: 2\n" if "pdfinfo" in command[0] else
                  b"Definitions\nUse only the causal prefix.\f"
                  b"3 Method\nSet the decay coefficient to 0.83.\f")
        return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

    extractor = PopplerPdfExtractor(runner=runner)
    document = extractor.extract_document(original, max_pages=2)
    assert document.page_count == 2
    assert "0.83" in document.pages[1] and "causal prefix" in document.pages[0]
    assert all(content == original for _, content in calls)
    # Readable pages survive absent headings without fabricated summary claims.
    packet_text = extractor.extract_pdf(original, max_pages=2)
    assert packet_text.pages == document.pages
    assert packet_text.abstract_summary == packet_text.conclusions == ""
    assert packet_text.claim_support == ()
    with pytest.raises(LiteratureValidationError, match="page_count"):
        extractor.extract_document(original, max_pages=1)


@pytest.mark.parametrize("primary", ["empty", "unsafe"])
def test_fallback_does_not_replace_valid_empty_results_or_security_rejections(primary):
    class Never:
        def web_search(self, *args, **kwargs):
            raise AssertionError("fallback must not run")
    transport = Transport({SEARCH: _response(SEARCH, b'<div class="no-results">None</div>')})
    if primary == "unsafe": transport.dns = ("127.0.0.1",)
    web = GroundingWeb(transport, search_fallback=Never())
    if primary == "unsafe":
        with pytest.raises(GroundingWebError, match="unsafe_url"): web.web_search("project evaluation")
    else:
        assert web.web_search("project evaluation")["results"] == []


def test_fallback_failure_is_not_an_empty_search_or_unbounded_retry():
    from hypoloom.literature import LiteratureFetchError
    class Limited:
        calls = 0
        def web_search(self, *args, **kwargs):
            self.calls += 1
            raise LiteratureFetchError("limited", http_status=429)
    fallback = Limited()
    web = GroundingWeb(Transport({SEARCH: _response(SEARCH, b"", status_code=429)}), search_fallback=fallback)
    with pytest.raises(GroundingWebError, match="search_backends_failed") as error:
        web.web_search("project evaluation")
    assert "rate_limited" in str(error.value) and fallback.calls == 1


def test_public_connect_uses_one_deadline_and_only_preapproved_addresses(monkeypatch):
    import hypoloom.grounding_web as module
    now = [10.0]; calls = []
    class Sock:
        def __init__(self, *args): pass
        def settimeout(self, seconds): calls.append(("timeout", seconds))
        def connect(self, address):
            calls.append(("connect", address)); now[0] += 0.75
            raise TimeoutError("fixture timeout")
        def close(self): calls.append(("close",))
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs): pass
        def connect(self): self.sock = self._create_connection(("ignored", 443), 15, None)
        def close(self): pass
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.socket, "socket", Sock)
    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)
    with pytest.raises((TimeoutError, GroundingWebError)):
        PublicHttpsTransport().fetch(DOC, approved_addresses=("8.8.8.8", "1.1.1.1"),
                                     max_bytes=100, timeout_seconds=1)
    assert [item for item in calls if item[0] == "timeout"] == [("timeout", 1), ("timeout", 0.25)]
    assert [item[1][0] for item in calls if item[0] == "connect"] == ["8.8.8.8", "1.1.1.1"]
    assert calls.count(("close",)) == 2
from hypoloom.literature import ClaimPageRef, FetchResponse, PdfExtraction


SEARCH = "https://html.duckduckgo.com/html/?q=project+evaluation"
DOC = "https://example.org/docs"


def _response(url: str, body: bytes = b"observed body", **changes) -> FetchResponse:
    return FetchResponse(requested_uri=url, status_code=200, peer_address="8.8.8.8",
                         media_type="text/html; charset=utf-8", content_length=len(body),
                         location=None, body=body) if not changes else replace(_response(url, body), **changes)


class Transport:
    def __init__(self, responses: dict[str, FetchResponse]):
        self.responses = responses
        self.dns = ("8.8.8.8",)
        self.calls = []

    def resolve(self, host: str):
        self.calls.append(("resolve", host))
        return self.dns

    def fetch(self, url: str, **kwargs):
        self.calls.append(("fetch", url, kwargs))
        return self.responses[url]


def _public_http_responses(monkeypatch, responses):
    """Exercise real HTTP header parsing and public transport over saved bytes."""
    import io
    calls = {"requested": [], "body_reads": [], "closed": []}
    class Body(io.BytesIO):
        def read1(self, size=-1):
            calls["body_reads"].append(self.url)
            return super().read1(size)
    class Connection:
        def __init__(self, host, port, timeout):
            self.host = host
            self.sock = SimpleNamespace(getpeername=lambda: ("8.8.8.8", 443), settimeout=lambda value: None)
        def connect(self): pass
        def request(self, method, target, headers):
            self.url = "https://" + self.host + target
            calls["requested"].append(self.url)
        def getresponse(self):
            body = Body(responses[self.url]); body.url = self.url
            self.response = web_module.http.client.HTTPResponse(SimpleNamespace(makefile=lambda *args: body))
            self.response.begin()
            return self.response
        def close(self):
            self.response.close()
            calls["closed"].append(self.url)
    monkeypatch.setattr(web_module.http.client, "HTTPSConnection", Connection)
    transport = PublicHttpsTransport()
    transport.resolve = lambda hostname: ("8.8.8.8",)
    return transport, calls


@pytest.mark.parametrize("status", [200, 403])
def test_public_challenge_header_ends_before_reading_large_html(monkeypatch, status):
    # ScienceDirect's observed 1.2 MB page places its challenge markup after 100 KB.
    body = b" " * 1207800 + b"<h1>Are you a robot?</h1>"
    wire = (f"HTTP/1.1 {status} Response\r\nContent-Type: text/html\r\n"
            f"Content-Length: {len(body)}\r\ncF-mItIgAtEd: challenge\r\n\r\n").encode() + body
    transport, calls = _public_http_responses(monkeypatch, {DOC: wire})
    with pytest.raises(GroundingWebError) as failure:
        GroundingWeb(transport, max_response_bytes=1024).document(DOC)
    assert failure.value.code == "challenge" and failure.value.http_status == status
    assert calls == {"requested": [DOC], "body_reads": [], "closed": [DOC]}
    assert "Are you a robot" not in str(failure.value)


@pytest.mark.parametrize("marker", ["", "Cf-Mitigated: other\r\n"])
def test_public_403_without_challenge_header_stays_http_error(monkeypatch, marker):
    wire = ("HTTP/1.1 403 Forbidden\r\nContent-Type: text/html\r\n"
            "Content-Length: 6\r\n" + marker + "\r\ndenied").encode()
    transport, calls = _public_http_responses(monkeypatch, {DOC: wire})
    with pytest.raises(GroundingWebError) as failure:
        GroundingWeb(transport).document(DOC)
    assert failure.value.code == "http_error" and failure.value.http_status == 403
    assert calls["requested"] == calls["closed"] == [DOC]
    assert calls["body_reads"] == [DOC]


def test_agent_selected_query_returns_actual_public_html_results_and_snippets():
    body = b'''<html><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fdocs">Project <b>guide</b></a>
<a class="result__snippet">Run the <b>evaluation</b> entrypoint.</a>
<a class="result__a" href="https://example.org/metrics">Metrics</a><div class="result__snippet">Observed metrics</div></html>'''
    transport = Transport({SEARCH: _response(SEARCH, body)})
    result = GroundingWeb(transport).web_search("project evaluation", max_results=1)
    assert result["query"] == "project evaluation"
    assert result["results"] == [{"url": DOC, "title": "Project guide",
                                  "snippet": "Run the evaluation entrypoint.", "truncated": False}]
    assert result["provider"] == "duckduckgo-html" and result["truncated"] is True
    assert result["source_is_untrusted"] is True
    assert parse_qs(urlsplit(transport.calls[1][1]).query) == {"q": ["project evaluation"]}


@pytest.mark.parametrize("body,code", [
    (b'<form id="challenge-form" action="/anomaly.js">Solve this CAPTCHA</form>', "challenge"),
    (b"<p>Unfortunately, bots use DuckDuckGo too.</p>", "challenge"),
    (b"<html>Provider layout changed</html>", "invalid_provider_response"),
])
def test_challenge_or_unrecognized_markup_is_never_fabricated_empty_search(body: bytes, code: str):
    with pytest.raises(GroundingWebError) as error:
        GroundingWeb(Transport({SEARCH: _response(SEARCH, body)})).web_search("project evaluation")
    assert error.value.code == code


def test_actual_no_result_marker_is_distinguished_from_provider_failure():
    response = _response(SEARCH, b'<div class="no-results">No results found</div>')
    assert GroundingWeb(Transport({SEARCH: response})).web_search("project evaluation")["results"] == []


def test_fetch_follows_checked_redirect_and_extracts_text_without_executing_html():
    final = "https://example.org/manual"
    body = b'<title>Manual</title><script>steal_credentials()</script><h1>Evaluation</h1><p>python evaluate.py</p><a href="/metrics#score">Metrics</a>'
    transport = Transport({DOC: _response(DOC, b"", status_code=302, location="/manual"),
                           final: _response(final, body)})
    result = GroundingWeb(transport).web_fetch(DOC)
    assert result["requested_url"] == DOC and result["url"] == final
    assert "python evaluate.py" in result["text"]
    assert "steal_credentials" not in result["text"]
    assert result["links"] == ["https://example.org/metrics"]
    assert result["title"] == "Manual" and result["text_scope"] == "document_text"
    assert [call[0] for call in transport.calls] == ["resolve", "fetch", "resolve", "fetch"]


@pytest.mark.parametrize("url", ["http://example.org", "file:///etc/passwd", "https://user:secret@example.org",
                                "https://localhost", "https://127.0.0.1", "https://169.254.169.254",
                                "https://example.org:8443", "https://example.org/#fragment"])
def test_unsafe_url_rejected_before_dns_or_http(url: str):
    transport = Transport({})
    with pytest.raises(GroundingWebError, match="unsafe_url"):
        GroundingWeb(transport).web_fetch(url)
    assert transport.calls == []


@pytest.mark.parametrize("count", [12, 32])
def test_document_accepts_public_cdn_address_sets(count):
    transport = Transport({DOC: _response(DOC, b"article text", media_type="text/plain")})
    transport.dns = tuple(f"8.8.8.{index}" for index in range(1, count + 1))
    document = GroundingWeb(transport).document(DOC)
    assert document["content"] == b"article text"
    assert transport.calls[1][2]["approved_addresses"] == tuple(sorted(transport.dns))


@pytest.mark.parametrize("damage", ["too-many", "mixed-dns", "changed-peer"])
def test_large_cdn_address_sets_keep_dns_and_peer_checks(damage):
    transport = Transport({DOC: _response(DOC, b"article text", media_type="text/plain")})
    transport.dns = tuple(f"8.8.8.{index}" for index in range(1, 33))
    if damage == "too-many":
        transport.dns += ("8.8.8.33",)
    elif damage == "mixed-dns":
        transport.dns = (*transport.dns[:-1], "127.0.0.1")
    else:
        transport.responses[DOC] = replace(transport.responses[DOC], peer_address="1.1.1.1")
    with pytest.raises(GroundingWebError, match="unsafe_url"):
        GroundingWeb(transport).document(DOC)
    assert len([call for call in transport.calls if call[0] == "fetch"]) == (damage == "changed-peer")


@pytest.mark.parametrize("damage", ["private-dns", "mixed-dns", "private-peer", "changed-peer", "changed-url", "length", "bytes"])
def test_dns_peer_identity_and_transport_bounds_fail_closed(damage: str):
    response = _response(DOC)
    transport = Transport({DOC: response})
    if damage == "private-dns":
        transport.dns = ("127.0.0.1",)
    elif damage == "mixed-dns":
        transport.dns = ("8.8.8.8", "10.0.0.1")
    elif damage in {"private-peer", "changed-peer"}:
        response = replace(response, peer_address="127.0.0.1" if damage == "private-peer" else "1.1.1.1")
    elif damage == "changed-url":
        response = replace(response, requested_uri="https://example.org/other")
    elif damage == "length":
        response = replace(response, content_length=100)
    transport.responses[DOC] = response
    with pytest.raises(GroundingWebError):
        GroundingWeb(transport, max_response_bytes=1 if damage == "bytes" else 100).web_fetch(DOC)
    if damage in {"private-dns", "mixed-dns"}:
        assert not any(call[0] == "fetch" for call in transport.calls)


@pytest.mark.parametrize("location", ["https://127.0.0.1/private", DOC])
def test_redirect_cannot_enter_private_network_or_loop(location: str):
    transport = Transport({DOC: _response(DOC, b"", status_code=302, location=location)})
    with pytest.raises(GroundingWebError):
        GroundingWeb(transport).web_fetch(DOC)
    assert len([call for call in transport.calls if call[0] == "fetch"]) == 1


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (503, "http_error"), (202, "http_error")])
def test_http_failure_is_visible(status: int, code: str):
    with pytest.raises(GroundingWebError) as error:
        GroundingWeb(Transport({SEARCH: _response(SEARCH, b"", status_code=status)})).web_search("project evaluation")
    assert error.value.code == code


def test_plain_text_truncation_is_explicit_and_pdf_scope_is_honest():
    response = _response(DOC, b"complete document body", media_type="text/plain")
    result = GroundingWeb(Transport({DOC: response})).web_fetch(DOC, max_chars=8)
    assert result["text"] == "complete" and result["truncated"] is True
    pdf = b"%PDF-1.4\nfixture\n%%EOF"
    from hypoloom.literature import PdfDocument
    extraction = PdfDocument(page_count=2, pages=("Observed abstract", "Methods: exact coefficient 0.83\nObserved conclusion"))
    extractor = SimpleNamespace(extract_document=lambda content, max_pages: extraction)
    result = GroundingWeb(Transport({DOC: _response(DOC, pdf, media_type="application/pdf")}),
                          pdf_extractor=extractor).web_fetch(DOC)
    assert "Physical page 2" in result["text"] and "exact coefficient 0.83" in result["text"]
    assert result["text_scope"] == "pdf_physical_pages"


def test_default_https_transport_never_forwards_ambient_credentials(monkeypatch):
    calls = []
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "private-host-key")
    monkeypatch.setenv("HTTP_PROXY", "http://user:password@proxy.invalid")
    class Response:
        status = 200
        def getheader(self, name):
            return {"Content-Type": "text/plain", "Content-Length": "4"}.get(name)
        def read1(self, size):
            content = getattr(self, "content", b"safe")
            self.content = b""
            return content
    class Connection:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))
            self.sock = SimpleNamespace(getpeername=lambda: ("8.8.8.8", 443), settimeout=lambda value: None)
        def connect(self):
            pass
        def request(self, method, target, headers):
            calls.append((method, target, headers))
        def getresponse(self):
            self.sock = None  # http.client closes its own reference for Connection: close.
            return Response()
        def close(self):
            calls.append("closed")
    monkeypatch.setattr(web_module.http.client, "HTTPSConnection", Connection)
    result = PublicHttpsTransport().fetch(DOC, approved_addresses=("8.8.8.8",), max_bytes=10)
    assert result.body == b"safe"
    assert set(calls[1][2]) == {"Accept", "User-Agent"}
    assert "private-host-key" not in str(calls) and "password" not in str(calls)
    assert calls[-1] == "closed"


@pytest.mark.parametrize('phase', ['tcp_connect', 'tls_handshake', 'request_send', 'response_headers', 'response_body'])
def test_public_timeout_reports_phase_and_partial_bytes_without_exception_text(monkeypatch, phase):
    from hypoloom.document_fallback import error_event
    closed = []
    class Sock:
        def __init__(self, *args): pass
        def settimeout(self, _seconds): pass
        def connect(self, _address):
            if phase == 'tcp_connect': raise TimeoutError('private network text')
        def getpeername(self): return ('8.8.8.8', 443)
        def close(self): pass
    class Response:
        status = 200
        calls = 0
        def getheader(self, name):
            return {'Content-Type': 'application/pdf', 'Content-Length': '8'}.get(name)
        def read1(self, _size):
            self.calls += 1
            if self.calls == 1: return b'%PDF'
            raise TimeoutError('private network text')
    class Connection:
        def __init__(self, *args, **kwargs): self.sock = None
        def connect(self):
            self.sock = self._create_connection(('ignored', 443), 15)
            if phase == 'tls_handshake': raise TimeoutError('private network text')
        def request(self, *args, **kwargs):
            if phase == 'request_send': raise TimeoutError('private network text')
        def getresponse(self):
            if phase == 'response_headers': raise TimeoutError('private network text')
            return Response()
        def close(self): closed.append(True)
    monkeypatch.setattr(web_module.socket, 'socket', Sock)
    monkeypatch.setattr(web_module.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(GroundingWebError) as error:
        PublicHttpsTransport().fetch(DOC, approved_addresses=('8.8.8.8',), max_bytes=10)
    event = error_event('download', error.value, DOC)
    assert event['reason'] == 'timed_out' and event['transport_phase'] == phase
    assert event['received_bytes'] == (4 if phase == 'response_body' else 0)
    assert 'private' not in str(error.value) + str(event) and closed == [True]


def test_network_timeout_and_expired_work_window_are_visible():
    transport = Transport({DOC: _response(DOC)})
    def timeout(_host):
        raise TimeoutError("credential must not appear in diagnostics")
    transport.resolve = timeout
    with pytest.raises(GroundingWebError, match="timed_out") as error:
        GroundingWeb(transport).web_fetch(DOC)
    assert "credential" not in str(error.value)
    assert error.value.transport_phase == 'dns'
    transport = Transport({DOC: _response(DOC)})
    ticks = iter((0.0, 31.0))
    with pytest.raises(GroundingWebError, match="work window"):
        GroundingWeb(transport, clock=lambda: next(ticks)).web_fetch(DOC)
    assert not any(call[0] == "fetch" for call in transport.calls)


@pytest.mark.skipif(not Path("/usr/bin/pdfinfo").is_file() or not Path("/usr/bin/pdftotext").is_file(),
                    reason="real Poppler tools are not installed")
def test_web_pdf_uses_real_poppler_processes_over_exact_downloaded_bytes():
    stream = (b"BT /F1 12 Tf 14 TL 72 720 Td (Abstract) Tj T* "
              b"(Observed CPU integration abstract.) Tj T* (Introduction) Tj T* "
              b"(Other details.) Tj T* (Conclusion) Tj T* "
              b"(Observed CPU integration conclusion.) Tj T* (References) Tj ET")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    pdf, offsets = b"%PDF-1.4\n", [0]
    for number, item in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n".encode() + item + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    pdf += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    pdf += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    result = GroundingWeb(Transport({DOC: _response(DOC, pdf, media_type="application/pdf")})).web_fetch(DOC)
    assert "Observed CPU integration abstract" in result["text"]
    assert "Observed CPU integration conclusion" in result["text"]
    assert result["text_scope"] == "pdf_physical_pages"
