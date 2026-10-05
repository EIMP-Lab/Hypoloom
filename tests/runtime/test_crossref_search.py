"""Offline contracts for the independent, anonymous Crossref search fallback."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from hypoloom.literature import (
    LITERATURE_STRATA, LiteratureFetchError, LiteratureFilters, LiteratureQuery,
    LiteratureQueryError, LiteratureFailureCode, OpenAlexResponse, PreferredVenue, ScholarlySourceClass,
)


def query(**changes):
    value = LiteratureQuery(schema_version=2, query='causal AND learning',
        filters=LiteratureFilters(year_from=2020, year_to=2025, languages=('en',)),
        max_items=3, allowed_source_classes=(ScholarlySourceClass.JOURNAL,),
        strata=LITERATURE_STRATA, latest_as_of='2025-10-05', immutable_context_refs=())
    return replace(value, **changes)


def work(year=2020, **changes):
    value = {'DOI': f'10.1234/example.{year}', 'type': 'journal-article',
             'title': ['Causal learning'], 'author': [{'given': 'Ada', 'family': 'Lovelace'}],
             'published': {'date-parts': [[year, 1, 1]]}, 'language': 'en'}
    value.update(changes)
    return value


def response(items=(), **changes):
    body = json.dumps({'status': 'ok', 'message-type': 'work-list',
                       'message': {'items': list(items)}}).encode()
    value = dict(status_code=200, media_type='application/json', content_length=len(body), body=body)
    value.update(changes)
    return OpenAlexResponse(**value)


class Clock:
    def __init__(self):
        self.now = 10.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


class Transport:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies

    def get(self, **kwargs):
        self.calls.append(kwargs)
        if self.replies is not None:
            reply = self.replies[len(self.calls)-1]
            if isinstance(reply, Exception):
                raise reply
            return reply
        filters = parse_qs(urlsplit(kwargs['target']).query)['filter'][0]
        year = int(filters.split('from-pub-date:')[1][:4])
        return response([work(year)])


@pytest.fixture
def adapter(monkeypatch):
    from hypoloom.crossref_search import CrossrefSearch
    clock = Clock()
    monkeypatch.setattr('hypoloom.crossref_search.time.sleep', clock.sleep)
    transport = Transport()
    return CrossrefSearch(transport=transport, clock=clock), transport, clock


def test_three_strata_are_anonymous_bounded_and_independently_validated(adapter):
    search, transport, clock = adapter
    papers = search.search(query(), deadline=30)
    assert [p.year for p in papers] == [2020, 2022, 2024]
    assert tuple(p.stratum for p in papers) == LITERATURE_STRATA
    assert all(p.venue is None and p.pdf_uri is None for p in papers)
    assert papers[0].authors == ('Ada Lovelace',)
    assert papers[0].source_uri == 'https://api.crossref.org/works/10.1234%2Fexample.2020'
    assert papers[0].landing_uri == 'https://doi.org/10.1234/example.2020'
    assert clock.waits == [1.0, 1.0]
    for index, call in enumerate(transport.calls):
        assert call['host'] == 'api.crossref.org'
        assert call['max_bytes'] == 2 * 1024 * 1024
        assert call['timeout_seconds'] == 10
        assert set(call['headers']) == {'Accept', 'User-Agent'}
        assert call['headers']['Accept'] == 'application/json'
        params = parse_qs(urlsplit(call['target']).query)
        assert params['query.bibliographic'] == ['causal learning']
        assert params['rows'] == ['20']
        assert 'type:journal-article' in params['filter'][0]
        if index == 2:
            assert 'until-pub-date:2025-10-05' in params['filter'][0]


@pytest.mark.parametrize(('expression', 'title', 'abstract', 'accepted'), [
    ('causal AND learning', 'Causal inference', '', False),
    ('causal learning', 'Causal inference', 'Learning models', True),
    ('causal OR learning AND NOT noisy', 'Learning noisy models', '', False),
    ('causal OR learning AND NOT noisy', 'Causal noisy models', '', True),
    ('(causal OR learning) AND NOT noisy', 'Causal noisy models', '', False),
    ('causal NOT noisy', 'Causal model', '', True),
    ('causal NOT noisy', 'Causal noisy model', '', False),
    ('"causal learning"', 'Causal machine learning', '', False),
    ('"causal learning"', 'CAUSAL learning models', '', True),
    ('net', 'Internet models', '', False),
    ('causal AND learning', 'Causal inference', '<jats:p>Learning &amp; testing</jats:p>', True),
])
def test_remote_keyword_near_matches_cannot_bypass_boolean_expression(adapter, expression, title, abstract, accepted):
    search, transport, _clock = adapter
    transport.replies = [response([work(title=[title], abstract=abstract)]), response(), response()]
    papers = search.search(query(query=expression), deadline=30)
    assert bool(papers) is accepted


@pytest.mark.parametrize('expression', ['causal and learning', 'caus*', 'causal OR',
                                        '(causal', 'causal "unclosed'])
def test_unknown_or_unsupported_query_semantics_rejected_before_network(adapter, expression):
    search, transport, _clock = adapter
    with pytest.raises(LiteratureQueryError):
        search.search(query(query=expression), deadline=30)
    assert not transport.calls


@pytest.mark.parametrize('changes', [
    {'DOI': ''}, {'DOI': 'not-a-doi'}, {'title': []}, {'title': [None]},
    {'author': []}, {'author': [{}]}, {'author': [{'given': 'Ada'}]},
    {'type': 'book-chapter'}, {'type': 'posted-content', 'subtype': 'other'},
    {'language': None}, {'language': 'fr'}, {'language': 'English'},
    {'published': {'date-parts': [[2020, 13, 1]]}},
    {'published': {'date-parts': [[True]]}}, {'published': None},
    {'published': {'date-parts': [[2025, 11, 1]]}},
    {'published': {'date-parts': [[2022, 1, 1]]}},
])
def test_incomplete_wrong_or_out_of_stratum_metadata_is_not_evidence(adapter, changes):
    search, transport, _clock = adapter
    transport.replies = [response([work(**changes)]), response(), response()]
    assert search.search(query(), deadline=30) == ()
    assert len(transport.calls) == 3


def test_missing_language_is_und_only_when_language_is_unrestricted(adapter):
    search, transport, _clock = adapter
    candidate = work()
    del candidate['language']
    transport.replies = [response([candidate]), response(), response()]
    papers = search.search(query(filters=LiteratureFilters(year_from=2020, year_to=2025, languages=())), deadline=30)
    assert [p.language for p in papers] == ['und']


@pytest.mark.parametrize(('kind', 'subtype', 'source'), [
    ('journal-article', None, ScholarlySourceClass.JOURNAL),
    ('proceedings-article', None, ScholarlySourceClass.CONFERENCE),
    ('posted-content', 'preprint', ScholarlySourceClass.PREPRINT),
    ('dataset', None, ScholarlySourceClass.SCHOLARLY_DATASET),
    ('report', None, ScholarlySourceClass.TECHNICAL_REPORT),
])
def test_explicit_crossref_types_map_to_requested_source_classes(adapter, kind, subtype, source):
    search, transport, _clock = adapter
    transport.replies = [response([work(type=kind, subtype=subtype)]), response(), response()]
    papers = search.search(query(allowed_source_classes=(source,)), deadline=30)
    assert [p.source_class for p in papers] == [source]
    assert f'type:{kind}' in parse_qs(urlsplit(transport.calls[0]['target']).query)['filter'][0]


def test_types_use_one_or_filter_request_per_stratum_and_no_extra_pages(adapter):
    search, transport, _clock = adapter
    search.search(query(allowed_source_classes=tuple(ScholarlySourceClass)), deadline=30)
    assert len(transport.calls) == 3
    value = parse_qs(urlsplit(transport.calls[0]['target']).query)
    assert value['filter'][0].count('type:') == 5
    assert 'cursor' not in value and 'offset' not in value


def test_pdf_links_are_hints_only_and_require_explicit_pdf_mime_and_safe_https(adapter):
    search, transport, _clock = adapter
    links = [{'URL': 'http://publisher.example/p.pdf', 'content-type': 'application/pdf'},
             {'URL': 'https://127.0.0.1/p.pdf', 'content-type': 'application/pdf'},
             {'URL': 'https://publisher.example/html', 'content-type': 'text/html'},
             {'URL': 'https://publisher.example/unknown.pdf'},
             {'URL': 'https://publisher.example/a.pdf', 'content-type': 'application/pdf'},
             {'URL': 'https://publisher.example/a.pdf', 'content-type': 'application/pdf'},
             {'URL': 'https://publisher.example/b.pdf', 'content-type': 'application/pdf'}]
    transport.replies = [response([work(link=links)]), response(), response()]
    papers = search.search(query(), deadline=30)
    assert papers[0].pdf_uris == ('https://publisher.example/a.pdf', 'https://publisher.example/b.pdf')


def test_duplicate_doi_preferences_and_quotas_do_not_inflate_candidates(adapter):
    search, transport, _clock = adapter
    original = work()
    preferred = work(DOI='10.1234/preferred', **{'container-title': ['Preferred Journal']})
    transport.replies = [response([original, original, preferred]), response(), response()]
    papers = search.search(query(preferred_venues=(PreferredVenue(name='Preferred Journal',
        source_class=ScholarlySourceClass.JOURNAL),)), deadline=30)
    assert [p.bibliographic_identity for p in papers] == ['doi:10.1234/preferred']
    assert papers[0].venue is None


@pytest.mark.parametrize('status', [301, 400, 401, 403, 429, 500, 503])
def test_http_failure_ends_immediately_without_retry_or_later_strata(adapter, status):
    search, transport, _clock = adapter
    transport.replies = [response(status_code=status, retry_after='120')]
    with pytest.raises(LiteratureFetchError) as caught:
        search.search(query(), deadline=30)
    assert caught.value.http_status == status
    assert caught.value.retry_after_seconds == 120
    assert len(transport.calls) == 1


@pytest.mark.parametrize('reply', [
    response(body=b'not json', content_length=8),
    response(media_type='text/html'), response(content_length=3),
    response(body=b'x' * (2 * 1024 * 1024 + 1), content_length=None),
    response(body=b'{"message":{"items":[]}}', content_length=None),
    response([work()] * 21), response([None]), TimeoutError(),
])
def test_shape_size_transport_failure_is_bounded_and_not_retried(adapter, reply):
    search, transport, _clock = adapter
    transport.replies = [reply]
    with pytest.raises(LiteratureFetchError):
        search.search(query(), deadline=30)
    assert len(transport.calls) == 1


def test_deadline_caps_transport_and_pacing_cannot_start_late_request(adapter):
    search, transport, clock = adapter
    with pytest.raises(LiteratureFetchError, match='deadline'):
        search.search(query(), deadline=clock() + 0.5)
    assert len(transport.calls) == 1
    assert transport.calls[0]['timeout_seconds'] == .5
    assert clock.waits == []


def test_expired_or_precancelled_query_sends_nothing(adapter):
    search, transport, clock = adapter
    with pytest.raises(LiteratureFetchError):
        search.search(query(), deadline=clock())
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(LiteratureFetchError, match='cancel'):
        search.search(query(), deadline=30, cancel_event=cancelled)
    assert transport.calls == []


def test_cancellation_interrupts_real_pacing_wait_without_second_request():
    from hypoloom.crossref_search import CrossrefSearch
    transport = Transport()
    cancelled = threading.Event()
    timer = threading.Timer(.05, cancelled.set)
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(LiteratureFetchError, match='cancel'):
            CrossrefSearch(transport=transport).search(query(), deadline=started+10, cancel_event=cancelled)
    finally:
        timer.cancel()
        timer.join()
    assert len(transport.calls) == 1
    assert time.monotonic() - started < .8


def test_default_transport_shares_deadline_cancellation_and_explicit_proxy(monkeypatch):
    from hypoloom.crossref_search import CrossrefSearch
    from hypoloom import literature
    clock = Clock()
    monkeypatch.setattr('hypoloom.crossref_search.time.sleep', clock.sleep)
    calls = []
    router = object()
    cancel = threading.Event()
    # Advance the test clock rather than sleep on the real Event.
    monkeypatch.setattr(cancel, 'wait', lambda delay: clock.sleep(delay))
    def get(provider, host, target, headers, timeout_seconds, max_bytes, **kwargs):
        calls.append((provider, host, headers, timeout_seconds, max_bytes, kwargs))
        return response()
    monkeypatch.setattr(literature, '_metadata_https_get', get)
    assert CrossrefSearch(proxy_router=router, clock=clock).search(query(), deadline=30, cancel_event=cancel) == ()
    assert len(calls) == 3
    assert all(call[0:2] == ('crossref', 'api.crossref.org') for call in calls)
    assert all(call[-1] == {'proxy_router': router, 'cancel_event': cancel} for call in calls)


def test_rate_limit_retry_after_cools_subsequent_calls_without_waiting(adapter):
    search, transport, clock = adapter
    transport.replies = [response(status_code=429, retry_after='900'), response(), response(), response()]
    with pytest.raises(LiteratureFetchError) as first:
        search.search(query(), deadline=30)
    assert first.value.http_status == 429
    with pytest.raises(LiteratureFetchError) as cooled:
        search.search(query(), deadline=clock() + 1200)
    assert cooled.value.http_status == 429 and cooled.value.retry_after_seconds == 900
    assert len(transport.calls) == 1 and clock.waits == []
    clock.now += 900
    assert search.search(query(), deadline=clock() + 30) == ()
    assert len(transport.calls) == 4


def test_cancellation_uses_existing_nonretryable_deadline_signal(adapter):
    search, transport, _clock = adapter
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(LiteratureFetchError) as caught:
        search.search(query(), deadline=30, cancel_event=cancel)
    assert caught.value.code is LiteratureFailureCode.PDF_DEADLINE
    assert not transport.calls


@pytest.mark.parametrize(('parts', 'accepted'), [
    ([2025, 10, 5], True), ([2025, 10, 6], False), ([2025, 11], False),
    ([2025], False), ([2025, 9], True), ([2024], True),
])
def test_exact_and_partial_publication_dates_respect_cutoff(adapter, parts, accepted):
    search, transport, _clock = adapter
    transport.replies = [response(), response(), response([work(2024, published={'date-parts': [parts]})])]
    assert bool(search.search(query(), deadline=30)) is accepted


def test_max_items_bounds_overfetch_without_adding_pages(adapter):
    search, transport, _clock = adapter
    search.search(query(max_items=32), deadline=30)
    assert [parse_qs(urlsplit(call['target']).query)['rows'][0] for call in transport.calls] == ['50', '55', '55']
    assert len(transport.calls) == 3


def test_only_explicit_publication_dates_can_supply_year(adapter):
    search, transport, _clock = adapter
    candidate = work()
    del candidate['published']
    candidate['created'] = {'date-parts': [[2020, 1, 1]]}
    candidate['indexed'] = {'date-parts': [[2020, 1, 1]]}
    transport.replies = [response([candidate]), response(), response()]
    assert search.search(query(), deadline=30) == ()
    candidate['issued'] = {'date-parts': [[2020]]}
    transport.calls = []
    transport.replies = [response([candidate]), response(), response()]
    assert search.search(query(), deadline=30)[0].year == 2020


@pytest.mark.parametrize('word', ['XOR', 'NOR', 'NAND', 'NEAR'])
@pytest.mark.parametrize('present', [True, False])
def test_safe_operator_like_words_remain_literal_query_terms(adapter, word, present):
    search, transport, _clock = adapter
    title = f'{word} causal learning' if present else 'Causal learning'
    transport.replies = [response([work(title=[title])]), response(), response()]
    papers = search.search(query(query=f'{word} AND learning'), deadline=30)
    assert bool(papers) is present
