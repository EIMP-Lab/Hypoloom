"""Batch budgets cover native DNS, redirect fetches and PDF extraction."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from dataclasses import asdict
import base64
import os
import time

import pytest

from hypoloom.literature import (
    FixedLiteratureIO, LiteratureFailureCode, LiteratureFetchError,
    OpenAlexLiteratureFetcher, SemanticScholarPdfLocator,
    StdlibOpenAlexTransport, StdlibSemanticScholarTransport, RetrievalDisposition,
)
from test_literature import FakeFetcher, _query


def test_openalex_response_limit_keeps_existing_failure_classification(monkeypatch):
    from hypoloom import network_io

    def exceeded(*args, **kwargs):
        raise network_io.NetworkOperationError("response_limit", transport_phase="response_body")

    monkeypatch.setattr(network_io, "run_network_operation", exceeded)
    with pytest.raises(LiteratureFetchError) as caught:
        StdlibOpenAlexTransport().get(host="api.openalex.org", target="/works?search=fixture",
            headers={}, timeout_seconds=1, max_bytes=1024)
    assert caught.value.code is LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT
    assert caught.value.transport_phase == "response_body"


def test_batch_passes_one_remaining_window_through_dns_redirects_and_extraction():
    now = [0.0]
    calls = []
    fixture = FakeFetcher()

    class Fetcher(OpenAlexLiteratureFetcher):
        def search(self, query):
            return fixture.search(query)

        def record(self, phase, timeout):
            calls.append((phase, now[0], timeout))
            now[0] += 1

        def resolve(self, host, *, timeout_seconds=None):
            self.record("dns", timeout_seconds)
            return fixture.resolve(host)

        def fetch(self, uri, *, timeout_seconds=None, **kwargs):
            self.record("fetch", timeout_seconds)
            return fixture.fetch(uri, **kwargs)

        def extract_pdf(self, body, *, timeout_seconds=None, **kwargs):
            self.record("extract", timeout_seconds)
            return fixture.extract_pdf(body, **kwargs)

    adapter = FixedLiteratureIO(Fetcher(api_key="fixture"), pdf_workers=1,
                               pdf_work_window_seconds=30, clock=lambda: now[0])
    result = adapter.retrieve(_query())
    assert len(result.pdf_artifacts) == 2
    assert {phase for phase, _, _ in calls} == {"dns", "fetch", "extract"}
    # All phases and the second redirect spend the original batch budget.
    assert all(timeout == 30 - start for _, start, timeout in calls), calls
    assert len([phase for phase, _, _ in calls if phase == "fetch"]) == 3


def test_cancelling_semantic_scholar_queue_never_sends_a_second_request():
    from test_semantic_scholar_recovery import DOI, response
    entered, release, cancelled = Event(), Event(), Event()
    calls = []

    class Transport:
        def get(self, **kwargs):
            calls.append(kwargs)
            entered.set()
            assert release.wait(2)
            return response()

    client = SemanticScholarPdfLocator(transport=Transport())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.locate_doi, DOI, timeout_seconds=2)
        assert entered.wait(1)
        try:
            second = pool.submit(client.locate_doi, DOI, timeout_seconds=2,
                                 cancel_event=cancelled)
            cancelled.set()
            with pytest.raises(LiteratureFetchError) as caught:
                second.result(timeout=.5)
            assert caught.value.code is LiteratureFailureCode.PDF_DEADLINE
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=1)


@pytest.mark.parametrize("cancel", [False, True])
def test_real_worker_timeout_or_cancel_preserves_other_paper_and_next_batch(tmp_path, monkeypatch, cancel):
    from hypoloom import network_io
    fixture = FakeFetcher()
    pid_file = tmp_path / "network-pid"
    blocked = [True]
    real_operation = network_io.run_network_operation

    def operation(name, payload, **limits):
        assert name == "hypoloom.grounding_web:_fetch_in_process"
        if payload["uri"].startswith("https://latest.") and blocked[0]:
            return real_operation("tests.runtime.network_fault_worker:request", {
                "mode": "response_headers", "pid_file": str(pid_file),
                "deadline": payload["deadline"],
            }, **limits)
        response = fixture.fetch(payload["uri"], approved_addresses=payload["approved_addresses"],
                                 max_bytes=payload["max_bytes"])
        return {**asdict(response), "body_b64": base64.b64encode(response.body).decode(), "retry_after": None}

    monkeypatch.setattr(network_io, "run_network_operation", operation)
    fetcher = OpenAlexLiteratureFetcher(api_key="fixture", resolver=fixture.resolve,
                                        pdf_extractor=fixture, timeout_seconds=1)
    fetcher.search = fixture.search
    adapter = FixedLiteratureIO(fetcher, pdf_workers=2)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = adapter.submit_read(pool, "fixture-run", "retrieve", _query())
        if cancel:
            wait_until = time.monotonic() + 3
            while not pid_file.exists() and time.monotonic() < wait_until:
                time.sleep(.01)
            assert pid_file.exists()
            job.cancelled.set()
        result = job.future.result(timeout=3)
    assert time.monotonic() - started < 3
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)

    assert result["error"] is None
    assert len(result["value"].pdf_artifacts) == 1
    assert all("latest.example.org" not in key[1] for key in adapter._pdf_cache)
    entry = next(item for item in adapter.download_trace if item["bibliographic_identity"].startswith("openalex:"))
    event = next(event for event in entry["events"] if event["stage"] == "direct")
    assert event["failure_code"] == ("PDF_DEADLINE" if cancel else "PDF_TIMEOUT")
    if not cancel:
        assert event["transport_phase"] == "response_headers"
    blocked[0] = False
    recovered = adapter.retrieve(_query())
    assert len(recovered.pdf_artifacts) == 2
    assert recovered.packet.papers[-1].retrieval_disposition is RetrievalDisposition.RETRIEVED


@pytest.mark.parametrize("provider", ["openalex", "semantic-scholar"])
def test_metadata_timeout_kills_real_worker_and_keeps_safe_reason(tmp_path, monkeypatch, provider):
    from hypoloom import network_io
    pid_file = tmp_path / "metadata-pid"
    real_operation = network_io.run_network_operation

    def operation(name, payload, **limits):
        assert name == "hypoloom.grounding_web:_fetch_in_process"
        return real_operation("tests.runtime.network_fault_worker:request",
            {"mode": "dns", "pid_file": str(pid_file), "deadline": payload["deadline"]}, **limits)

    monkeypatch.setattr(network_io, "run_network_operation", operation)
    transport = StdlibOpenAlexTransport() if provider == "openalex" else StdlibSemanticScholarTransport()
    host = "api.openalex.org" if provider == "openalex" else "api.semanticscholar.org"
    target = "/works?search=test" if provider == "openalex" else "/graph/v1/paper/DOI%3A10.1000%2Fpaper?fields=externalIds%2CopenAccessPdf"
    with pytest.raises(LiteratureFetchError) as error:
        transport.get(host=host, target=target, headers={"x-api-key": "private-fixture"},
                      timeout_seconds=1, max_bytes=1024)
    assert error.value.code is (LiteratureFailureCode.OPENALEX_TIMEOUT if provider == "openalex" else LiteratureFailureCode.PDF_TIMEOUT)
    assert error.value.transport_phase == "dns"
    assert "private-fixture" not in str(error.value)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)

def test_unreaped_worker_is_not_reported_as_an_ordinary_pdf_gap(monkeypatch):
    from hypoloom import network_io
    fixture = FakeFetcher()
    calls = []

    def failed(*args, **kwargs):
        calls.append(args)
        raise network_io.NetworkOperationError("cleanup_failed")

    monkeypatch.setattr(network_io, "run_network_operation", failed)
    fetcher = OpenAlexLiteratureFetcher(api_key="fixture", resolver=fixture.resolve,
                                        pdf_extractor=fixture)
    fetcher.search = fixture.search
    adapter = FixedLiteratureIO(fetcher, pdf_workers=1)
    with pytest.raises(LiteratureFetchError) as error:
        adapter.retrieve(_query())
    assert error.value.code is LiteratureFailureCode.NETWORK_CLEANUP_FAILED
    assert len(calls) == 1 and not adapter._pdf_cache
