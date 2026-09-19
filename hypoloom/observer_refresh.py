"""Disposable run-bound UI refresh sharing; never a run or audit authority."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

from .monitor import HostStatusBackoff, MonitorError, build_observer_snapshot
from .observation_api import build_product_observation
from .oracle import Oracle
from .state_store import StateStore


class ObserverRefresh:
    """Coalesce fast read projections and separately report historical audits.

    CLI/qualification uses the ordinary synchronous Oracle. This reader belongs
    to a single server process, has no persistent cache, and never discovers runs.
    """

    def __init__(self, state_root, run_id, *, refresh_seconds=2.0, audit_seconds=30.0):
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

    def get(self, *, force=False):
        with self._lock:
            if self._closed: raise MonitorError('observer reader is closed')
            version = self._data_version()
            if force or self._cached is None or version != self._cached_version or time.monotonic() - self._cached_at >= self.refresh_seconds:
                with StateStore(self.root).readonly_connection() as db:
                    db.execute('BEGIN')
                    snapshot = build_observer_snapshot(self.root, self.run_id, _connection=db,
                        _oracle={'ok': None, 'issues': []}, _host_backoff=self._host_backoff)
                    activity = build_product_observation(self.root, self.run_id, _connection=db,
                        _snapshot=snapshot.to_dict())
                self._cached = {'snapshot': snapshot, 'activity': activity}
                self._cached_at = time.monotonic()
                self._cached_version = version
            if not self._closed and not self.audit_running and (self._audit_done is None or time.monotonic() - self._audit_done >= self.audit_seconds):
                self._thread = threading.Thread(target=self._run_audit,
                    name='hypoloom-observer-audit', daemon=True)
                self._thread.start()
            return {**self._cached, 'snapshot': replace(self._cached['snapshot'], oracle=self._historical_audit())}

    def wait_for_audit(self, timeout=None):
        thread = self._thread
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
