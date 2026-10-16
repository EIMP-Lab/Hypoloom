"""Disposable run-bound UI refresh sharing; never a run or audit authority."""
from __future__ import annotations

import threading
import time
import json
from dataclasses import replace
from pathlib import Path

from .monitor import (HostStatusBackoff, MonitorError, build_observer_snapshot,
                      build_monitor_snapshot, _with_live_attempt_context)
from .observation_api import build_product_observation
from .observer_evidence import evidence_read_budget, read_attempt_evidence
from .oracle import Oracle
from .state_store import StateStore


class ObserverRefresh:
    """Coalesce fast read projections and separately report historical audits.

    CLI/qualification uses the ordinary synchronous Oracle. This reader belongs
    to a single server process, has no persistent cache, and never discovers runs.
    """

    def __init__(self, state_root, run_id, *, refresh_seconds=2.0, audit_seconds=30.0, evidence_seconds=10.0):
        self.root, self.run_id = Path(state_root).resolve(), run_id
        self.refresh_seconds, self.audit_seconds = refresh_seconds, audit_seconds
        self._lock, self._audit_lock = threading.Lock(), threading.Lock()
        self._cached, self._cached_at = None, 0.0
        self._audit_result, self._audited_at, self._audit_done = None, None, None
        self._audit_error, self._closed = False, False
        self._thread = None
        self._version_lock = threading.Lock()
        self._version_connection = self._version_context = None
        self._cached_version = None
        self._host_backoff = HostStatusBackoff()
        self._refresh_lock = threading.Lock()
        self._refresh_thread = None
        self._refresh_error, self._retry_at = False, 0.0
        self.evidence_seconds = evidence_seconds
        self._evidence_thread, self._evidence_done = None, None
        self._evidence_cursor = 0
        self._evidence_changed = False
        self._offline = None

    def _data_version(self):
        # SQLite's connection-local change indication invalidates only this
        # disposable display cache. Keep no read transaction open between polls.
        with self._version_lock:
            if self._closed: raise MonitorError('observer reader is closed')
            if self._version_connection is None:
                self._version_context = StateStore(self.root).readonly_connection(check_same_thread=False)
                self._version_connection = self._version_context.__enter__()
            return self._version_connection.execute('PRAGMA data_version').fetchone()[0]

    @property
    def audit_running(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _audit(self):
        return Oracle(self.root / 'state.sqlite', self.root / 'artifacts').audit(self.run_id).to_dict()

    def _run_audit(self):
        if self._closed: return
        try:
            result = self._audit()
        except Exception:
            # This is a display failure, not a clean audit, and must not leak
            # exception text containing local paths or database content.
            with self._audit_lock:
                self._audit_result, self._audit_error = None, True
                self._audit_done = time.monotonic()
        else:
            with self._audit_lock:
                self._audit_result, self._audit_error = result, False
                self._audited_at, self._audit_done = time.time(), time.monotonic()

    def _historical_audit(self):
        with self._audit_lock:
            result = dict(self._audit_result or {'ok': None, 'issues': []})
            result['observation'] = {
                'state': 'error' if self._audit_error else 'historical' if self._audit_result is not None else 'pending',
                'audited_at': self._audited_at,
                'age_seconds': None if self._audited_at is None else max(0, time.time() - self._audited_at),
                'refreshing': self.audit_running,
            }
            return result

    def _warm_evidence(self):
        # Release the database read before any slow file I/O. Only the existing
        # bounded, metadata-validated summary cache receives these observations.
        with StateStore(self.root).readonly_connection() as db:
            db.execute('BEGIN')
            identities = [dict(row) for row in db.execute(
                "SELECT attempt_id,state FROM execution_attempts WHERE run_id=? AND kind='AGENT' "
                "ORDER BY started_at DESC,attempt_id", (self.run_id,))]
            opened = [row for row in identities if row['state'] == 'OPEN']
            history = [row for row in identities if row['state'] != 'OPEN']
            if len(opened) >= 16:
                opened, history = [], identities
            selected = opened[:16]
            if history:
                start = self._evidence_cursor % len(history)
                count = min(16 - len(selected), len(history))
                selected += (history[start:] + history[:start])[:count]
                self._evidence_cursor = (start + count) % len(history)
            rows = [dict(db.execute('SELECT * FROM execution_attempts WHERE attempt_id=?',
                                   (row['attempt_id'],)).fetchone()) for row in selected]
        with evidence_read_budget(16):
            for row in rows:
                if self._closed: return
                with evidence_read_budget(0):
                    before = read_attempt_evidence(self.root, self.run_id, row)
                if read_attempt_evidence(self.root, self.run_id, row) != before:
                    self._evidence_changed = True

    def _run_evidence(self):
        try:
            if not self._closed: self._warm_evidence()
        except Exception:
            # A cache miss remains deferred/unknown; no failure text or false
            # clean verdict is published by this best-effort display worker.
            pass
        finally:
            self._evidence_done = time.monotonic()

    def get(self, *, force=False):
        with self._lock:
            if self._closed: raise MonitorError('observer reader is closed')
            version = self._data_version()
            if force or self._cached is None or version != self._cached_version or time.monotonic() - self._cached_at >= self.refresh_seconds:
                rebuild = force or self._offline is None or version != self._cached_version or self._evidence_changed
                self._evidence_changed = False
                with StateStore(self.root).readonly_connection() as db, evidence_read_budget(0):
                    db.execute('BEGIN')
                    where = '' if rebuild else " AND state='OPEN'"
                    attempts = [{**row, 'observation': json.loads(row['observation_json'] or '{}')}
                        for row in db.execute('SELECT * FROM execution_attempts WHERE run_id=?' + where, (self.run_id,))]
                    if rebuild:
                        offline = build_monitor_snapshot(self.root, self.run_id, _connection=db,
                            _oracle={'ok': None, 'issues': []}, _attempts=attempts)
                    else:
                        # Retain only compact projections between polls. Mutable
                        # native files and Host liveness are still observed now;
                        # historical summary timestamps are not relabelled.
                        from .observer_summary import _TERMINAL, _host_observation, refresh_summary_clock
                        live = _with_live_attempt_context(self.root, self.run_id,
                            tuple(row for row in self._offline.attempts if row.state == 'OPEN'),
                            {row['attempt_id']: row['observation'] for row in attempts})
                        by_id = {row.attempt_id: row for row in live}
                        host = _host_observation(self.root)
                        host['orphaned'] = self._offline.status['run_state'] not in _TERMINAL and host['availability'] == 'absent'
                        observed_at = time.time()
                        overview = refresh_summary_clock(self._offline.overview,
                            self._offline.status['run_state'], observed_at)
                        offline = replace(self._offline, observed_at=observed_at,
                            attempts=tuple(by_id.get(row.attempt_id, row) for row in self._offline.attempts),
                            overview={**overview, 'host_observation': host})
                    # Busy resident ticks can exceed the short synchronous probe.
                    # This shared worker may wait; HTTP poll() stays nonblocking
                    # and retains the old observation's age until it completes.
                    snapshot = build_observer_snapshot(self.root, self.run_id, host_timeout=10.0, _connection=db,
                        _oracle={'ok': None, 'issues': []}, _host_backoff=self._host_backoff, _snapshot=offline)
                    activity = (build_product_observation(self.root, self.run_id, _connection=db,
                        _snapshot=snapshot.to_dict(), _attempts=attempts) if rebuild else
                        {**self._cached['activity'], 'observed_at': snapshot.observed_at, 'summary': snapshot.overview})
                with self._refresh_lock:
                    if self._closed: raise MonitorError('observer reader is closed')
                    self._cached = {'snapshot': snapshot, 'activity': activity}
                    self._cached_at = time.monotonic()
                    self._cached_version = version
                    self._offline = offline
                    self._refresh_error = False
            evidence_running = self._evidence_thread is not None and self._evidence_thread.is_alive()
            if not self._closed and not evidence_running and (self._evidence_done is None or
                    time.monotonic() - self._evidence_done >= self.evidence_seconds):
                self._evidence_thread = threading.Thread(target=self._run_evidence,
                    name='hypoloom-observer-evidence', daemon=True)
                self._evidence_thread.start()
            if not self._closed and not self.audit_running and (self._audit_done is None or time.monotonic() - self._audit_done >= self.audit_seconds):
                self._thread = threading.Thread(target=self._run_audit,
                    name='hypoloom-observer-audit', daemon=True)
                self._thread.start()
            return {**self._cached, 'snapshot': replace(self._cached['snapshot'], oracle=self._historical_audit())}

    def _run_refresh(self):
        try:
            self.get()
        except Exception:
            # Keep the last complete transaction visible, with an explicit
            # failure. Neither private exception text nor a clean verdict leaks.
            with self._refresh_lock:
                self._refresh_error = True
                self._retry_at = time.monotonic() + self.refresh_seconds

    def poll(self):
        """Read the existing cache without waiting for projection or Host I/O.

        One disposable worker shares get() with the TUI and full-detail readers.
        A successful HTTP response does not make an old observation fresh.
        """
        with self._refresh_lock:
            if self._closed: raise MonitorError('observer reader is closed')
            now = time.monotonic()
            running = self._refresh_thread is not None and self._refresh_thread.is_alive()
            if (not running and now >= self._retry_at and
                    (self._cached is None or now - self._cached_at >= self.refresh_seconds)):
                self._refresh_thread = threading.Thread(target=self._run_refresh,
                    name='hypoloom-observer-refresh', daemon=True)
                self._refresh_thread.start()
                running = True
            cached = self._cached
            observation = {
                'state': 'error' if self._refresh_error else 'pending' if cached is None else 'ready',
                'refreshing': running or self._lock.locked(),
                'age_seconds': None if cached is None else max(0, time.time() - cached['snapshot'].observed_at),
            }
        if cached is None:
            return {'snapshot': None, 'activity': None, 'observation': observation}
        return {**cached, 'snapshot': replace(cached['snapshot'], oracle=self._historical_audit()),
            'observation': observation}

    def wait_for_refresh(self, timeout=None):
        thread = self._refresh_thread
        if thread is not None and thread is not threading.current_thread(): thread.join(timeout=timeout)

    def wait_for_audit(self, timeout=None):
        thread = self._thread
        if thread is not None and thread is not threading.current_thread(): thread.join(timeout=timeout)

    def wait_for_evidence(self, timeout=None):
        thread = self._evidence_thread
        if thread is not None and thread is not threading.current_thread(): thread.join(timeout=timeout)

    def close(self):
        # Like the existing TUI snapshot reader, an already-running daemon read
        # may finish after the UI closes. It cannot write or schedule more work.
        # Never wait on a slow projection lock or full audit on the UI thread.
        self._closed = True
        with self._version_lock:
            if self._version_connection is not None:
                self._version_context.__exit__(None, None, None)
                self._version_connection = self._version_context = None
        self.wait_for_audit(timeout=0.05)
        self.wait_for_refresh(timeout=0.05)
        self.wait_for_evidence(timeout=0.05)
