"""Semantic Scholar credentials, shared admission and bounded fallback recovery."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import traceback
from dataclasses import replace

import pytest

import hypoloom.literature as literature
from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser
from hypoloom.literature import (
    FixedLiteratureIO, LiteratureFetchError, LiteratureFailureCode,
    OpenAlexResponse, SemanticScholarPdfLocator, StdlibSemanticScholarTransport,
    build_openalex_literature_io,
)
from test_literature import FakeFetcher, _papers, _query


DOI = "10.1000/paper"
PDF = "https://papers.example.org/paper.pdf"
SECRET = "fixture-semantic-scholar-private-key"


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def response(status=200, retry_after=None):
    body = json.dumps({"externalIds": {"DOI": DOI}, "openAccessPdf": {"url": PDF}}).encode()
    return OpenAlexResponse(status_code=status, media_type="application/json",
        content_length=len(body), body=body, retry_after=retry_after)


class Transport:
    def __init__(self, clock=None, responses=None):
        self.clock = clock or Clock()
        self.responses = list(responses or [response()])
        self.calls = []

    def get(self, **kwargs):
        self.calls.append({**kwargs, "at": self.clock()})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def locator(transport=None, **kwargs):
    clock = kwargs.pop("clock", Clock())
    transport = transport or Transport(clock)
    return SemanticScholarPdfLocator(transport=transport, clock=clock, sleep=clock.sleep, **kwargs)


def test_key_is_explicit_exact_host_only_and_absent_from_observations():
    transport = Transport()
    client = locator(transport, api_key=SECRET)
    assert client.locate_doi(DOI) == (PDF,)
    call, = transport.calls
    assert call["host"] == "api.semanticscholar.org"
    assert call["target"] == "/graph/v1/paper/DOI%3A10.1000%2Fpaper?fields=externalIds%2CopenAccessPdf"
    assert call["headers"]["x-api-key"] == SECRET
    assert SECRET not in repr(client) + repr(client.configuration) + repr(client.preflight())
    assert client.preflight()["credential_source"] == "explicit"


def test_loader_reads_once_per_admitted_request_and_can_rotate_or_remove_key():
    values = iter([SECRET, "replacement-key", None])
    transport = Transport()
    client = locator(transport, api_key_loader=lambda: next(values))
    for _ in range(3):
        assert client.locate_doi(DOI) == (PDF,)
    assert [call["headers"].get("x-api-key") for call in transport.calls] == [SECRET, "replacement-key", None]
    assert client.preflight()["credential_source"] == "loader"
    assert SECRET not in repr(client.configuration) + repr(client.preflight())


@pytest.mark.parametrize("kind", ["loader", "transport"])
def test_private_errors_do_not_escape_through_exception_chain(kind):
    def failed():
        raise RuntimeError(SECRET)
    transport = Transport()
    if kind == "transport":
        transport.get = lambda **kwargs: failed()
    client = locator(transport, **({"api_key_loader": failed} if kind == "loader" else {"api_key": SECRET}))
    with pytest.raises(LiteratureFetchError) as caught:
        client.locate_doi(DOI)
    assert SECRET not in "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert len(transport.calls) == 0


@pytest.mark.parametrize("key", ["", " secret", "secret\r\nInjected: yes", "secret value", 123])
def test_invalid_key_is_rejected_without_exposing_value(key):
    with pytest.raises((ValueError, LiteratureFetchError)) as caught:
        locator(api_key=key)
    if key:
        assert str(key) not in str(caught.value)


def test_key_and_loader_are_mutually_exclusive():
    with pytest.raises(ValueError):
        locator(api_key=SECRET, api_key_loader=lambda: SECRET)


def test_spacing_uses_remaining_deadline_without_http_retries():
    clock = Clock()
    transport = Transport(clock)
    client = locator(transport, clock=clock)
    assert client.locate_doi(DOI, timeout_seconds=2) == (PDF,)
    assert client.locate_doi(DOI, timeout_seconds=2) == (PDF,)
    assert [call["at"] for call in transport.calls] == [0.0, 1.0]
    assert [call["timeout_seconds"] for call in transport.calls] == [2.0, 1.0]
    with pytest.raises(LiteratureFetchError) as caught:
        client.locate_doi(DOI, timeout_seconds=.5)
    assert caught.value.code is LiteratureFailureCode.PDF_TIMEOUT
    assert len(transport.calls) == 2 and clock() == 1.0


def test_queued_concurrent_request_expires_without_a_second_http_call():
    entered, release = Event(), Event()
    calls = []
    class Blocking:
        def get(self, **kwargs):
            calls.append(kwargs)
            entered.set()
            assert release.wait(2)
            return response()
    client = SemanticScholarPdfLocator(transport=Blocking())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.locate_doi, DOI, timeout_seconds=2)
        assert entered.wait(1)
        try:
            second = pool.submit(client.locate_doi, DOI, timeout_seconds=.05)
            with pytest.raises(LiteratureFetchError) as caught:
                second.result(timeout=1)
            assert caught.value.code is LiteratureFailureCode.PDF_TIMEOUT
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=1) == (PDF,)


@pytest.mark.parametrize(("header", "delay"), [(None, 60.0), ("90", 90.0), ("bad", 60.0), ("5", 60.0)])
def test_429_cooldown_skips_other_dois_then_recovers_without_sleeping(header, delay):
    clock = Clock()
    transport = Transport(clock, [response(429, header), response()])
    client = locator(transport, clock=clock)
    with pytest.raises(LiteratureFetchError) as caught:
        client.locate_doi(DOI)
    assert caught.value.http_status == 429 and caught.value.retry_after_seconds == delay
    clock.now = delay - 1
    with pytest.raises(LiteratureFetchError) as caught:
        client.locate_doi("10.1000/another")
    assert caught.value.http_status == 429 and caught.value.retry_after_seconds == 1
    assert len(transport.calls) == 1 and clock.sleeps == []
    clock.now = delay
    assert client.locate_doi(DOI) == (PDF,)
    assert len(transport.calls) == 2


def test_retry_after_http_date_reuses_existing_parser(monkeypatch):
    monkeypatch.setattr(literature.time, "time", lambda: 0)
    client = locator(Transport(responses=[response(429, "Thu, 01 Jan 1970 00:02:00 GMT")]))
    with pytest.raises(LiteratureFetchError) as caught:
        client.locate_doi(DOI)
    assert caught.value.retry_after_seconds == 120


def test_exact_host_transport_preserves_retry_after_without_redirecting(monkeypatch):
    from types import SimpleNamespace
    from hypoloom import grounding_web, network_io
    calls = []
    class Response:
        status = 429
        pending = True
        def getheader(self, name):
            return {"Retry-After": "120", "Content-Type": "application/json", "Content-Length": "2"}.get(name)
        def read1(self, bound):
            result = b"{}" if self.pending else b""
            self.pending = False
            return result
    class Connection:
        def __init__(self, host, port, timeout):
            calls.append((host, port))
            self.sock = SimpleNamespace(getpeername=lambda: ("93.184.216.34", 443), settimeout=lambda value: None)
        def connect(self):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(literature.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(grounding_web.socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 443))])
    monkeypatch.setattr(network_io, "run_network_operation",
                        lambda operation, payload, **kwargs: grounding_web._fetch_in_process(payload))
    transport = StdlibSemanticScholarTransport()
    kwargs = {"target": "/graph/v1/paper/DOI%3A10.1000%2Fpaper?fields=externalIds%2CopenAccessPdf",
              "headers": {"x-api-key": SECRET}, "timeout_seconds": 1, "max_bytes": 1024}
    assert transport.get(host="api.semanticscholar.org", **kwargs).retry_after == "120"
    with pytest.raises(LiteratureFetchError):
        transport.get(host="papers.example.org", **kwargs)
    assert calls == [("api.semanticscholar.org", 443)]


def test_runtime_cooldown_honors_retry_after_from_any_locator():
    clock = Clock()
    class Limited:
        calls = 0
        def locate(self, paper):
            return ()
        def locate_doi(self, doi, **kwargs):
            self.calls += 1
            raise LiteratureFetchError("limited", http_status=429, retry_after_seconds=120)
    limited = Limited()
    adapter = FixedLiteratureIO(FakeFetcher(), fallback_locator=limited, clock=clock)
    assert adapter.locate_doi("doi:" + DOI, [], timeout_seconds=5) == ()
    clock.now = 60
    events = []
    assert adapter.locate_doi("doi:" + DOI, events, timeout_seconds=5) == ()
    assert limited.calls == 1 and events[-1]["outcome"] == "RATE_LIMITED"
    clock.now = 120
    assert adapter.locate_doi("doi:" + DOI, [], timeout_seconds=5) == ()
    assert limited.calls == 2


def test_batch_passes_only_remaining_window_to_doi_locator():
    clock = Clock()
    class Locator:
        calls = []
        def locate(self, paper):
            pytest.fail("bounded DOI interface must receive the remaining work window")
        def locate_doi(self, doi, *, timeout_seconds):
            self.calls.append((doi, timeout_seconds))
            return ()
    limited = Locator()
    papers = [replace(p, pdf_uri=None) for p in _papers()]
    adapter = FixedLiteratureIO(FakeFetcher(papers=papers), fallback_locator=limited,
        clock=clock, pdf_work_window_seconds=1)
    adapter.retrieve(_query())
    assert limited.calls and all(0 < seconds <= 1 for _, seconds in limited.calls)


def test_response_after_deadline_cannot_be_admitted():
    clock = Clock()
    class Slow:
        def get(self, **kwargs):
            clock.now += 1.1
            return response()
    client = locator(Slow(), clock=clock)
    with pytest.raises(LiteratureFetchError) as caught:
        client.locate_doi(DOI, timeout_seconds=1)
    assert caught.value.code is LiteratureFailureCode.PDF_TIMEOUT


def test_builder_forwards_optional_key_without_changing_openalex_credentials():
    transport = Transport()
    adapter = build_openalex_literature_io(api_key="openalex-only", semantic_scholar_api_key=SECRET,
                                         semantic_scholar_transport=transport)
    assert adapter.fallback_locator.locate_doi(DOI) == (PDF,)
    assert transport.calls[0]["headers"]["x-api-key"] == SECRET


@pytest.mark.parametrize("injected", [False, True])
def test_init_sample_reuses_one_locator_and_keeps_cooldown(monkeypatch, injected):
    from hypoloom import document_fallback
    clock = Clock()
    transport = Transport(clock, [response(429), response()])
    client = locator(transport, clock=clock)
    creations = []
    def factory(**kwargs):
        creations.append(kwargs)
        return client
    monkeypatch.setattr(literature, "SemanticScholarPdfLocator", factory)
    def acquire(web, identity, *, locate, **kwargs):
        try:
            locate(5)
        except LiteratureFetchError as exc:
            assert exc.http_status == 429
        return None, ""
    monkeypatch.setattr(document_fallback, "acquire_public_pdf", acquire)
    class Browser(PlaywrightInstitutionalBrowser):
        def fetch(self, url, **kwargs):
            return {"state": "verification_required"}
    browser = Browser("http://127.0.0.1:9222", **({"pdf_locator": client} if injected else {}))
    assert creations == []
    for _ in range(2):
        assert browser.fetch_sample("https://doi.org/" + DOI)["state"] == "verification_required"
    assert len(creations) == (0 if injected else 1)
    assert len(transport.calls) == 1
