from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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
