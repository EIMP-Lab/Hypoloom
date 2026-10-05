"""Primary outage may use independent metadata without relaxing packet admission."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from hypoloom.literature import (FixedLiteratureIO, LiteratureCoverageError,
    LiteratureFailureCode as Code, LiteratureFetchError, OpenAlexLiteratureFetcher)
from hypoloom.literature_recovery import points, reduce_availability
from test_literature import _query, RecordingOpenAlexTransport


class Clock:
    now = 100.
    def __call__(self): return self.now


class Backup:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error
        self.papers = tuple(OpenAlexLiteratureFetcher(api_key="fixture",
            transport=RecordingOpenAlexTransport()).search(_query()))

    def search(self, query, *, deadline, cancel_event):
        self.calls.append((query, deadline, cancel_event))
        if self.error: raise self.error
        return self.papers


def fetcher(status=503, *, backup=None, clock=None):
    return OpenAlexLiteratureFetcher(api_key="private-primary-key",
        transport=RecordingOpenAlexTransport(status_code=status), max_retries=0,
        search_fallback=backup or Backup(), clock=clock or Clock())


def test_primary_failure_uses_backup_and_does_not_reset_cooldown():
    clock = Clock(); adapter = fetcher(clock=clock)
    first = tuple(adapter.search(_query()))
    assert first and len(adapter._transport.calls) == 1
    assert adapter.last_search_observation['available'] is False
    assert adapter.last_search_observation['fallback_available'] is True
    assert adapter._search_fallback.calls[0][1] == 160
    clock.now += 10
    assert tuple(adapter.search(_query())) == first
    assert len(adapter._transport.calls) == 1
    assert adapter._primary_retry_at == 130
    assert adapter.last_search_observation['retry_after_seconds'] == 20
    clock.now = 131
    adapter._transport.status_code = 200
    assert tuple(adapter.search(_query())) == first
    assert adapter.last_search_observation == {'service': 'openalex', 'available': True}


@pytest.mark.parametrize('status', [400, 401, 403, 404])
def test_permanent_or_auth_primary_failures_do_not_use_backup(status):
    adapter = fetcher(status)
    with pytest.raises(LiteratureFetchError): adapter.search(_query())
    assert adapter._search_fallback.calls == []


def test_credential_loss_is_not_hidden_by_primary_cooldown():
    adapter = fetcher()
    adapter.search(_query())
    adapter.api_key_loader = lambda: None
    with pytest.raises(LiteratureFetchError) as error: adapter.search(_query())
    assert error.value.code is Code.OPENALEX_AUTH
    assert len(adapter._search_fallback.calls) == 1


def test_both_sources_failure_preserves_primary_recovery_and_safe_backup_diagnostic():
    adapter = fetcher(backup=Backup(error=LiteratureFetchError('private response omitted',
        code=Code.FETCH_FAILED, http_status=429)))
    with pytest.raises(LiteratureFetchError) as error: adapter.search(_query())
    assert error.value.code is Code.OPENALEX_HTTP and error.value.http_status == 503
    assert adapter.last_search_observation['fallback_http_status'] == 429
    assert 'private' not in str(adapter.last_search_observation)


def test_primary_deadline_reserves_backup_without_giving_it_a_new_window():
    clock = Clock(); backup = Backup(); adapter = fetcher(clock=clock, backup=backup)
    def primary(query, *, deadline):
        assert deadline == 130
        clock.now = deadline
        raise LiteratureFetchError('timed out', code=Code.OPENALEX_TIMEOUT)
    adapter._search_openalex = primary
    adapter.search(_query())
    assert backup.calls[0][1] == 160


def test_fallback_missing_stratum_remains_coverage_failure_and_cancellation_propagates():
    adapter = fetcher(); adapter._search_fallback.papers = adapter._search_fallback.papers[:1]
    io = FixedLiteratureIO(adapter)
    with pytest.raises(LiteratureCoverageError): io._search_candidates(_query())
    event = Event(); adapter._search_fallback.error = LiteratureFetchError('cancelled', code=Code.PDF_DEADLINE)
    with io.cancellation(event), pytest.raises(LiteratureFetchError) as error: io._search_candidates(_query())
    assert error.value.code is Code.PDF_DEADLINE
    assert adapter._search_fallback.calls[-1][2] is event


def test_worker_copies_search_observation_before_next_search():
    adapter = fetcher(); io = FixedLiteratureIO(adapter)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = io.submit_read(pool, 'fixture', 'coverage', _query()).future.result()
    assert result['error'] is None
    observation = result['diagnostics']['literature_search']
    adapter.last_search_observation.clear()
    assert observation['fallback_available'] is True


def test_open_model_attempt_with_backup_success_clears_wait_without_primary_recovery_claim():
    from test_service_notifications import row
    rows = [row('failure', 1, 2)]
    success = row('fallback', 35, 36)
    success['state'] = 'OPEN'
    success['observation']['literature_service'].update(fallback_available=True, fallback_service='crossref')
    rows.append(success)
    status = reduce_availability(points(rows, []), now=37)
    assert status['state'] == 'ready' and not status['pending']
    assert status['active_service'] == 'crossref'
    assert status['primary_failure']
