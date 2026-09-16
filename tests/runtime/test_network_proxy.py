"""Real loopback HTTP/TLS sockets behind injected public destination identity."""

import http.client
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

import pytest

import hypoloom.network_proxy as proxy_module
from hypoloom.network_proxy import AttemptNetworkProxy, ConnectedStream, NetworkBridge, PublicNetworkTransport


PUBLIC = "93.184.216.34"


class _Transport:
    def __init__(self, port, *, addresses=(PUBLIC,), peer=PUBLIC):
        self.port, self.addresses, self.peer = port, addresses, peer
        self.resolutions, self.connections = [], []

    def resolve(self, hostname, port):
        self.resolutions.append((hostname, port))
        return self.addresses

    def connect(self, hostname, port, *, approved_addresses, timeout_seconds):
        self.connections.append((hostname, port, approved_addresses))
        stream = socket.create_connection(("127.0.0.1", self.port), timeout=timeout_seconds)
        return ConnectedStream(stream, self.peer)


@contextmanager
def _upstream(*, tls=None, body=b"SCRIPTED_PUBLIC_NETWORK_NONCE", redirect=None):
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            observed.append((self.path, dict(self.headers)))
            self.send_response(302 if redirect else 200)
            if redirect:
                self.send_header("Location", redirect)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server.server_port, observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _proxy(tmp_path, transport, **kwargs):
    return AttemptNetworkProxy(tmp_path / "network.sock", transport=transport,
        **{"max_connections": 20, "max_total_bytes": 1024 * 1024,
            "deadline_at": time.monotonic() + 10, **kwargs})


def _request(path, target, method="GET", headers=None):
    connection = http.client.HTTPConnection("localhost", timeout=2)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(2)
    connection.sock.connect(str(path))
    try:
        connection.request(method, target, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_actual_urllib_http_forward_preserves_body_but_not_proxy_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "")
    with _upstream() as (port, observed):
        transport = _Transport(port)
        proxy = _proxy(tmp_path, transport)
        try:
            with NetworkBridge(str(proxy.socket_path), timeout_seconds=3) as bridge:
                opener = build_opener(ProxyHandler({"http": bridge.proxy_url}))
                with opener.open("http://example.org/data?q=scientific", timeout=2) as response:
                    assert response.read() == b"SCRIPTED_PUBLIC_NETWORK_NONCE"
            assert _request(proxy.socket_path, "http://example.org/second",
                headers={"Proxy-Authorization": "Bearer sandbox-only-sentinel"})[0] == 200
            assert len(observed) == 2
            assert observed[0][0] == "/data?q=scientific"
            assert observed[0][1]["Host"] == "example.org"
            assert all("Proxy-Authorization" not in headers for _, headers in observed)
            assert transport.connections == [("example.org", 80, (PUBLIC,))] * 2
        finally:
            proxy.close()
        assert not proxy.socket_path.exists()


@pytest.fixture
def tls_pair(tmp_path):
    executable = shutil.which("openssl")
    if executable is None:
        pytest.skip("openssl fixture executable is unavailable")
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    result = subprocess.run([executable, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=example.org", "-addext", "subjectAltName=DNS:example.org",
        "-keyout", str(key), "-out", str(cert)], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert, key)
    return server, cert


def test_actual_urllib_https_connect_keeps_certificate_verification(tmp_path, monkeypatch, tls_pair):
    monkeypatch.setenv("NO_PROXY", "")
    server_context, certificate = tls_pair
    with _upstream(tls=server_context) as (port, observed):
        transport = _Transport(port)
        proxy = _proxy(tmp_path, transport)
        try:
            with NetworkBridge(str(proxy.socket_path), timeout_seconds=3) as bridge:
                opener = build_opener(ProxyHandler({"https": bridge.proxy_url}),
                    HTTPSHandler(context=ssl.create_default_context(cafile=str(certificate))))
                with opener.open("https://example.org/tls-science", timeout=2) as response:
                    assert response.read() == b"SCRIPTED_PUBLIC_NETWORK_NONCE"
            assert observed[0][0] == "/tls-science"
            assert transport.connections == [("example.org", 443, (PUBLIC,))]
        finally:
            proxy.close()


@pytest.mark.parametrize("method,target", [
    ("GET", "http://127.0.0.1/control"), ("GET", "http://10.0.0.1/"),
    ("GET", "http://169.254.169.254/"), ("GET", "http://localhost/"),
    ("GET", "http://example.org:8080/"), ("GET", "https://example.org/"),
    ("GET", "http://secret@example.org/"), ("GET", "file:///etc/passwd"),
    ("CONNECT", "127.0.0.1:443"), ("CONNECT", "[::1]:443"),
    ("CONNECT", "example.org:22"), ("CONNECT", "example.org:443/path"),
])
def test_private_credentials_and_unsupported_destinations_never_connect(tmp_path, method, target):
    transport = _Transport(1)
    proxy = _proxy(tmp_path, transport)
    try:
        status, body = _request(proxy.socket_path, target, method)
        assert status == 403 and b"destination" in body
        assert not transport.connections
    finally:
        proxy.close()


@pytest.mark.parametrize("addresses", [("127.0.0.1",), (PUBLIC, "192.168.0.1"), (), tuple("8.8.8." + str(i) for i in range(1, 10))])
def test_all_dns_answers_must_be_bounded_and_public(tmp_path, addresses):
    transport = _Transport(1, addresses=addresses)
    proxy = _proxy(tmp_path, transport)
    try:
        assert _request(proxy.socket_path, "http://example.org/")[0] == 403
        assert not transport.connections
    finally:
        proxy.close()


@pytest.mark.parametrize("peer", ["127.0.0.1", "8.8.8.8"])
def test_connected_peer_must_be_public_and_one_of_approved_dns(tmp_path, peer):
    with _upstream() as (port, observed):
        proxy = _proxy(tmp_path, _Transport(port, peer=peer))
        try:
            assert _request(proxy.socket_path, "http://example.org/")[0] == 403
            assert observed == []
        finally:
            proxy.close()


def test_redirect_to_host_private_network_is_rechecked_by_real_client(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with _upstream(redirect="http://127.0.0.1/operator") as (port, observed):
        transport = _Transport(port)
        proxy = _proxy(tmp_path, transport)
        try:
            with NetworkBridge(str(proxy.socket_path), timeout_seconds=3) as bridge:
                opener = build_opener(ProxyHandler({"http": bridge.proxy_url}))
                from urllib.error import HTTPError
                with pytest.raises(HTTPError) as error:
                    opener.open("http://example.org/redirect", timeout=2)
                assert error.value.code == 403
            assert len(observed) == len(transport.connections) == 1
        finally:
            proxy.close()


def test_connection_and_byte_limits_are_shared_and_visible(tmp_path):
    with _upstream() as (port, observed):
        transport = _Transport(port)
        proxy = _proxy(tmp_path, transport, max_connections=1)
        try:
            assert _request(proxy.socket_path, "http://example.org/")[0] == 200
            # Connection admission rejects before reading an HTTP request. Read
            # that response directly; writing after its close can raise EPIPE.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                stream.settimeout(2)
                stream.connect(str(proxy.socket_path))
                response = http.client.HTTPResponse(stream)
                response.begin()
                assert response.status == 429
                assert response.read() == b"network proxy connection limit exhausted\n"
                assert response.will_close and stream.recv(1) == b""
            assert transport.connections == [("example.org", 80, (PUBLIC,))]
            assert len(observed) == 1 and proxy._connections == 1
        finally:
            proxy.close()
        assert not proxy.socket_path.exists() and not proxy._sockets
        proxy = _proxy(tmp_path, _Transport(port), max_total_bytes=1)
        try:
            status, body = _request(proxy.socket_path, "http://example.org/")
            assert status == 429 and body == b"network proxy byte limit exhausted\n"
        finally:
            proxy.close()
        assert not proxy.socket_path.exists() and not proxy._sockets


def test_bridge_preserves_early_rejection_when_request_write_hits_closed_endpoint(tmp_path, monkeypatch):
    with _upstream() as (port, observed):
        transport = _Transport(port)
        proxy = _proxy(tmp_path, transport, max_connections=1)
        try:
            assert _request(proxy.socket_path, "http://example.org/")[0] == 200
            closed = threading.Event()
            refused = []
            verify, shutdown = proxy.server.verify_request, proxy.server.shutdown_request

            def verify_request(stream, address):
                admitted = verify(stream, address)
                if not admitted:
                    refused.append(stream)
                return admitted

            def shutdown_request(stream):
                shutdown(stream)
                if stream in refused:
                    closed.set()

            monkeypatch.setattr(proxy.server, "verify_request", verify_request)
            monkeypatch.setattr(proxy.server, "shutdown_request", shutdown_request)
            pump = proxy_module._pump
            witnessed = []

            def pump_after_refusal(left, right, **options):
                if left.family == socket.AF_INET and right.family == socket.AF_UNIX:
                    # Schedule the real bridge after admission has sent and
                    # closed, with both its request and refusal ready to copy.
                    assert closed.wait(2)
                    assert select.select((left,), (), (), 2)[0] == [left]
                    assert select.select((right,), (), (), 2)[0] == [right]
                    reply = right.recv(4096, socket.MSG_PEEK)
                    assert reply.startswith(b"HTTP/1.1 429 ")
                    assert reply.endswith(b"network proxy connection limit exhausted\n")
                    witnessed.append(reply)
                return pump(left, right, **options)

            monkeypatch.setattr(proxy_module, "_pump", pump_after_refusal)
            with NetworkBridge(str(proxy.socket_path), timeout_seconds=2) as bridge:
                with socket.create_connection(("127.0.0.1", bridge.server.server_address[1]), timeout=2) as stream:
                    try:
                        stream.sendall(b"GET http://example.org/ HTTP/1.1\r\nHost: example.org\r\n\r\n")
                        response = http.client.HTTPResponse(stream)
                        response.begin()
                        assert response.status == 429
                        assert response.read() == b"network proxy connection limit exhausted\n"
                        assert response.will_close and stream.recv(1) == b""
                    finally:
                        assert len(witnessed) == 1
                        assert transport.connections == [("example.org", 80, (PUBLIC,))]
                        assert len(observed) == 1 and proxy._connections == 1
            assert not bridge._sockets
        finally:
            proxy.close()
        assert not proxy.socket_path.exists() and not proxy._sockets


def test_expired_endpoint_rejects_before_connect_and_close_does_not_replace_paths(tmp_path):
    transport = _Transport(1)
    proxy = _proxy(tmp_path, transport, deadline_at=time.monotonic() - 1)
    try:
        assert _request(proxy.socket_path, "http://example.org/")[0] == 504
        assert not transport.connections
        with pytest.raises(FileExistsError):
            _proxy(tmp_path, transport)
    finally:
        proxy.close()
        proxy.close()
    replacement = tmp_path / "network.sock"
    replacement.write_text("unrelated user file")
    with pytest.raises(FileExistsError):
        _proxy(tmp_path, transport)
    assert replacement.read_text() == "unrelated user file"


@contextmanager
def _holding_upstream():
    release = threading.Event()
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            release.wait(2)
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("reason", ["idle", "deadline", "close", "bytes"])
def test_live_connect_tunnel_is_bounded_and_closed_without_socket_residue(tmp_path, reason):
    with _holding_upstream() as port:
        options = {"idle_timeout_seconds": 0.1} if reason == "idle" else (
            {"deadline_at": time.monotonic() + 0.2} if reason == "deadline" else (
            {"max_total_bytes": 3} if reason == "bytes" else {}))
        proxy = _proxy(tmp_path, _Transport(port), **options)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        client.connect(str(proxy.socket_path))
        try:
            client.sendall(b"CONNECT example.org:443 HTTP/1.1\r\nHost: example.org:443\r\n\r\n")
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                header += client.recv(1)
            assert b"200 Connection Established" in header
            if reason == "close":
                proxy.close()
            if reason == "bytes":
                client.sendall(b"greater-than-three-byte-budget")
            assert client.recv(1) == b""
        finally:
            client.close()
            proxy.close()
        assert not proxy.socket_path.exists()
        assert not proxy._sockets


def test_default_public_connector_uses_only_preapproved_ip(monkeypatch):
    calls = []
    class PeerSocket:
        def getpeername(self):
            return (PUBLIC, 443)
    def connect(address, timeout):
        calls.append((address, timeout))
        return PeerSocket()
    monkeypatch.setattr(socket, "create_connection", connect)
    result = PublicNetworkTransport().connect("never-resolve-again.invalid", 443,
        approved_addresses=(PUBLIC,), timeout_seconds=1)
    assert calls[0][0] == (PUBLIC, 443)
    assert result.peer_address == PUBLIC


@pytest.mark.parametrize("enabled", [False, True])
def test_provider_main_only_passes_proxy_environment_for_explicit_network_socket(tmp_path, monkeypatch, enabled):
    from hypoloom import provider_bridge
    result = {}
    command = ["bridge", "--socket", str(tmp_path / "provider.sock")]
    if enabled:
        command += ["--network-socket", str(tmp_path / "network.sock")]
    monkeypatch.setattr(sys, "argv", command + ["--", "unused-child"])
    monkeypatch.setenv("ALL_PROXY", "http://ambient-host-proxy.invalid")
    monkeypatch.setenv("https_proxy", "http://ambient-host-secret.invalid")
    def run(command, *, env, check):
        result.update(env)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, "run", run)
    assert provider_bridge.main() == 0
    assert "ALL_PROXY" not in result
    if enabled:
        assert result["https_proxy"].startswith("http://127.0.0.1:")
        assert result["HTTPS_PROXY"] == result["https_proxy"] == result["HTTP_PROXY"] == result["http_proxy"]
        assert result["NO_PROXY"] == "127.0.0.1,localhost,::1"
    else:
        assert not any(name.lower().endswith("_proxy") for name in result)
