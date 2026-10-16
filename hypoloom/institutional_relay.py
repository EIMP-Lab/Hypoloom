"""Disposable, authenticated loopback CDP transport owned by one run.

The backend is either an explicitly supplied external endpoint, or a private
Unix socket populated by SSH. Neither the port nor token is durable run state.
"""
from __future__ import annotations

from contextlib import contextmanager
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import select
import socket
import threading
from urllib.parse import urlsplit


class CDPRelay(ThreadingHTTPServer):
    daemon_threads = True
    # Rebinding after close must survive TCP TIME_WAIT; SO_REUSEPORT is never set.
    allow_reuse_address = True

    def __init__(self, dial, *, port=0, gui=False):
        self.dial, self.gui = dial, gui
        self.token = secrets.token_urlsafe(24)
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.peers = set()
        super().__init__(("127.0.0.1", port), _Handler)
        self.endpoint = f"http://127.0.0.1:{self.server_address[1]}"
        self.thread = threading.Thread(target=self.serve_forever,
            kwargs={"poll_interval": .05}, name="institutional-relay", daemon=True)
        self.thread.start()

    def get_request(self):
        peer, address = super().get_request()
        peer.settimeout(5)
        with self.lock:
            self.peers.add(peer)
        return peer, address

    def shutdown_request(self, peer):
        try:
            super().shutdown_request(peer)
        finally:
            with self.lock:
                self.peers.discard(peer)

    @contextmanager
    def backend(self):
        with self.dial() as peer:
            with self.lock:
                if self.closed.is_set(): raise OSError("relay_closed")
                self.peers.add(peer)
            try:
                yield peer
            finally:
                with self.lock:
                    self.peers.discard(peer)

    def close(self):
        if self.closed.is_set(): return
        self.closed.set()
        self.shutdown()
        with self.lock:
            for peer in self.peers:
                try: peer.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        self.server_close()
        self.thread.join(timeout=2)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def _reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        if code == 401 and self.server.gui:
            self.send_header("WWW-Authenticate", 'Basic realm="Hypoloom browser"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self): self._handle()
    def do_POST(self): self._handle()

    def _handle(self):
        relay = self.server
        started = False
        expected = "Bearer " + relay.token
        if relay.gui:
            import base64
            expected = "Basic " + base64.b64encode(("hypoloom:" + relay.token).encode()).decode()
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected.encode()):
            self._reply(401); return
        if relay.closed.is_set(): self._reply(503); return
        path = urlsplit(self.path)
        if path.scheme or path.netloc or not self.path.startswith("/"):
            self._reply(400); return
        if not relay.gui and (self.command != "GET" or path.query or
                not (path.path in {"/json/version", "/json/version/"} or path.path.startswith("/devtools/browser/"))):
            self._reply(404); return
        if self.headers.get("Transfer-Encoding"):
            self._reply(400); return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 1 << 20: raise ValueError
        except ValueError:
            self._reply(413); return
        try:
            with relay.backend() as upstream:
                if not relay.gui and path.path.rstrip("/") == "/json/version":
                    upstream.sendall(b"GET /json/version HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                    response = http.client.HTTPResponse(upstream); response.begin()
                    body = response.read((1 << 20) + 1)
                    if response.status != 200 or len(body) > 1 << 20: raise ValueError
                    document = json.loads(body)
                    websocket = urlsplit(document["webSocketDebuggerUrl"])
                    if not websocket.path.startswith("/devtools/browser/") or websocket.query: raise ValueError
                    document["webSocketDebuggerUrl"] = relay.endpoint.replace("http:", "ws:", 1) + websocket.path
                    started = True
                    self._reply(200, json.dumps(document).encode())
                    return
                headers = [f"{self.command} {self.path} HTTP/1.1", "Host: localhost"]
                headers += [f"{key}: {value}" for key, value in self.headers.items()
                            if key.lower() not in {"authorization", "host", "connection", "proxy-authorization"}]
                headers.append("Connection: Upgrade" if self.headers.get("Upgrade", "").lower() == "websocket" else "Connection: close")
                upstream.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("latin1") + self.rfile.read(size))
                self.close_connection = True
                self.wfile.flush()
                while not relay.closed.is_set():
                    ready, _, _ = select.select([self.connection, upstream], [], [], .2)
                    for peer in ready:
                        data = peer.recv(65536)
                        if not data: return
                        if peer is upstream: started = True
                        (upstream if peer is self.connection else self.connection).sendall(data)
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            if not started:
                try: self._reply(503)
                except OSError: pass


@contextmanager
def unix_backend(path):
    with socket.socket(socket.AF_UNIX) as peer:
        peer.settimeout(5)
        peer.connect(str(path))
        yield peer


@contextmanager
def tcp_backend(endpoint):
    parts = urlsplit(endpoint)
    with socket.create_connection((parts.hostname, parts.port), timeout=5) as peer:
        yield peer
