"""Private control discovery and SSH lifetime; no run creation or state writes."""
from __future__ import annotations

import json
import http.client
import os
from pathlib import Path
import socket
import sqlite3
import stat
import sys
import threading
import time
from urllib.parse import urlsplit

from .control_protocol import ControlRequest, ControlResponse, receive_message, request_over_socket, send_message, socket_address


def validate_target(root, run_id, request_id):
    """Reuse init's existing path/ID rules; this scope is not an access token."""
    from .cli import _SAFE_PROFILE_NAME
    if not isinstance(root, (str, Path)) or not Path(root).is_absolute():
        raise ValueError("absolute_connection_state_root_required")
    if any(c in str(root) for c in "\x00\r\n") or any(
        not isinstance(value, str) or _SAFE_PROFILE_NAME.fullmatch(value) is None
        for value in (run_id, request_id)
    ):
        raise ValueError("explicit_run_target_required")
    return {"state_root": str(Path(root).resolve()), "run_id": run_id, "request_id": request_id}


def _payload_scope(root, payload):
    if set(payload) != {"run_id", "request_id"}:
        raise ValueError("explicit_run_target_required")
    return validate_target(root, payload["run_id"], payload["request_id"])


def _creation_lifecycle(store, scope):
    """Read the accepted CREATE_RUN and current head from the existing authority."""
    from .lifecycle import RunState
    from .state_store import StateTransaction
    with store.readonly_connection() as database:
        database.execute("BEGIN")
        transaction = StateTransaction(database)
        command = transaction.get_command(scope["request_id"])
        head = transaction.latest_transition(scope["run_id"])
    if command is None and head is None:
        return "pending"
    if (command is None or head is None or command["run_id"] != scope["run_id"]
            or command["workflow_id"] != scope["run_id"] or command["command_type"] != "CREATE_RUN"
            or command["response_status"] != "accepted" or head["workflow_kind"] != "RUN"
            or head["run_id"] != scope["run_id"]):
        raise ValueError("connection_creation_identity_mismatch")
    return "terminal" if head["state"] in {
        RunState.COMPLETED.value, RunState.FAILED.value, RunState.CANCELLED.value, RunState.STOPPING.value,
    } else "active"


def host_connections(host, payload):
    from .institutional_runtime import RunAccess
    scope = _payload_scope(host.state_root, payload)
    lifecycle = _creation_lifecycle(host.store, scope)
    entries = (host.institutional_access.connections(scope["run_id"])
        if lifecycle == "active" and isinstance(host.institutional_access, RunAccess) else [])
    return {"scope": scope, "lifecycle": lifecycle, "connections": entries}


def _private_root(root):
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("connection_root_must_be_private")


def _query(root, scope, *, preparation=False):
    _private_root(root)
    path = root / "control.sock"
    if not stat.S_ISSOCK(path.stat().st_mode):
        raise ValueError("connection_control_socket_required")
    response = request_over_socket(path, ControlRequest(1, "institutional-connections", None,
        "INSTITUTIONAL_CONNECTIONS", {name: scope[name] for name in ("run_id", "request_id")}), timeout=2)
    if response.status != "accepted":
        raise ValueError("connection_scope_rejected")
    result = response.result or {}
    lifecycles = {"preparing", "awaiting_launch", "terminal"} if preparation else {"active", "pending", "terminal"}
    if (result.get("scope") != scope or result.get("lifecycle") not in lifecycles
            or not isinstance(result.get("connections"), list)):
        raise ValueError("connection_scope_response_invalid")
    entries = result["connections"]
    if result["lifecycle"] in {"pending", "terminal"} and entries:
        raise ValueError("connection_inactive_scope_has_endpoint")
    expected_run = "preparation" if preparation else scope["run_id"]
    if any(not isinstance(item, dict) or item.get("run_id") != expected_run for item in entries):
        raise ValueError("connection_entry_scope_mismatch")
    return {**result, "connections": [{**item, "state_root": str(root)} for item in entries]}


def _durable_lifecycle(root, scope):
    """A stopped Host can still prove terminal state, never current liveness."""
    from .state_store import StateStore, StateStoreError
    database = root / "state.sqlite"
    if not database.is_file():
        return None
    try:
        return _creation_lifecycle(StateStore(root, busy_timeout_ms=1000), scope)
    except (sqlite3.Error, StateStoreError) as exc:
        raise ConnectionError("connection_state_unavailable") from exc


def connections(root, run_id=None, request_id=None, *, preparation_root=None):
    scope = validate_target(root, run_id, request_id)
    final = Path(scope["state_root"])
    if final.exists():
        _private_root(final)
        try:
            return _query(final, scope)
        except OSError:
            lifecycle = _durable_lifecycle(final, scope)
            if lifecycle == "terminal":
                return {"scope": scope, "lifecycle": "terminal", "connections": []}
            if lifecycle == "active":
                raise ConnectionError("connection_host_unavailable") from None
    if preparation_root is not None:
        if not Path(preparation_root).is_absolute():
            raise ValueError("absolute_preparation_root_required")
        return _query(Path(preparation_root).resolve(), scope, preparation=True)
    raise ConnectionError("connection_target_unavailable")


def run(args, *, output=print):
    scope = validate_target(args.state_root, getattr(args, "run_id", None), getattr(args, "request_id", None))
    preparation_root = getattr(args, "preparation_root", None)
    def current():
        return connections(scope["state_root"], scope["run_id"], scope["request_id"], preparation_root=preparation_root)
    if args.institutional_action == "forward-ready":
        return 0 if prepare_forward(scope["state_root"], scope["run_id"], args.socket_path,
            request_id=scope["request_id"], preparation_root=preparation_root) else 2
    if args.institutional_action == "watch":
        # The containing SSH session owns -R. Returning closes that session and
        # its forwarding on terminal state, Host loss or a new Host instance.
        checked, announced = 0, False
        while True:
            try:
                selected = current()
                if not any(c.get("socket_path") == args.socket_path for c in selected["connections"]): return 0
                if time.monotonic() - checked > 5:
                    from .institutional_relay import unix_backend
                    with unix_backend(args.socket_path) as peer:
                        peer.sendall(b"GET /json/version HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                        response = http.client.HTTPResponse(peer); response.begin()
                        raw = response.read((1 << 20) + 1)
                        if response.status != 200 or len(raw) > 1 << 20: return 2
                        identity = urlsplit(json.loads(raw)["webSocketDebuggerUrl"]).path
                        if identity != args.browser_path: return 2
                    if not announced:
                        output(json.dumps({"scope": scope, "lifecycle": selected["lifecycle"], "connected": True}))
                        sys.stdout.flush()
                        announced = True
                    checked = time.monotonic()
            except (OSError, ValueError, KeyError, http.client.HTTPException): return 2
            time.sleep(.5)
    selected = current()
    if not getattr(args, "include_ui_credentials", False):
        for item in selected["connections"]: item.pop("browser_ui_password", None)
    output(json.dumps(selected, ensure_ascii=False))
    return 0


def prepare_forward(root, run_id, path, *, request_id=None, preparation_root=None):
    """OpenSSH leaves dead stream-local pathnames; remove only this live run's
    refused socket before a new forward. An existing listener is never adopted.
    """
    try:
        current = connections(root, run_id, request_id, preparation_root=preparation_root)
        if not any(c.get("socket_path") == path for c in current["connections"]): return False
        selected = Path(path)
        try: prior = selected.lstat()
        except FileNotFoundError: return True
        if prior.st_uid != os.geteuid() or not stat.S_ISSOCK(prior.st_mode): return False
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(1)
            try: peer.connect(path)
            except ConnectionRefusedError:
                current = selected.lstat()
                if (current.st_dev, current.st_ino) != (prior.st_dev, prior.st_ino): return False
                selected.unlink()
                return True
        return False
    except (OSError, ValueError): return False


class PreparationControl:
    """Temporary operation endpoint using the same private control transport."""
    def __init__(self, root, access, handler=None, *, target=None):
        self.path = Path(root) / "control.sock"
        self.access = access
        self.handler = handler
        self.target = None if target is None else validate_target(**{
            "root": target["state_root"], "run_id": target["run_id"], "request_id": target["request_id"]})
        self.closed = threading.Event()
        self.server = socket.socket(socket.AF_UNIX)
        address, fd = socket_address(self.path)
        try: self.server.bind(address)
        finally:
            if fd is not None: os.close(fd)
        self.path.chmod(0o600)
        self.server.listen(4); self.server.settimeout(.1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _connections(self, request):
        if self.target is None:
            raise ValueError("explicit_run_target_required")
        requested = _payload_scope(self.target["state_root"], request.payload)
        if requested != self.target:
            raise ValueError("connection_creation_identity_mismatch")
        view = {}
        if self.handler is not None:
            response = self.handler(ControlRequest(1, "institutional-lifecycle", None, "PREPARATION_STATUS", {}))
            if response.status != "accepted": raise ValueError("preparation_lifecycle_unavailable")
            view = response.result or {}
        lifecycle = ("terminal" if view.get("state") in {"finishing", "finished"} else
            "awaiting_launch" if view.get("state") == "awaiting_launch" else "preparing")
        entries = self.access.connections() if lifecycle != "terminal" and hasattr(self.access, "connections") else []
        result = {"scope": dict(self.target), "lifecycle": lifecycle, "connections": entries}
        if isinstance(view.get("remaining_seconds"), (int, float)) and not isinstance(view["remaining_seconds"], bool):
            result["remaining_seconds"] = view["remaining_seconds"]
        return result

    def _serve(self):
        while not self.closed.is_set():
            try: peer, _ = self.server.accept()
            except TimeoutError: continue
            except OSError: return
            with peer:
                peer.settimeout(1)
                try:
                    request = ControlRequest.from_mapping(receive_message(peer))
                    if request.command == "INSTITUTIONAL_CONNECTIONS":
                        try:
                            response = ControlResponse.accepted(request.request_id, None, self._connections(request))
                        except ValueError as exc:
                            response = ControlResponse.failure(request.request_id, "rejected", "invalid_connection_scope", str(exc))
                    elif self.handler is not None:
                        try: response = self.handler(request)
                        except ValueError as exc:
                            response = ControlResponse.failure(request.request_id, "rejected", "invalid_preparation_action", str(exc))
                    else: continue
                    send_message(peer, response.to_mapping())
                except (OSError, ValueError): pass

    def close(self):
        self.closed.set(); self.server.close(); self.thread.join(timeout=2)
        self.path.unlink(missing_ok=True)
