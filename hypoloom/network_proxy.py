"""Per-Attempt public HTTP proxy; standalone sandbox bridge is stdlib-only.

The Host supplies limits and owns the Unix endpoint. No provider authentication,
workflow state or ambient proxy configuration is consumed here.
"""
from __future__ import annotations

import ipaddress
import math
import select
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class ConnectedStream:
    socket: socket.socket
    peer_address: str


class PublicNetworkTransport:
    def resolve(self, hostname, port):
        return tuple(sorted({item[4][0] for item in socket.getaddrinfo(
            hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)}))

    def connect(self, hostname, port, *, approved_addresses, timeout_seconds):
        deadline = time.monotonic() + timeout_seconds
        error = None
        for address in approved_addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                stream = socket.create_connection((address, port), timeout=remaining)
                return ConnectedStream(stream, str(stream.getpeername()[0]))
            except OSError as exc:
                error = exc
        raise OSError("public connection failed") from error


class _Rejected(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def _error(stream, status, reason):
    body = (reason + "\n").encode("ascii")
    response = (f"HTTP/1.1 {status} Proxy Error\r\nContent-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
    try:
        stream.sendall(response)
    except OSError:
        pass


def _close_socket(stream):
    try:
        stream.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    stream.close()


def _pump(left, right, *, deadline, idle_seconds, consume=lambda size: None):
    """Bounded full duplex copying; no protocol or TLS interception."""
    readers = {left: right, right: left}
    idle_at = time.monotonic() + idle_seconds
    while readers:
        remaining = min(deadline, idle_at) - time.monotonic()
        if remaining <= 0:
            raise _Rejected(504, "network proxy deadline or idle timeout")
        readable, _, _ = select.select(tuple(readers), (), (), remaining)
        if not readable:
            raise _Rejected(504, "network proxy deadline or idle timeout")
        for source in readable:
            chunk = source.recv(65536)
            target = readers[source]
            if not chunk:
                del readers[source]
                try:
                    target.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            consume(len(chunk))
            target.settimeout(min(idle_seconds, max(0.001, deadline - time.monotonic())))
            try:
                target.sendall(chunk)
            except BrokenPipeError:
                # A peer can close its request side after sending a refusal.
                # Stop this direction, but drain its reverse response to EOF.
                del readers[source]
                continue
            idle_at = time.monotonic() + idle_seconds


class _ThreadedUnix(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False


class _ThreadedTCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False


class AttemptNetworkProxy:
    """Public HTTP:80 and opaque CONNECT:443, with shared finite limits."""

    def __init__(self, socket_path: Path, *, max_connections: int, max_total_bytes: int,
                 deadline_at: float, connect_timeout_seconds: float = 15,
                 idle_timeout_seconds: float = 15, transport=None):
        for value in (max_connections, max_total_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("network proxy limits must be positive integers")
        if isinstance(deadline_at, bool) or not isinstance(deadline_at, (float, int)) or not math.isfinite(deadline_at):
            raise ValueError("network proxy requires a finite monotonic deadline")
        for value in (connect_timeout_seconds, idle_timeout_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 15:
                raise ValueError("network proxy connect/idle limits must be within (0, 15] seconds")
        self.socket_path = Path(socket_path)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise FileExistsError(self.socket_path)
        self.transport = transport or PublicNetworkTransport()
        self.max_connections, self.max_total_bytes = max_connections, max_total_bytes
        self.deadline_at = float(deadline_at)
        self.connect_timeout_seconds, self.idle_timeout_seconds = connect_timeout_seconds, idle_timeout_seconds
        self._lock, self._closed = threading.Lock(), threading.Event()
        self._connections = self._bytes = 0
        self._sockets = set()
        proxy = self

        class Server(_ThreadedUnix):
            def verify_request(self, request, address):
                try:
                    with proxy._lock:
                        proxy._check()
                        if proxy._connections >= proxy.max_connections:
                            raise _Rejected(429, "network proxy connection limit exhausted")
                        proxy._connections += 1
                        proxy._sockets.add(request)
                    return True
                except _Rejected as exc:
                    _error(request, exc.status, exc.reason)
                    return False

            def handle_error(self, request, address):
                return

        class Handler(BaseHTTPRequestHandler):
            rbufsize = 0  # CONNECT bytes must not be hidden in a read-ahead buffer.

            def setup(self):
                self.request.settimeout(min(proxy.idle_timeout_seconds, max(0.001, proxy.deadline_at - time.monotonic())))
                super().setup()

            def log_message(self, *args):
                return

            def finish(self):
                try:
                    super().finish()
                finally:
                    with proxy._lock:
                        proxy._sockets.discard(self.request)

            def _forward(self):
                started, upstream = False, None
                try:
                    host, port, path = proxy._destination(self.command, self.path)
                    upstream = proxy._connect(host, port)
                    if self.command == "CONNECT":
                        self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    else:
                        connection_fields = {value.strip().lower() for value in self.headers.get("Connection", "").split(",")}
                        excluded = {"host", "proxy-authorization", "proxy-connection", "connection", "keep-alive", "upgrade"} | connection_fields
                        # HTTP is forwarded to one pinned public peer, with the
                        # original framing and a connection-close response.
                        fields = [(name, value) for name, value in self.headers.items() if name.lower() not in excluded]
                        fields.extend((("Host", "[" + host + "]" if ":" in host else host), ("Connection", "close")))
                        header = (f"{self.command} {path} HTTP/1.1\r\n" + "".join(f"{name}: {value}\r\n" for name, value in fields) + "\r\n").encode("latin-1")
                        proxy._consume(len(header))
                        upstream.sendall(header)
                    started = True
                    _pump(self.request, upstream, deadline=proxy.deadline_at,
                        idle_seconds=proxy.idle_timeout_seconds, consume=proxy._consume)
                except _Rejected as exc:
                    if not started:
                        _error(self.request, exc.status, exc.reason)
                except (OSError, ValueError, TypeError):
                    if not started:
                        _error(self.request, 502, "network proxy connection unavailable")
                finally:
                    self.close_connection = True
                    if upstream is not None:
                        with proxy._lock:
                            proxy._sockets.discard(upstream)
                        _close_socket(upstream)

            do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_CONNECT = _forward

        self.server = Server(str(self.socket_path), Handler)
        self.socket_path.chmod(0o600)
        self._identity = self.socket_path.stat()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        self._timer = threading.Thread(target=self._expire, daemon=True)
        self._timer.start()

    def _check(self):
        if self._closed.is_set() or time.monotonic() >= self.deadline_at:
            raise _Rejected(504, "network proxy deadline expired or endpoint closed")
        if self._bytes >= self.max_total_bytes:
            raise _Rejected(429, "network proxy byte limit exhausted")

    def _consume(self, size):
        with self._lock:
            self._check()
            if size > self.max_total_bytes - self._bytes:
                self._bytes = self.max_total_bytes
                raise _Rejected(429, "network proxy byte limit exhausted")
            self._bytes += size

    def _destination(self, method, value):
        from .literature import _uri_parts
        try:
            parsed = urlsplit("//" + value if method == "CONNECT" else value)
            if method == "CONNECT":
                if parsed.port != 443 or parsed.path or parsed.query:
                    raise ValueError("CONNECT requires port 443")
                port = 443
            else:
                if parsed.scheme != "http" or parsed.port not in {None, 80}:
                    raise ValueError("HTTP forwarding requires port 80")
                port = 80
            if parsed.username is not None or parsed.password is not None or parsed.fragment or not parsed.hostname:
                raise ValueError("destination must not contain credentials or fragments")
            host = parsed.hostname
            authority = "[" + host + "]" if ":" in host else host
            _, host = _uri_parts("https://" + authority, "network proxy destination")
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            return host, port, path
        except ValueError as exc:
            raise _Rejected(403, "network proxy destination must be public HTTP:80 or CONNECT:443") from exc

    def _connect(self, host, port):
        from .literature import _public_address
        self._check()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        try:
            values = (str(literal),) if literal is not None else self.transport.resolve(host, port)
            if isinstance(values, (str, bytes)):
                raise ValueError("invalid resolver result")
            addresses = tuple(sorted({_public_address(value, "network DNS address") for value in values}))
            if not 1 <= len(addresses) <= 8:
                raise ValueError("public DNS needs 1..8 addresses")
        except ValueError as exc:
            raise _Rejected(403, "network proxy DNS destination is not public") from exc
        self._check()
        result = self.transport.connect(host, port, approved_addresses=addresses,
            timeout_seconds=min(self.connect_timeout_seconds, self.deadline_at - time.monotonic()))
        if not isinstance(result, ConnectedStream):
            raise ValueError("invalid network transport connection")
        try:
            if _public_address(result.peer_address, "network peer") not in addresses:
                raise ValueError("connected peer was not approved")
            with self._lock:
                self._check()
                self._sockets.add(result.socket)
            return result.socket
        except (ValueError, _Rejected) as exc:
            _close_socket(result.socket)
            if isinstance(exc, _Rejected):
                raise
            raise _Rejected(403, "network proxy connected peer is not DNS-approved") from exc

    def _expire(self):
        if not self._closed.wait(max(0, self.deadline_at - time.monotonic())):
            self._close_connections()

    def _close_connections(self):
        with self._lock:
            streams = tuple(self._sockets)
            self._sockets.clear()
        for stream in streams:
            _close_socket(stream)

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self._close_connections()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self._timer.join(timeout=2)
        try:
            current = self.socket_path.lstat()
            if (current.st_dev, current.st_ino) == (self._identity.st_dev, self._identity.st_ino):
                self.socket_path.unlink()
        except FileNotFoundError:
            pass


class NetworkBridge:
    """Sandbox TCP endpoint to one mounted UDS; no destination selection."""

    def __init__(self, socket_path: str, *, timeout_seconds: float = 600):
        self.socket_path, self.timeout_seconds = socket_path, timeout_seconds
        self._lock, self._sockets = threading.Lock(), set()
        bridge = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                upstream.settimeout(bridge.timeout_seconds)
                with bridge._lock:
                    bridge._sockets.update((self.request, upstream))
                try:
                    upstream.connect(bridge.socket_path)
                    # The Host endpoint owns the actual Attempt deadline. This
                    # bridge only has an I/O idle timeout, not a second fixed
                    # total-duration ceiling on an otherwise active transfer.
                    _pump(self.request, upstream, deadline=float("inf"),
                        idle_seconds=bridge.timeout_seconds)
                except (OSError, _Rejected):
                    pass
                finally:
                    with bridge._lock:
                        bridge._sockets.difference_update((self.request, upstream))
                    _close_socket(upstream)

        self.server = _ThreadedTCP(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)

    @property
    def proxy_url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        with self._lock:
            streams = tuple(self._sockets)
            self._sockets.clear()
        for stream in streams:
            _close_socket(stream)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
