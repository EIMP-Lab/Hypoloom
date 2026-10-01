"""Private control discovery and SSH lifetime; no run creation or state writes."""
from __future__ import annotations

import json
import http.client
import os
from pathlib import Path
import socket
import stat
import threading
import time
from urllib.parse import urlsplit

from .control_protocol import ControlRequest, ControlResponse, receive_message, request_over_socket, send_message, socket_address


def connections(root):
    root = Path(root).resolve()
    info = root.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("connection_root_must_be_private")
    path = root / "control.sock"
    if not stat.S_ISSOCK(path.stat().st_mode): return []
    response = request_over_socket(path, ControlRequest(1, "institutional-connections", None,
        "INSTITUTIONAL_CONNECTIONS", {}), timeout=2)
    if response.status != "accepted": return []
    return [{"state_root": str(root), **item} for item in response.result.get("connections", [])]


def run(args, *, output=print):
    if args.institutional_action == "forward-ready":
        return 0 if prepare_forward(args.state_root, args.run_id, args.socket_path) else 2
    if args.institutional_action == "watch":
        # The containing SSH session owns -R. Returning closes that session and
        # its forwarding on terminal state, Host loss or a new Host instance.
        checked = 0
        while True:
            try:
                current = connections(args.state_root)
                if not any(c["run_id"] == args.run_id and c.get("socket_path") == args.socket_path for c in current): return 0
                if time.monotonic() - checked > 5:
                    from .institutional_relay import unix_backend
                    with unix_backend(args.socket_path) as peer:
                        peer.sendall(b"GET /json/version HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                        response = http.client.HTTPResponse(peer); response.begin()
                        raw = response.read((1 << 20) + 1)
                        if response.status != 200 or len(raw) > 1 << 20: return 2
                        identity = urlsplit(json.loads(raw)["webSocketDebuggerUrl"]).path
                        if identity != args.browser_path: return 2
                    checked = time.monotonic()
            except (OSError, ValueError, KeyError, http.client.HTTPException): return 2
            time.sleep(.5)
    roots = [Path(args.state_root)] if args.state_root else (sorted(Path(args.runs_root).iterdir()) if Path(args.runs_root).exists() else [])
    selected = []
    for root in roots:
        if root.is_symlink() or not (root / "control.sock").exists(): continue
        try:
            for item in connections(root):
                if not args.include_ui_credentials:
                    item.pop("browser_ui_password", None)
                selected.append(item)
        except (OSError, ValueError): continue
    output(json.dumps({"connections": selected}, ensure_ascii=False))
    return 0


def prepare_forward(root, run_id, path):
    """OpenSSH leaves dead stream-local pathnames; remove only this live run's
    refused socket before a new forward. An existing listener is never adopted.
    """
    try:
        if not any(c["run_id"] == run_id and c.get("socket_path") == path for c in connections(root)): return False
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
    def __init__(self, root, access):
        self.path = Path(root) / "control.sock"
        self.access = access
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

    def _serve(self):
        while not self.closed.is_set():
            try: peer, _ = self.server.accept()
            except TimeoutError: continue
            except OSError: return
            with peer:
                peer.settimeout(1)
                try:
                    request = ControlRequest.from_mapping(receive_message(peer))
                    if request.command != "INSTITUTIONAL_CONNECTIONS" or request.payload: continue
                    send_message(peer, ControlResponse.accepted(request.request_id, None,
                        {"connections": self.access.connections()}).to_mapping())
                except (OSError, ValueError): pass

    def close(self):
        self.closed.set(); self.server.close(); self.thread.join(timeout=2)
        self.path.unlink(missing_ok=True)
