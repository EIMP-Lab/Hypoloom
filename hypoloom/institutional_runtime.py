"""Run-owned browser resources. Durable run state remains the Host's authority."""
from __future__ import annotations

import os
import errno
from pathlib import Path
import threading
import time

from .institutional_access import InstitutionalAccess
from .institutional_browser import PlaywrightInstitutionalBrowser
from .institutional_connection import ConnectionFailure, _profile_busy
from .institutional_relay import CDPRelay, tcp_backend, unix_backend


def private_profile(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ConnectionFailure("browser_profile_must_be_private")
    return path


class RunBrowser(PlaywrightInstitutionalBrowser):
    def __init__(self, spec, run_id):
        super().__init__(spec.get("endpoint") or "http://127.0.0.1:1",
            library_url=spec.get("library_url"), ui_url=spec.get("browser_ui_url"))
        self.spec, self.run_id = spec, run_id
        self.relay = self.gui = self.container = None
        self.context = None
        self.directory = None
        self.directory_owner = None
        self.socket_path = None
        self.stopping = threading.Event()
        self._headers = None
        self.resource_lock = threading.RLock()
        self.allocation_error = None
        self._allocate()

    def _allocate(self):
        with self.resource_lock:
            if self.stopping.is_set(): raise ConnectionFailure("run_connection_closed")
            try:
                self._start_resources()
                self.allocation_error = None
            except (OSError, ValueError) as exc:
                self._release_transports()
                self.allocation_error = "port_in_use" if getattr(exc, "errno", None) == errno.EADDRINUSE else "connection_unavailable"
            except BaseException:
                self._release_transports()
                raise

    def _start_resources(self):
        spec = self.spec
        if spec["mode"] == "client":
            from .institutional_lifetime import TransportDirectory
            self.directory_owner = TransportDirectory(spec["ready_timeout"])
            self.directory = self.directory_owner.path
            self.socket_path = self.directory / "browser.sock"
            dial = lambda: unix_backend(self.socket_path)
        elif spec["mode"] == "existing":
            dial = lambda: tcp_backend(spec["endpoint"])
        elif spec["mode"] == "server":
            from .institutional_container import Container
            self.container = Container(spec, self.run_id)
            dial = self.container.cdp
            self.gui = CDPRelay(self.container.gui, gui=True)
            self.ui_url = self.gui.endpoint
        else:
            return
        self.relay = CDPRelay(dial, port=spec.get("port", 0))
        self.endpoint = self.relay.endpoint
        self._headers = {"Authorization": "Bearer " + self.relay.token}

    @property
    def configuration(self):
        # Live ports/paths/tokens never affect frozen runtime identity.
        return {"connection": {k: v for k, v in self.spec.items() if k != "desktop"}}

    def details(self, *, private=False):
        value = {"mode": self.spec["mode"], "state": "closed" if self.stopping.is_set() else "allocated",
                 "endpoint": self.relay.endpoint if self.relay else None}
        if self.allocation_error and not self.stopping.is_set():
            value.update(state="unavailable", reason=self.allocation_error)
        if self.socket_path is not None and not self.allocation_error and not self.stopping.is_set():
            value["state"] = "client_socket_present" if self.socket_path.exists() else "waiting_for_client"
            if private: value["socket_path"] = str(self.socket_path)
        if private and self.gui is not None:
            value.update(browser_ui_url=self.gui.endpoint, browser_ui_username="hypoloom", browser_ui_password=self.gui.token)
        return value

    def _connect(self):
        if self.stopping.is_set(): raise ConnectionFailure("run_connection_closed")
        if self.allocation_error:
            self._allocate()
            if self.allocation_error: raise ConnectionFailure(self.allocation_error)
        if self.stopping.is_set(): raise ConnectionFailure("run_connection_closed")
        if self.spec["mode"] != "local": return super()._connect()
        if self.context is not None and self.context.browser.is_connected(): return self.context
        if not self.spec.get("executable"): raise ConnectionFailure("browser_executable_missing")
        if not self.spec["desktop"]["kind"]: raise ConnectionFailure(self.spec["desktop"]["reason"])
        profile = private_profile(self.spec["profile"])
        if _profile_busy(profile): raise ConnectionFailure("browser_profile_in_use")
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        try:
            self.context = self._playwright.chromium.launch_persistent_context(str(profile),
                executable_path=self.spec["executable"], headless=False, chromium_sandbox=True,
                ignore_default_args=["--password-store=basic", "--use-mock-keychain"],
                args=["--ozone-platform=" + self.spec["desktop"]["kind"]], timeout=self._remaining(15) * 1000)
            self._browser = self.context.browser
            return self.context
        except BaseException:
            self.close()
            raise

    def check_connection(self, *, timeout_seconds):
        deadline = time.monotonic() + timeout_seconds
        # Wait for the existing client transport before starting Playwright.
        # A present socket still requires a real CDP/context connection below.
        while self.socket_path is not None and not self.socket_path.exists():
            if self.stopping.is_set(): return {"state": "closed"}
            remaining = deadline - time.monotonic()
            if remaining <= 0: return {"state": "waiting_for_client"}
            time.sleep(min(.1, remaining))
        result = super().check_connection(timeout_seconds=max(.001, deadline - time.monotonic()))
        if result["state"] != "ready":
            observed = self.details()
            if observed["state"] in {"waiting_for_client", "unavailable", "closed"}:
                result = {"state": observed["state"]}
            if "reason" in observed: result["reason"] = observed["reason"]
        return result

    def release(self):
        """Host-thread release interrupts transports before worker-thread close."""
        self.stopping.set()
        with self.resource_lock: self._release_transports()

    def _release_transports(self):
        failure = None
        for name in ("relay", "gui", "container", "directory_owner"):
            owned = getattr(self, name)
            try:
                if owned is not None: owned.close()
            except Exception as exc: failure = failure or exc
            finally: setattr(self, name, None)
        self.directory = None
        self._headers = None
        if self.spec["mode"] == "server": self.ui_url = None
        if failure is not None: raise failure

    def close(self):
        try:
            if self.context is not None: self.context.close()
        finally:
            self.context = None
            super().close()


class RunAccess:
    def __init__(self, spec, *, pdf_locator=None, **settings):
        self.spec, self.settings = spec, settings
        self._pdf_locator = pdf_locator
        self.instances = {}
        self.retired = []
        self.read_choices = lambda run_id: ({}, {})
        self.event_sink = None
        self.closed = False
        self.lock = threading.RLock()
        self.template = InstitutionalAccess(PlaywrightInstitutionalBrowser("http://127.0.0.1:1"), **settings)
        self.acquisition_seconds = self.template.acquisition_seconds

    @property
    def configuration(self):
        return {"connection": {k: v for k, v in self.spec.items() if k != "desktop"}, **self.settings}

    def _selected(self, run_id):
        selected = self.instances.get(run_id, self.template)
        selected.read_choices, selected.event_sink = self.read_choices, self.event_sink
        return selected

    def sync(self, live_runs, *, create=True):
        with self.lock:
            if self.closed: return
            failure = None
            for run_id in set(self.instances) - set(live_runs):
                access = self.instances.pop(run_id)
                try: access.backend.release()
                except Exception as exc: failure = failure or exc
                finally:
                    access.close(wait=False)
                    self.retired.append(access)
            if failure is not None: raise failure
            for run_id in (set(live_runs) - set(self.instances)) if create else ():
                backend = RunBrowser(self.spec, run_id)
                if self._pdf_locator is not None:
                    backend._pdf_locator = self._pdf_locator
                self.instances[run_id] = InstitutionalAccess(backend, **self.settings)

    def tick(self, active_runs):
        self.sync(active_runs)
        with self.lock:
            for run_id in active_runs:
                if run_id in self.instances: self._selected(run_id).tick({run_id})

    def status(self, run_id):
        with self.lock:
            value = self._selected(run_id).status(run_id)
            value.update(browser_endpoint=None, browser_ui_url=None, library_url=self.spec.get("library_url"), windows_setup_command=None)
            access = self.instances.get(run_id)
            value["connection"] = access.backend.details() if access else {"mode": self.spec["mode"], "state": "inactive"}
            if access: value["browser_ui_url"] = access.backend.ui_url
            return value

    def connections(self, run_id=None):
        with self.lock:
            return [{"run_id": key, **value.backend.details(private=True)} for key, value in self.instances.items()
                    if run_id is None or run_id == key]

    def request(self, scope, *args, **kwargs):
        with self.lock:
            if scope[0] not in self.instances: return {"status": "unavailable", "reason": "run_connection_inactive"}
            return self._selected(scope[0]).request(scope, *args, **kwargs)

    def peek(self, scope, *args):
        with self.lock: return self._selected(scope[0]).peek(scope, *args)

    def paper_identity(self, run_id, url):
        with self.lock: return self._selected(run_id).paper_identity(run_id, url)

    def automatic_reason(self, scope, *args, **kwargs):
        with self.lock: return self._selected(scope[0]).automatic_reason(scope, *args, **kwargs)

    def control(self, run_id, *args, **kwargs):
        with self.lock:
            if run_id not in self.instances: raise ValueError("run_connection_inactive")
            return self._selected(run_id).control(run_id, *args, **kwargs)

    def check_connection(self, run_id, *, timeout_seconds):
        with self.lock:
            return self._selected(run_id).check_connection(run_id, timeout_seconds=timeout_seconds)

    def discard_preparation(self, run_id, provider):
        with self.lock:
            if run_id in self.instances:
                self.instances[run_id].discard_preparation(run_id, provider)

    def close(self):
        try:
            with self.lock: self.sync(set())
        finally:
            self.closed = True
            for access in self.retired: access._executor.shutdown(wait=True)
            self.retired.clear()
            self.template.close()
