from __future__ import annotations

import json
from dataclasses import replace
from threading import Barrier, Lock

import pytest

import hypoloom.literature as literature
from hypoloom.literature import (
    FetchResponse, FixedLiteratureIO, LiteratureFetchError, LiteratureFailureCode,
    LiteratureValidationError, RetrievalDisposition,
    LITERATURE_STRATA, SearchPaper, build_openalex_literature_io,
)
from test_literature import (
    FakeFetcher, PDF_LATEST, PUBLIC_A, _paper, _papers, _query,
)


def _entry(adapter: FixedLiteratureIO, identity: str) -> dict:
    return next(item for item in adapter.download_trace
                if item["bibliographic_identity"] == identity)


def _response(uri: str, *, status: int = 200, body: bytes = PDF_LATEST) -> FetchResponse:
    return FetchResponse(requested_uri=uri, status_code=status, peer_address=PUBLIC_A,
                         media_type="application/pdf", content_length=len(body),
                         location=None, body=body)


@pytest.mark.parametrize(("failure", "expected"), [
    (503, "HTTP_FAILURE"),
    (TimeoutError("secret-token"), "TIMEOUT"),
    (OSError("secret-token"), "TRANSPORT_FAILURE"),
    (b"<html>secret-token</html>", "INVALID_PDF"),
])
def test_download_trace_distinguishes_failures_without_response_or_uri_secrets(
    failure: object, expected: str,
) -> None:
    uri = "https://latest.example.org/secret-path.pdf?token=secret-token"

    class Fetcher(FakeFetcher):
        def fetch(self, source: str, **kwargs: object) -> FetchResponse:
            if source == uri and isinstance(failure, Exception):
                raise failure
            return super().fetch(source, **kwargs)

    papers = [replace(_papers()[0], pdf_uri=uri), *_papers()[1:]]
    response = _response(uri, status=failure if isinstance(failure, int) else 200,
                         body=failure if isinstance(failure, bytes) else PDF_LATEST)
    adapter = FixedLiteratureIO(Fetcher(papers=papers, responses={uri: response}))
    adapter.retrieve(_query())
    entry = _entry(adapter, "openalex:W12345")
    event = next(event for event in entry["events"] if event["outcome"] == expected)
    assert entry["retrieval_disposition"] == "PDF_UNAVAILABLE"
    assert event["http_status"] == (failure if isinstance(failure, int) else None)
    assert event["source_host"] == "latest.example.org"
    assert "secret" not in json.dumps(adapter.download_trace)
    assert _entry(adapter, "arxiv:2101.12345v2")["events"][0]["outcome"] == "NO_OA_URL"


def test_extraction_failure_is_visible_and_never_cached() -> None:
    class Fetcher(FakeFetcher):
        failed = True

        def extract_pdf(self, content: bytes, *, max_pages: int):
            if content == PDF_LATEST and self.failed:
                raise LiteratureFetchError("secret-body", code=LiteratureFailureCode.POPPLER_FAILED)
            return super().extract_pdf(content, max_pages=max_pages)

    fetcher = Fetcher()
    adapter = FixedLiteratureIO(fetcher)
    adapter.retrieve(_query())
    event = _entry(adapter, "openalex:W12345")["events"][0]
    assert event["outcome"] == "EXTRACTION_FAILURE"
    assert event["failure_code"] == "POPPLER_FAILED"
    fetcher.failed = False
    result = adapter.retrieve(_query())
    assert result.packet.papers[-1].retrieval_disposition is RetrievalDisposition.RETRIEVED
    assert sum(uri == "https://latest.example.org/paper.pdf" for uri, *_ in fetcher.fetch_calls) == 2


def test_success_cache_reuses_exact_identity_uri_and_returns_detached_diagnostics() -> None:
    fetcher = FakeFetcher()
    adapter = FixedLiteratureIO(fetcher)
    first = adapter.retrieve(_query())
    calls = (len(fetcher.fetch_calls), len(fetcher.extract_calls))
    second = adapter.retrieve(replace(_query(), query="another overlapping research question"))
    assert (len(fetcher.fetch_calls), len(fetcher.extract_calls)) == calls
    assert second.pdf_artifacts == first.pdf_artifacts
    assert _entry(adapter, "doi:10.1000/foundation")["events"][0]["outcome"] == "CACHE_HIT"
    trace = adapter.download_trace
    trace[0]["events"].clear()
    assert adapter.download_trace[0]["events"]
    fetcher.papers[0] = replace(fetcher.papers[0], bibliographic_identity="openalex:W999")
    adapter.retrieve(_query())
    assert len(fetcher.extract_calls) == calls[1] + 1
    uri = "https://latest.example.org/updated.pdf"
    fetcher.papers[0] = replace(fetcher.papers[0], pdf_uri=uri)
    fetcher.responses[uri] = _response(uri)
    adapter.retrieve(_query())
    assert len(fetcher.extract_calls) == calls[1] + 2


def test_download_trace_resets_when_the_next_search_fails() -> None:
    fetcher = FakeFetcher()
    adapter = FixedLiteratureIO(fetcher)
    adapter.retrieve(_query())
    fetcher.papers = []
    with pytest.raises(LiteratureValidationError):
        adapter.retrieve(_query())
    assert adapter.download_trace == ()


@pytest.mark.parametrize(("entries", "byte_limit", "extra_extractions"), [
    (1, 64 * 1024 * 1024, 2), (32, len(PDF_LATEST), 1),
])
def test_success_cache_is_bounded_by_entries_and_bytes(
    monkeypatch, entries, byte_limit, extra_extractions,
) -> None:
    monkeypatch.setattr(literature, "MAX_PDF_CACHE_ENTRIES", entries)
    monkeypatch.setattr(literature, "MAX_PDF_CACHE_BYTES", byte_limit)
    fetcher = FakeFetcher()
    adapter = FixedLiteratureIO(fetcher)
    adapter.retrieve(_query())
    count = len(fetcher.extract_calls)
    adapter.retrieve(_query())
    assert len(fetcher.extract_calls) == count + extra_extractions
    if extra_extractions == 1:
        assert _entry(adapter, "openalex:W12345")["events"][0]["outcome"] == "CACHE_HIT"


@pytest.mark.parametrize("located", [(), ("https://latest.example.org/fallback.pdf",), 429])
def test_doi_fallback_outcomes_and_http_status_are_visible(located) -> None:
    class Locator:
        def locate(self, paper):
            if isinstance(located, int):
                raise LiteratureFetchError("secret", http_status=located)
            return located

    papers = [*_papers()[:2], replace(_papers()[-1], pdf_uri=None)]
    uri = "https://latest.example.org/fallback.pdf"
    adapter = FixedLiteratureIO(FakeFetcher(papers=papers, responses={uri: _response(uri)}),
                                fallback_locator=Locator())
    adapter.retrieve(_query())
    events = _entry(adapter, "doi:10.1000/foundation")["events"]
    if located == 429:
        assert any(e["stage"] == "fallback_lookup" and e["http_status"] == 429
                   and e["outcome"] == "HTTP_FAILURE" for e in events)
    elif located:
        assert any(e["outcome"] == "FALLBACK_FOUND" for e in events)
        assert events[-1]["stage"] == "fallback"
        assert events[-1]["outcome"] == "RETRIEVED"
    else:
        assert events[-1]["outcome"] == "FALLBACK_EMPTY"


def test_deadline_and_request_budget_have_separate_outcomes() -> None:
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()

    class Fetcher(FakeFetcher):
        def fetch(self, uri, **kwargs):
            response = super().fetch(uri, **kwargs)
            clock.value += 2.0
            return response

    adapter = FixedLiteratureIO(Fetcher(), clock=clock, pdf_work_window_seconds=1)
    adapter.retrieve(_query())
    assert any(event["outcome"] == "DEADLINE" for item in adapter.download_trace
               for event in item["events"])
    uri = "https://latest.example.org/other.pdf"
    papers = [replace(_papers()[0], alternate_pdf_uris=(uri,)), *_papers()[1:]]
    fetcher = FakeFetcher(papers=papers, responses={
        "https://latest.example.org/paper.pdf": _response("https://latest.example.org/paper.pdf", status=403),
        uri: _response(uri, status=403),
    })
    adapter = FixedLiteratureIO(fetcher, max_redirects=0)
    adapter.retrieve(_query())
    # The second direct request consumes the last per-paper slot; remaining work is explicit.
    assert any(event["outcome"] == "REQUEST_BUDGET" for item in adapter.download_trace
               for event in item["events"])


def test_two_pdf_workers_overlap_but_keep_batch_limit_all_papers_and_order() -> None:
    papers: list[SearchPaper] = []
    responses = {}
    primary_uris = set()
    for index in range(1, 21):
        uris = tuple(f"https://papers.example.org/{index}-{suffix}.pdf" for suffix in range(10))
        primary_uris.add(uris[0])
        papers.append(replace(
            _paper(LITERATURE_STRATA[(index - 1) % 3], f"openalex:W{index}", year=2000 + index),
            pdf_uri=uris[0], alternate_pdf_uris=uris[1:],
        ))
        responses.update({uri: _response(uri, status=403) for uri in uris})
    pair = Barrier(2, timeout=5)
    lock = Lock()

    class Fetcher(FakeFetcher):
        active = 0
        peak = 0
        started = 0

        def fetch(self, uri, **kwargs):
            with lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.started += 1
                initial_pair = self.started <= 2
            try:
                if initial_pair:
                    pair.wait()
                return super().fetch(uri, **kwargs)
            finally:
                with lock:
                    self.active -= 1

    fetcher = Fetcher(papers=papers, responses=responses)
    adapter = FixedLiteratureIO(fetcher, pdf_workers=2)
    result = adapter.retrieve(_query(max_items=20))
    assert fetcher.peak == 2
    assert fetcher.active == 0
    assert len(fetcher.fetch_calls) == literature.MAX_PDF_HTTP_CALLS_PER_BATCH
    assert primary_uris <= {uri for uri, *_ in fetcher.fetch_calls}
    assert {item.bibliographic_identity for item in result.packet.papers} == {
        item.bibliographic_identity for item in papers
    }
    assert list(result.packet.papers) == sorted(result.packet.papers, key=lambda item: item.order_key)
    assert len(adapter.download_trace) == 20
    assert all(len(item["events"]) <= literature.MAX_PDF_DOWNLOAD_EVENTS_PER_PAPER
               for item in adapter.download_trace)


def test_concurrent_pdf_results_preserve_artifact_refs_and_cache_hits() -> None:
    fetcher = FakeFetcher()
    adapter = FixedLiteratureIO(fetcher, pdf_workers=2)
    first = adapter.retrieve(_query())
    assert [item.artifact_ref for item in first.pdf_artifacts] == [
        "literature-pdf-001", "literature-pdf-003",
    ]
    count = len(fetcher.extract_calls)
    second = adapter.retrieve(_query())
    assert second == first
    assert len(fetcher.extract_calls) == count
    assert sum(event["outcome"] == "CACHE_HIT" for item in adapter.download_trace
               for event in item["events"]) == 2


def test_parallel_fallback_lookups_keep_the_existing_batch_bound() -> None:
    class Locator:
        calls = 0
        lock = Lock()

        def locate(self, paper):
            with self.lock:
                self.calls += 1
            return ()

    papers = [_paper(LITERATURE_STRATA[index % 3], f"doi:10.1000/fallback-{index}", year=2000 + index)
              for index in range(12)]
    locator = Locator()
    adapter = FixedLiteratureIO(FakeFetcher(papers=papers), fallback_locator=locator, pdf_workers=2)
    adapter.retrieve(_query(max_items=12))
    assert locator.calls == literature.MAX_PDF_FALLBACK_LOOKUPS
    assert sum(event["outcome"] == "FALLBACK_LIMIT" for item in adapter.download_trace
               for event in item["events"]) == 4


def test_factory_enables_two_pdf_workers_with_explicit_configuration() -> None:
    adapter = build_openalex_literature_io(api_key="never-disclosed")
    assert adapter.pdf_workers == adapter.configuration["pdf_workers"] == 2
    assert FixedLiteratureIO(FakeFetcher()).pdf_workers == 1
    with pytest.raises(ValueError, match="pdf_workers"):
        FixedLiteratureIO(FakeFetcher(), pdf_workers=3)


def test_parallel_workers_share_one_deadline_and_never_cache_late_results() -> None:
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()
    pair = Barrier(2, timeout=5)

    class Fetcher(FakeFetcher):
        slow = True

        def fetch(self, uri, **kwargs):
            response = super().fetch(uri, **kwargs)
            if self.slow:
                pair.wait()
                clock.value = 2.0
            return response

    papers = list(_papers())
    uri = "https://latest.example.org/intermediate.pdf"
    papers[1] = replace(papers[1], pdf_uri=uri)
    fetcher = Fetcher(papers=papers, responses={uri: _response(uri)})
    adapter = FixedLiteratureIO(fetcher, clock=clock, pdf_work_window_seconds=1, pdf_workers=2)
    first = adapter.retrieve(_query())
    assert len(fetcher.fetch_calls) == 2
    assert first.pdf_artifacts == ()
    assert all(any(event["outcome"] == "DEADLINE" for event in item["events"])
               for item in adapter.download_trace)
    fetcher.slow = False
    clock.value = 0.0
    second = adapter.retrieve(_query())
    assert len(second.pdf_artifacts) == 3
    assert not any(event["outcome"] == "CACHE_HIT" for item in adapter.download_trace
                   for event in item["events"])


def test_invalid_fallback_uri_diagnostic_cannot_break_failure_isolation() -> None:
    class Locator:
        def locate(self, paper):
            return ("https://[invalid/secret",)

    papers = [*_papers()[:2], replace(_papers()[-1], pdf_uri=None)]
    adapter = FixedLiteratureIO(FakeFetcher(papers=papers), fallback_locator=Locator())
    result = adapter.retrieve(_query())
    assert len(result.packet.papers) == 3
    assert result.packet.papers[-1].retrieval_disposition is RetrievalDisposition.RETRIEVED
    assert "secret" not in json.dumps(adapter.download_trace)


def test_expired_pdf_window_stops_later_metadata_dns_work() -> None:
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()

    class Fetcher(FakeFetcher):
        def resolve(self, hostname):
            assert clock.value == 0, "expired PDF work must not begin another DNS lookup"
            return super().resolve(hostname)

        def fetch(self, uri, **kwargs):
            result = super().fetch(uri, **kwargs)
            clock.value = 2
            return result

    adapter = FixedLiteratureIO(Fetcher(), clock=clock, pdf_work_window_seconds=1)
    assert adapter.retrieve(_query()).pdf_artifacts == ()
    assert all(any(event["outcome"] == "DEADLINE" for event in item["events"])
               for item in adapter.download_trace)


def test_metadata_dns_failure_is_observed_without_rejecting_available_pdf() -> None:
    class Fetcher(FakeFetcher):
        def resolve(self, hostname):
            if hostname == "catalog.example.org":
                raise OSError("secret-detail")
            return super().resolve(hostname)

    adapter = FixedLiteratureIO(Fetcher())
    result = adapter.retrieve(_query())
    assert len(result.pdf_artifacts) == 2
    events = _entry(adapter, "openalex:W12345")["events"]
    assert any(event["stage"] == "landing_uri" and event["outcome"] == "TRANSPORT_FAILURE"
               for event in events)
    assert "secret" not in json.dumps(adapter.download_trace)


@pytest.mark.parametrize("workers", [1, 2])
def test_slow_fallback_cannot_starve_later_direct_pdfs(workers) -> None:
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()

    class Locator:
        def locate(self, paper):
            clock.value = 2.0
            raise LiteratureFetchError("lookup timeout", code=LiteratureFailureCode.PDF_TIMEOUT)

    papers = [
        replace(_papers()[2], pdf_uri=None),
        replace(_papers()[1], bibliographic_identity="doi:10.1000/intermediate"),
        _papers()[0],
    ]
    fetcher = FakeFetcher(papers=papers)
    adapter = FixedLiteratureIO(fetcher, fallback_locator=Locator(), pdf_workers=workers,
                                clock=clock, pdf_work_window_seconds=1)
    result = adapter.retrieve(_query())
    assert len(result.pdf_artifacts) == 1
    assert result.packet.papers[-1].retrieval_disposition is RetrievalDisposition.RETRIEVED
    assert fetcher.fetch_calls[0][0] == papers[-1].pdf_uri


@pytest.mark.parametrize("workers", [1, 2])
def test_fallback_429_stops_batches_until_cooldown_expires(workers) -> None:
    class Locator:
        calls = 0

        def locate(self, paper):
            self.calls += 1
            raise LiteratureFetchError("limited", http_status=429)

    papers = [_paper(LITERATURE_STRATA[index % 3], f"doi:10.1000/limited-{index}",
                     year=2000 + index) for index in range(12)]
    locator = Locator()
    now = [0.0]
    adapter = FixedLiteratureIO(FakeFetcher(papers=papers), fallback_locator=locator,
                                pdf_workers=workers, clock=lambda: now[0])
    adapter.retrieve(_query(max_items=12))
    assert 1 <= locator.calls <= workers
    assert any(event["outcome"] == "RATE_LIMITED" for item in adapter.download_trace
               for event in item["events"])
    previous = locator.calls
    adapter.retrieve(_query(max_items=12))
    assert locator.calls == previous
    now[0] = 59.0
    adapter.retrieve(_query(max_items=12))
    assert locator.calls == previous
    now[0] = 60.0
    adapter.retrieve(_query(max_items=12))
    assert 1 <= locator.calls - previous <= workers
