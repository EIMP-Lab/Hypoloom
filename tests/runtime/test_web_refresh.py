from __future__ import annotations

import json
import gzip
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from hypoloom.observer_refresh import ObserverRefresh
from hypoloom.web_observer import create_server
from test_web_observer import _create_fresh_run, _request, _start, _stop, _row_census


def test_refresh_auth_shared_transaction_and_no_durable_writes(tmp_path):
    root = tmp_path / 'state'
    _create_fresh_run(root, 'refresh-run')
    before = _row_census(root / 'state.sqlite')
    server = create_server(root, 'refresh-run', port=0)
    server.log_requests = False
    thread = _start(server)
    try:
        assert _request(server, 'GET', '/api/refresh')[0] == 401
        code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token)
        assert code == 202 and json.loads(body)['observation']['state'] == 'pending'
        server.refresh_reader.wait_for_refresh(timeout=5)
        code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token)
        assert code == 200
        data = json.loads(body)
        assert data['snapshot']['run_id'] == data['activity']['run_id'] == 'refresh-run'
        assert data['snapshot']['observed_at'] == data['activity']['observed_at']
        assert 'summary' not in data['activity']
        assert data['snapshot']['oracle']['observation']['state'] in {'pending', 'historical'}
        assert data['activity']['events_page']['total'] >= len(data['activity']['events'])
        assert _request(server, 'GET', '/api/refresh?run_id=foreign', token=server.access_token)[0] == 400
    finally:
        _stop(server, thread)
        server.refresh_reader.wait_for_audit(timeout=5)
    assert _row_census(root / 'state.sqlite') == before
    assert not server.refresh_reader.audit_running


def test_refresh_coalesces_readers_and_slow_audit_never_blocks_status(tmp_path, monkeypatch):
    root = tmp_path / 'state'
    _create_fresh_run(root, 'refresh-run')
    entered, release = threading.Event(), threading.Event()
    calls = []
    def audit(self):
        calls.append(1); entered.set()
        assert release.wait(5)
        return {'ok': True, 'issues': []}
    monkeypatch.setattr(ObserverRefresh, '_audit', audit)
    reader = ObserverRefresh(root, 'refresh-run')
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: reader.get(), range(4)))
        assert entered.wait(2)
        assert len({item['snapshot'].observed_at for item in results}) == 1
        assert all(item['snapshot'].oracle['ok'] is None for item in results)
        assert len(calls) == 1
        release.set()
        reader.wait_for_audit()
        result = reader.get()['snapshot']
        assert result.oracle['ok'] is True
        assert result.oracle['observation']['state'] == 'historical'
        assert result.oracle['observation']['audited_at'] is not None
        assert len(calls) == 1
    finally:
        release.set(); reader.close()


def test_refresh_retains_live_traffic_after_busy_host_without_blocking_http():
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from hypoloom.monitor import build_monitor_snapshot
    from test_monitor_live_status import _one_status_response

    with TemporaryDirectory(prefix='hypweb-') as directory:
        root = Path(directory)
        _create_fresh_run(root, 'refresh-run')
        before = _row_census(root / 'state.sqlite')
        live = dict(build_monitor_snapshot(root, 'refresh-run').status)
        live.update(quiescent_for_backup=False, agent_usage={}, resource_waits=[],
            provider_traffic={'scope': 'run', 'observed_at': time.time(), 'warming_up': False,
                'rpm': 7, 'in_flight': 1, 'queued': 0,
                'by_expert': {'scientist': {'rpm': 7, 'in_flight': 1, 'queued': 0}}})
        # Retained-run STATUS took 4.1 seconds during normal model activity.
        host, requests, errors = _one_status_response(root / 'control.sock', live,
            response_delay_seconds=4)
        server = create_server(root, 'refresh-run', port=0)
        thread = _start(server)
        try:
            for _ in range(3):
                started = time.monotonic()
                code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token)
                assert time.monotonic() - started < 2
                assert code == 202 and json.loads(body)['observation']['state'] == 'pending'
            server.refresh_reader.wait_for_refresh(timeout=7)
            code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token)
            snapshot = json.loads(body)['snapshot']
            assert code == 200 and len(requests) == 1 and not errors
            assert snapshot['overview']['host_observation']['availability'] == 'responded'
            assert snapshot['status']['provider_traffic'] == live['provider_traffic']
            assert server.refresh_reader._host_backoff.consecutive_failures == 0
        finally:
            _stop(server, thread)
            host.join(timeout=5)
        assert not host.is_alive() and _row_census(root / 'state.sqlite') == before


def test_historical_audit_failure_is_not_clean(tmp_path, monkeypatch):
    root = tmp_path / 'state'
    _create_fresh_run(root, 'refresh-run')
    def fail(self):
        raise OSError('private path must not reach HTTP')
    monkeypatch.setattr(ObserverRefresh, '_audit', fail)
    reader = ObserverRefresh(root, 'refresh-run')
    try:
        reader.get(); reader.wait_for_audit()
        result = reader.get()['snapshot'].oracle
        assert result['ok'] is None
        assert result['observation']['state'] == 'error'
        assert 'private path' not in json.dumps(result)
    finally:
        reader.close()


def test_history_pagination_preserves_total_and_is_bounded():
    from hypoloom.web_observer import WebObserverRequestHandler
    rows = list(range(250))
    page, detail = WebObserverRequestHandler._history_page(rows, limit=100)
    assert page == list(range(150, 250)) and detail['total'] == 250 and detail['has_more']
    older, detail = WebObserverRequestHandler._history_page(rows, offset=100, limit=100)
    assert older == list(range(50, 150)) and detail['total'] == 250
    assert rows == list(range(250))


def test_tui_audit_badge_never_treats_pending_as_issues_or_historical_as_current():
    from hypoloom.tui import _oracle_observation_label
    assert 'AUDIT PENDING' in _oracle_observation_label({'ok': None})
    assert 'AUDIT ERROR' in _oracle_observation_label({'ok': None, 'observation': {'state': 'error'}})
    assert 'LAST CLEAN' in _oracle_observation_label({'ok': True, 'observation': {'state': 'historical', 'audited_at': 1}})


def test_close_does_not_wait_for_busy_projection_lock_or_audit(tmp_path, monkeypatch):
    reader = ObserverRefresh(tmp_path, 'r')
    release = threading.Event()
    reader._thread = threading.Thread(target=lambda: release.wait(5), daemon=True)
    reader._thread.start()
    reader._lock.acquire()
    try:
        start = time.monotonic()
        reader.close()
        assert time.monotonic() - start < 0.5
    finally:
        reader._lock.release(); release.set(); reader.wait_for_audit(timeout=5)
    import pytest
    from hypoloom.monitor import MonitorError
    with pytest.raises(MonitorError, match='closed'): reader.get()


def test_refresh_shares_one_host_status_backoff_with_the_snapshot_builder(tmp_path, monkeypatch):
    from hypoloom import observer_refresh
    from hypoloom.monitor import HostStatusBackoff, build_monitor_snapshot
    root = tmp_path / 'state'
    _create_fresh_run(root, 'refresh-run')
    calls = []
    def builder(state_root, run_id, **kwargs):
        calls.append(kwargs)
        return build_monitor_snapshot(state_root, run_id)
    monkeypatch.setattr(observer_refresh, 'build_observer_snapshot', builder)
    reader = ObserverRefresh(root, 'refresh-run', audit_seconds=3600)
    try:
        reader.get()
        assert isinstance(reader._host_backoff, HostStatusBackoff)
        assert [call.get('_host_backoff') for call in calls] == [reader._host_backoff]
    finally:
        reader.wait_for_audit(timeout=5)
        reader.close()


def test_refresh_preserves_injected_snapshot_source_without_inventing_activity(tmp_path):
    from test_web_observer import _SyntheticSnapshot
    calls = []
    def source(root, run):
        calls.append((root, run))
        return _SyntheticSnapshot({'run_id': run, 'observed_at': 123, 'oracle': {'ok': None}})
    server = create_server(tmp_path / 'absent', 'custom', port=0, snapshot_builder=source)
    server.log_requests = False
    thread = _start(server)
    try:
        code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token)
        assert code == 200
        assert json.loads(body) == {'snapshot': {'run_id': 'custom', 'observed_at': 123, 'oracle': {'ok': None}}, 'activity': None}
        assert len(calls) == 1 and not server.refresh_reader.audit_running
    finally:
        _stop(server, thread)
    assert not (tmp_path / 'absent').exists()


def test_http_poll_never_waits_for_cold_or_refreshing_projection(tmp_path, monkeypatch):
    from hypoloom import observer_refresh
    root = tmp_path / 'state'
    _create_fresh_run(root, 'refresh-run')
    before = _row_census(root / 'state.sqlite')
    original = observer_refresh.build_observer_snapshot
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(*args, **kwargs):
        calls.append(1); entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(observer_refresh, 'build_observer_snapshot', slow)
    server = create_server(root, 'refresh-run', port=0)
    thread = _start(server)
    reader = server.refresh_reader
    try:
        started = time.monotonic()
        code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token)
        assert code == 202 and time.monotonic() - started < 1
        assert json.loads(body)['snapshot'] is None and entered.wait(2)
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: _request(server, 'GET', '/api/refresh',
                token=server.access_token), range(8)))
        assert all(code == 202 for code, _, _ in responses)
        assert len(calls) == 1
        release.set(); reader.wait_for_refresh(timeout=5)
        reader.wait_for_audit(timeout=5)
        old = reader._cached['snapshot']
        reader._cached = {**reader._cached, 'snapshot': replace(old, observed_at=time.time() - 40)}
        reader._cached_at = 0
        entered.clear(); release.clear()
        code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token)
        retained = json.loads(body)
        assert code == 200 and entered.wait(2)
        assert retained['snapshot']['observed_at'] == reader._cached['snapshot'].observed_at
        assert retained['observation']['age_seconds'] >= 40
        assert retained['observation']['refreshing']
        code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token,
            headers={'X-Hypoloom-Observed-At': str(retained['snapshot']['observed_at'])})
        unchanged = json.loads(body)
        assert code == 200 and unchanged['unchanged'] and 'snapshot' not in unchanged
        assert unchanged['observation']['age_seconds'] >= retained['observation']['age_seconds']
        assert len(calls) == 2
        release.set(); reader.wait_for_refresh(timeout=5)
        code, _, body = _request(server, 'GET', '/api/refresh', token=server.access_token,
            headers={'X-Hypoloom-Observed-At': str(retained['snapshot']['observed_at'])})
        assert code == 200 and json.loads(body)['snapshot']['observed_at'] > retained['snapshot']['observed_at']
    finally:
        release.set(); _stop(server, thread)
        reader.wait_for_refresh(timeout=5); reader.wait_for_audit(timeout=5)
    assert _row_census(root / 'state.sqlite') == before


@pytest.mark.parametrize('warm', [False, True])
def test_refresh_failure_retains_age_backs_off_and_recovers(tmp_path, monkeypatch, warm):
    from hypoloom import observer_refresh
    root = tmp_path / 'state'
    _create_fresh_run(root, 'refresh-run')
    reader = ObserverRefresh(root, 'refresh-run')
    original = observer_refresh.build_observer_snapshot
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError('private diagnostic')
    try:
        old = reader.get()['snapshot'] if warm else None
        reader._cached_at = 0
        monkeypatch.setattr(observer_refresh, 'build_observer_snapshot', fail)
        reader.poll(); reader.wait_for_refresh(timeout=5)
        for _ in range(4):
            result = reader.poll()
            assert result['observation']['state'] == 'error'
            assert not result['observation']['refreshing']
            assert (result['snapshot'].observed_at if warm else result['snapshot']) == (old.observed_at if warm else None)
            assert 'private diagnostic' not in str(result)
        assert len(calls) == 1
        monkeypatch.setattr(observer_refresh, 'build_observer_snapshot', original)
        reader._retry_at = 0
        reader.poll(); reader.wait_for_refresh(timeout=5)
        recovered = reader.poll()
        assert recovered['observation']['state'] == 'ready'
        assert recovered['snapshot'] is not None
    finally:
        reader.close(); reader.wait_for_refresh(timeout=5); reader.wait_for_audit(timeout=5)


def test_refresh_encoding_conditional_reads_and_auth(tmp_path):
    root = tmp_path / 'state'
    _create_fresh_run(root, 'refresh-run')
    server = create_server(root, 'refresh-run', port=0)
    thread = _start(server)
    try:
        server.refresh_reader.get(); server.refresh_reader.wait_for_audit(timeout=5)
        _, identity_headers, body = _request(server, 'GET', '/api/refresh', token=server.access_token)
        plain = json.loads(body)
        code, headers, zipped = _request(server, 'GET', '/api/refresh', token=server.access_token,
            headers={'Accept-Encoding': 'br, gzip'})
        assert code == 200 and headers['content-encoding'] == 'gzip'
        assert headers['vary'] == identity_headers['vary'] == 'Accept-Encoding'
        assert int(headers['content-length']) == len(zipped) < len(body) / 2
        decoded = json.loads(gzip.decompress(zipped))
        assert decoded['snapshot']['oracle']['observation']['age_seconds'] >= plain['snapshot']['oracle']['observation']['age_seconds']
        decoded['snapshot']['oracle']['observation']['age_seconds'] = plain['snapshot']['oracle']['observation']['age_seconds']
        assert decoded['snapshot'] == plain['snapshot']
        assert decoded['activity'] == plain['activity']
        for encoding in ('gzip;q=0', 'gzip;q=invalid', 'br'):
            code, headers, body = _request(server, 'GET', '/api/refresh', token=server.access_token,
                headers={'Accept-Encoding': encoding})
            assert code == 200 and 'content-encoding' not in headers
            assert json.loads(body)['snapshot']['run_id'] == 'refresh-run'
        previous = str(plain['snapshot']['observed_at'])
        assert _request(server, 'GET', '/api/refresh', headers={'X-Hypoloom-Observed-At': previous})[0] == 401
        for invalid in ('nan', 'inf', 'invalid'):
            assert _request(server, 'GET', '/api/refresh', token=server.access_token,
                headers={'X-Hypoloom-Observed-At': invalid})[0] == 400
    finally:
        _stop(server, thread)


def test_close_during_cold_refresh_cannot_publish_or_schedule_audit(tmp_path, monkeypatch):
    from hypoloom import observer_refresh
    from hypoloom.monitor import MonitorError
    root = tmp_path / 'state'
    _create_fresh_run(root, 'refresh-run')
    original = observer_refresh.build_observer_snapshot
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(observer_refresh, 'build_observer_snapshot', slow)
    reader = ObserverRefresh(root, 'refresh-run')
    try:
        assert reader.poll()['snapshot'] is None and entered.wait(2)
        started = time.monotonic(); reader.close()
        assert time.monotonic() - started < 0.5
        release.set(); reader.wait_for_refresh(timeout=5)
        assert reader._cached is None and not reader.audit_running
        with pytest.raises(MonitorError, match='closed'): reader.poll()
    finally:
        release.set(); reader.close(); reader.wait_for_refresh(timeout=5)
