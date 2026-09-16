"""Profile-selected readers owned by one already authorized CLI Host lifetime.

The Host enters this context only after acquiring its existing exclusive lock.
No writer capability, run mutation, listener registry or persistent token lives
here. Notification progress remains in the existing disjoint delivery cursors.
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from pathlib import Path

from .notification_tailer import NotificationError, NotificationTailer, post_json
from .observer_refresh import ObserverRefresh
from .state_store import StateStore
from .web_observer import create_server


class ObserverServices:
    def __init__(self, state_root, *, output_root=None, poll_interval=1.0,
                 output=None, errors=None, environ=None, transport=None, proxy_env=None):
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("observer poll interval must be finite and positive")
        self.state_root = Path(state_root).resolve()
        self.output_root = (self.state_root.with_name(self.state_root.name + "-notifications")
            if output_root is None else Path(output_root).absolute())
        self.poll_interval = poll_interval
        self.output = sys.stdout if output is None else output
        self.errors = sys.stderr if errors is None else errors
        self.environ = environ
        self.proxy_env = proxy_env
        self._transport = post_json if transport is None else transport
        self.dashboards = {}
        self._web_threads = {}
        self._notifiers = {}
        self._notification_readers = {}
        self._reported = {}
        self._stop = threading.Event()
        self._poll_lock = threading.RLock()
        self._thread = None
        self._closed = False
        self._shutdown_deadline = None
        self._excluded_runs = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _diagnostic(self, key, message):
        if self._reported.get(key) != message:
            print(f"observer services: {key}: {message}", file=self.errors, flush=True)
            self._reported[key] = message

    def _initial_scope(self):
        if self._excluded_runs is None:
            # Capture before the Host's command/tick loop starts. Existing
            # terminal runs are not automatic delivery subscriptions; later
            # newly created IDs remain eligible even if they finish rapidly.
            heads = StateStore(self.state_root).inspect(
                "SELECT run_id,state FROM workflow_transitions AS t "
                "WHERE workflow_kind='RUN' AND revision=(SELECT MAX(revision) "
                "FROM workflow_transitions WHERE workflow_id=t.workflow_id)")
            self._excluded_runs = {row["run_id"] for row in heads if row["state"] in {"COMPLETED", "FAILED", "CANCELLED"}}

    def _post(self, url, document, *, headers, timeout, proxy_url=None):
        if self._shutdown_deadline is not None:
            remaining = self._shutdown_deadline - time.monotonic()
            if remaining <= 0:
                raise NotificationError("shutdown delivery window ended; cursor retained for retry")
            timeout = min(timeout, remaining)
        options = {} if proxy_url is None else {"proxy_url": proxy_url}
        return self._transport(url, document, headers=headers, timeout=timeout, **options)

    def _dashboard(self, run_id, config):
        if run_id in self.dashboards or not config.get("enabled", False):
            return
        host, port = config.get("host", "127.0.0.1"), config.get("port", 8765)
        try:
            server = create_server(self.state_root, run_id, host=host, port=port)
            server.log_requests = False
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                name=f"hypoloom-dashboard-{run_id}", daemon=True)
            try:
                thread.start()
            except BaseException:
                server.server_close()
                raise
            self.dashboards[run_id], self._web_threads[run_id] = server, thread
            self._reported.pop(f"{run_id}/dashboard", None)
            print(f"web observer [{run_id}]: {server.startup_url()}", file=self.output, flush=True)
        except (OSError, ValueError) as error:
            # Do not expose arbitrary exception strings or configured secrets.
            self._diagnostic(f"{run_id}/dashboard", f"{type(error).__name__}; configured loopback listener unavailable; retry pending")

    def _notify(self, run_id, sinks):
        if not ("log" in sinks or any(sinks.get(name, {}).get("enabled", False) for name in ("telegram", "webhook"))):
            return
        try:
            if run_id not in self._notifiers:
                reader = (self.dashboards[run_id].refresh_reader if run_id in self.dashboards
                          else ObserverRefresh(self.state_root, run_id, refresh_seconds=self.poll_interval))
                self._notification_readers[run_id] = reader
                self._notifiers[run_id] = NotificationTailer(self.state_root, run_id, self.output_root,
                    environ=self.environ, transport=self._post, proxy_env=self.proxy_env,
                    observation_reader=lambda: reader.get(force=self._stop.is_set())["activity"])
            result = self._notifiers[run_id].poll_once()
            for message in result.errors + result.warnings:
                self._diagnostic(f"{run_id}/notification/{message.split(':', 1)[0]}", message)
            if any(result.delivered.values()):
                print("notifications: " + json.dumps({"run_id": run_id, "delivered": result.delivered}, sort_keys=True),
                    file=self.output, flush=True)
        except NotificationError as error:
            self._diagnostic(f"{run_id}/notification", str(error))
        except Exception as error:
            self._diagnostic(f"{run_id}/notification", f"{type(error).__name__}; delivery pending, retry on next poll")

    def poll_once(self):
        """Read current admitted settings/events; also usable for focused checks."""
        with self._poll_lock:
            try:
                self._initial_scope()
                rows = StateStore(self.state_root).inspect(
                    "SELECT run_id,payload_json FROM research_facts WHERE fact_type='GoalDefined' ORDER BY run_id")
                for row in rows:
                    if row["run_id"] in self._excluded_runs:
                        continue
                    if self._shutdown_deadline is not None and time.monotonic() >= self._shutdown_deadline:
                        self._diagnostic("shutdown", "delivery window ended; unadvanced cursors remain retryable")
                        break
                    contract = json.loads(row["payload_json"])["research_contract"]
                    notify = contract.get("methodology", {}).get("controls", {}).get("product", {}).get("notify", {})
                    sinks = notify.get("sinks", {})
                    self._dashboard(row["run_id"], sinks.get("dashboard", {}))
                    self._notify(row["run_id"], sinks)
            except Exception as error:
                self._diagnostic("observation", f"{type(error).__name__}; read-only retry pending")

    def _run(self):
        try:
            while not self._stop.is_set():
                self.poll_once()
                self._stop.wait(self.poll_interval)
        finally:
            # A fast run may finish between normal polls. Read its accepted
            # terminal event once more; failed delivery never advances cursor.
            self.poll_once()

    def start(self):
        if self._closed:
            raise ValueError("closed observer services cannot be restarted; create a new lifetime")
        if self._thread is None:
            try:
                self._initial_scope()
            except Exception as error:
                self._diagnostic("observation", f"{type(error).__name__}; read-only retry pending")
            self._thread = threading.Thread(target=self._run, name="hypoloom-observer-services", daemon=True)
            self._thread.start()
        return self

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._shutdown_deadline = time.monotonic() + 20.0
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        for reader in self._notification_readers.values():
            reader.close()
        for run_id, server in self.dashboards.items():
            server.shutdown()
            server.server_close()
            self._web_threads[run_id].join()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


__all__ = ["ObserverServices"]
