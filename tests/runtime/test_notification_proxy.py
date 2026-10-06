"""Explicit notification proxies: local CONNECT/TLS only, never external traffic."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import io
import ipaddress
import json
from pathlib import Path
import select
import socket
import socketserver
import ssl
import subprocess
import sys
import threading

import pytest

from hypoloom import notification_tailer as notifier
from hypoloom.observer_services import ObserverServices
from tests.runtime.test_network_proxy import tls_pair as tls_pair


@pytest.fixture(autouse=True)
def local_connections_only(monkeypatch):
    """Fail before DNS/socket work if a tested fallback tries an external host."""
    original = socket.create_connection
    calls = []

    def connect(address, *args, **kwargs):
        calls.append(address)
        try:
            permitted = ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            permitted = address[0] == "localhost"
        assert permitted, "notification test attempted a non-loopback connection"
        return original(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", connect)
    return calls


@contextmanager
def _receiver(*, tls=None, status=200, body=b'{"ok":true}'):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append((self.path, dict(self.headers),
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever,
        kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_port, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _connect_proxy(upstream_port=None, *, status=200):
    requests = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = self.rfile.readline(8192)
            headers = []
            while line := self.rfile.readline(8192):
                if line == b"\r\n":
                    break
                headers.append(line)
            requests.append((request, headers))
            if status != 200:
                self.wfile.write(f"HTTP/1.1 {status} PROXY_PRIVATE_RESPONSE\r\nContent-Length: 0\r\n\r\n".encode())
                self.wfile.flush()
                return
            with socket.create_connection(("127.0.0.1", upstream_port), timeout=2) as upstream:
                self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self.wfile.flush()
                try:
                    while True:
                        readable, _, _ = select.select([self.connection, upstream], [], [], 3)
                        if not readable:
                            return
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            destination = upstream if source is self.connection else self.connection
                            destination.sendall(data)
                except OSError:
                    return  # Expected for rejected TLS certificates.

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever,
        kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _mock_notifications(monkeypatch, *, webhook=None):
    sink = "webhook" if webhook else "telegram"
    config = {"sinks": {sink: {"enabled": True, **(
        {"url_env": "TEST_WEBHOOK"} if webhook else
        {"bot_token_env": "TEST_BOT", "chat_id_env": "TEST_CHAT"})}},
        "events": {sink: ["run_started"]}}
    event = {"event_id": "fact:goal:1", "source": "fact", "source_id": "goal",
        "revision": 1, "type": "run_started", "run_id": "notification-TEST", "payload": {}}
    monkeypatch.setattr(notifier, "load_notification_config", lambda *_: config)
    monkeypatch.setattr(notifier, "build_product_observation", lambda *_: {"events": [event]})
    environment = {"TEST_BOT": "123:FAKE_TEST_TOKEN", "TEST_CHAT": "FAKE_TEST_CHAT"}
    if webhook:
        environment["TEST_WEBHOOK"] = webhook
    return config, environment


def _tailer(tmp_path, **kwargs):
    return notifier.NotificationTailer(tmp_path / "state", "notification-TEST",
        tmp_path / "delivery", **kwargs)


def test_actual_connect_verified_tls_then_json_post(tls_pair, monkeypatch):
    context, certificate = tls_pair
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    with _receiver(tls=context) as (port, received), _connect_proxy(port) as (proxy, tunnels):
        response = notifier.post_json("https://example.org/send?test=1", {"text": "TEST only"},
            headers={"X-Test": "local-only"}, timeout=2.5, proxy_url=proxy)
    assert response == notifier.HTTPResponse(200, b'{"ok":true}')
    assert len(tunnels) == 1 and tunnels[0][0].split()[:2] == [b"CONNECT", b"example.org:443"]
    assert len(received) == 1
    path, headers, body = received[0]
    assert (path, body) == ("/send?test=1", {"text": "TEST only"})
    assert headers["Host"] == "example.org" and headers["X-Test"] == "local-only"
    assert "Proxy-Authorization" not in headers


@pytest.mark.parametrize("failure", ["unknown-ca", "wrong-hostname"])
def test_connect_never_disables_ca_or_hostname_verification(tls_pair, monkeypatch, failure):
    context, certificate = tls_pair
    if failure == "wrong-hostname":
        monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    else:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    host = "different.example.org" if failure == "wrong-hostname" else "example.org"
    with _receiver(tls=context) as (port, received), _connect_proxy(port) as (proxy, tunnels):
        with pytest.raises(notifier.NotificationDeliveryError) as failure:
            notifier.post_json(f"https://{host}/TEST", {}, headers={}, timeout=2, proxy_url=proxy)
        assert failure.value.details["code"] == "tls_certificate_failed"
        assert failure.value.details["retryable"] is False
    assert len(tunnels) == 1 and received == []


def test_proxy_failure_has_no_direct_fallback_or_credential_diagnostic(tmp_path, monkeypatch,
        local_connections_only):
    _, environment = _mock_notifications(monkeypatch)
    with _connect_proxy(status=502) as (proxy, tunnels):
        environment["SELECTED_PROXY"] = proxy
        result = _tailer(tmp_path, environ=environment, proxy_env="SELECTED_PROXY").poll_once()
    assert result.delivered == {"telegram": 0} and len(result.errors) == 1
    assert len(tunnels) == 1 and len(local_connections_only) == 1
    assert not (tmp_path / "delivery/notification-TEST/cursors/telegram.json").exists()
    diagnostic = str(result)
    assert all(value not in diagnostic for value in
        (proxy, "PROXY_PRIVATE_RESPONSE", "FAKE_TEST_TOKEN", "FAKE_TEST_CHAT", "api.telegram.org"))


def test_loopback_http_webhook_is_direct_even_with_explicit_proxy():
    with _receiver() as (port, received), _connect_proxy(status=502) as (proxy, tunnels):
        response = notifier.post_json(f"http://127.0.0.1:{port}/TEST", {"local": True},
            headers={}, timeout=2, proxy_url=proxy)
    assert response.status == 200 and len(received) == 1 and tunnels == []


@pytest.mark.parametrize("url", [
    "https://proxy.invalid", "socks5://proxy.invalid:1080", "http://", "http://proxy.invalid:99999",
    "http://user:FAKE_SECRET@proxy.invalid", "http://proxy.invalid/private-path",
    "http://proxy.invalid?FAKE_SECRET=1", "http://proxy.invalid#FAKE_SECRET",
    " http://proxy.invalid", "http://proxy.invalid\n", "http://proxy.invalid/\t",
    "http://proxy.invalid/\x7f",
])
def test_invalid_explicit_proxy_fails_safely_before_network(url, local_connections_only):
    with pytest.raises(notifier.NotificationError) as error:
        notifier.post_json("https://example.org/TEST", {}, headers={}, timeout=2, proxy_url=url)
    assert url not in str(error.value) and "FAKE_SECRET" not in str(error.value)
    assert local_connections_only == []


@pytest.mark.parametrize("proxy_env,value", [
    ("MISSING_PROXY", None), ("SELECTED_PROXY", ""), ("BAD-NAME", "http://localhost:80"),
    ("SELECTED_PROXY", "http://private:FAKE_SECRET@proxy.invalid"),
])
def test_selected_proxy_env_is_required_validated_and_never_falls_back(tmp_path, monkeypatch,
        proxy_env, value, local_connections_only):
    _, environment = _mock_notifications(monkeypatch)
    environment["HTTPS_PROXY"] = "http://127.0.0.1:9"
    if value is not None:
        environment[proxy_env] = value
    with pytest.raises(notifier.NotificationError) as error:
        _tailer(tmp_path, environ=environment, proxy_env=proxy_env)
    assert "FAKE_SECRET" not in str(error.value) and "proxy.invalid" not in str(error.value)
    assert local_connections_only == []


def test_explicit_env_uses_tailer_mapping_and_cursor_prevents_second_post(tmp_path, monkeypatch):
    _, environment = _mock_notifications(monkeypatch)
    environment["SELECTED_PROXY"] = "http://localhost:8080/"
    monkeypatch.setenv("SELECTED_PROXY", "http://localhost:9999")
    calls = []

    def transport(url, document, *, headers, timeout, proxy_url):
        calls.append((proxy_url, timeout))
        return notifier.HTTPResponse(200, b'{"ok":true}')

    tailer = _tailer(tmp_path, environ=environment, proxy_env="SELECTED_PROXY", transport=transport)
    first = tailer.poll_once()
    second = _tailer(tmp_path, environ=environment, proxy_env="SELECTED_PROXY", transport=transport).poll_once()
    assert not first.errors and first.delivered == {"telegram": 1}
    assert not second.errors and second.delivered == {"telegram": 0}
    assert calls == [(environment["SELECTED_PROXY"], 10.0)]
    assert first.health['telegram']['route'] == 'http_proxy'
    assert 'localhost' not in json.dumps(first.health)
    environment['SELECTED_PROXY'] = 'http://localhost:9998'
    tailer.retry()
    assert tailer.health()['telegram']['route'] == 'http_proxy'
    assert tailer.proxy_url == calls[0][0], 'retry retains the route actually bound to this sender'


def test_no_proxy_preserves_old_custom_transport_signature(tmp_path, monkeypatch):
    _, environment = _mock_notifications(monkeypatch)
    environment["HTTPS_PROXY"] = "http://127.0.0.1:9"
    calls = []

    def old_transport(url, document, *, headers, timeout):
        calls.append(timeout)
        return notifier.HTTPResponse(200, b'{"ok":true}')

    result = _tailer(tmp_path, environ=environment, transport=old_transport).poll_once()
    assert result.delivered == {"telegram": 1} and not result.errors and calls == [10.0]
    assert result.health['telegram']['route'] == 'direct'


def test_post_default_ignores_all_ambient_proxies(monkeypatch):
    calls = []

    class Connection:
        def __init__(self, host, port=None, *, timeout):
            calls.append((host, port, timeout))

        def request(self, *args, **kwargs):
            pass

        def connect(self):
            pass

        def getresponse(self):
            return type("Response", (), {"status": 200, "read": lambda *_: b'{"ok":true}', "getheader": lambda *_: None})()

        def close(self):
            pass

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    notifier.post_json("https://example.org/TEST", {}, headers={}, timeout=2.25)
    assert calls == [("example.org", None, 2.25)]


@pytest.mark.parametrize("proxy", ["http://proxy.invalid", "http://proxy.invalid/"])
def test_proxy_default_port_and_origin_port_preserve_explicit_timeout(monkeypatch, proxy):
    calls = []

    class Connection:
        def __init__(self, host, port=None, *, timeout):
            calls.append(("connect", host, port, timeout))

        def set_tunnel(self, host, port=None, headers=None):
            calls.append(("tunnel", host, port))

        def connect(self):
            pass

        def request(self, method, path, **kwargs):
            calls.append((method, path))

        def getresponse(self):
            return type("Response", (), {"status": 200, "read": lambda *_: b'{"ok":true}', "getheader": lambda *_: None})()

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    response = notifier.post_json("https://example.org:4443/TEST", {},
        headers={}, timeout=1.75, proxy_url=proxy)
    assert response.status == 200
    assert calls == [("connect", "proxy.invalid", 80, 1.75),
        ("tunnel", "example.org", 4443), ("POST", "/TEST"), ("close",)]


def test_observer_forwards_selected_proxy_and_keeps_shutdown_timeout(tmp_path, monkeypatch):
    from types import SimpleNamespace
    config, environment = _mock_notifications(monkeypatch)
    monkeypatch.setattr("hypoloom.observer_services.ObserverRefresh", lambda *args, **kwargs:
        SimpleNamespace(get=lambda **options: {"activity": notifier.build_product_observation(*args)}))
    environment["SELECTED_PROXY"] = "http://localhost:8080"
    calls = []

    def transport(url, document, *, headers, timeout, proxy_url):
        calls.append((proxy_url, timeout))
        return notifier.HTTPResponse(200, b'{"ok":true}')

    services = ObserverServices(tmp_path / "state", output_root=tmp_path / "delivery",
        environ=environment, transport=transport, proxy_env="SELECTED_PROXY",
        output=io.StringIO(), errors=io.StringIO())
    monkeypatch.setattr("hypoloom.observer_services.time.monotonic", lambda: 100.0)
    services._shutdown_deadline = 101.25
    services._notify("notification-TEST", config["sinks"])
    assert calls == [(environment["SELECTED_PROXY"], 1.25)]
    services._shutdown_deadline = 100.0
    with pytest.raises(notifier.NotificationError, match="shutdown"):
        services._post("https://example.org/TEST", {}, headers={}, timeout=10,
            proxy_url=environment["SELECTED_PROXY"])
    assert len(calls) == 1 and not services.running and not (tmp_path / "state").exists()


def test_observer_without_proxy_keeps_old_transport_signature(tmp_path):
    calls = []

    def old_transport(url, document, *, headers, timeout):
        calls.append(timeout)
        return notifier.HTTPResponse(200, b'{"ok":true}')

    services = ObserverServices(tmp_path / "state", transport=old_transport)
    assert services._post("https://example.org/TEST", {}, headers={}, timeout=2).status == 200
    assert calls == [2]


def test_cli_notify_passes_only_proxy_env_name(tmp_path, monkeypatch):
    from hypoloom import cli
    observed = []
    monkeypatch.setattr(notifier, "main", lambda args: observed.extend(args) or 17)
    assert cli.main(["notify", "--state-root", str(tmp_path / "state"), "--run-id", "notification-TEST",
        "--output-root", str(tmp_path / "delivery"), "--proxy-env", "SELECTED_PROXY", "--once"]) == 17
    assert observed[observed.index("--proxy-env") + 1] == "SELECTED_PROXY" and "--once" in observed
    assert not (tmp_path / "state").exists()


def test_notifier_cli_constructs_selected_proxy_tailer(tmp_path, monkeypatch):
    observed = []

    class Tailer:
        def __init__(self, *args, **kwargs):
            observed.append(kwargs)

        def poll_once(self):
            return notifier.DeliveryReport()

    monkeypatch.setattr(notifier, "NotificationTailer", Tailer)
    assert notifier.main(["--state-root", str(tmp_path / "state"), "--run-id", "notification-TEST",
        "--output-root", str(tmp_path / "delivery"), "--proxy-env", "SELECTED_PROXY", "--once"]) == 0
    assert observed[0]["proxy_env"] == "SELECTED_PROXY"


def test_cli_serve_defers_proxy_services_until_host_lifetime_factory(tmp_path, monkeypatch):
    from hypoloom import cli
    observed, constructed = {}, []

    def serve(root, **options):
        observed.update(options)
        return 23

    def services(root, **options):
        constructed.append(options)
        return object()

    monkeypatch.setattr("hypoloom.workflow_host.serve", serve)
    monkeypatch.setattr("hypoloom.observer_services.ObserverServices", services)
    assert cli.main(["serve", "--state-root", str(tmp_path / "state"),
        "--notification-proxy-env", "SELECTED_PROXY"]) == 23
    assert constructed == []
    observed["services_factory"]()
    assert constructed[0]["proxy_env"] == "SELECTED_PROXY"
    assert not (tmp_path / "state").exists()


def test_actual_cli_proxy_tls_restart_cursor_keeps_new_run_readonly(tmp_path, tls_pair):
    """Notification process/TLS/cursor boundary, not an Agent or G3/G4/G5 run."""
    from tests.runtime.test_notification_tailer import _create_profile_run
    from tests.runtime.test_web_observer import _row_census

    context, certificate = tls_pair
    config = {"sinks": {"webhook": {"enabled": True, "url_env": "TEST_WEBHOOK_URL"}},
        "events": {"webhook": ["run_started"]}}
    state_root = _create_profile_run(tmp_path, config)
    output_root = tmp_path / "delivery"

    def source_snapshot():
        return _row_census(state_root / "state.sqlite"), {
            str(path.relative_to(state_root)): path.read_bytes()
            for path in state_root.rglob("*") if path.is_file()}

    before = source_snapshot()
    assert before[0]["execution_attempts"] == 0
    assert notifier.load_notification_config(state_root, "notifications") == config
    # Execute the real CLI in a fresh interpreter, with only a test-side network
    # deny rule. A missing/broken proxy must never fall back to public DNS/TCP.
    local_only_cli = """
import ipaddress, runpy, socket
original = socket.create_connection
def local_only(address, *args, **kwargs):
    try:
        permitted = ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        permitted = address[0] == 'localhost'
    if not permitted:
        raise OSError('TEST denied non-loopback connection')
    return original(address, *args, **kwargs)
socket.create_connection = local_only
runpy.run_module('hypoloom.cli', run_name='__main__')
"""
    command = [sys.executable, "-c", local_only_cli, "notify", "--state-root", str(state_root),
        "--run-id", "notifications", "--output-root", str(output_root),
        "--proxy-env", "SELECTED_PROXY", "--once"]
    project = Path(__file__).resolve().parents[2]
    with _receiver(tls=context) as (port, received), _connect_proxy(port) as (proxy, tunnels):
        environment = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "PYTHONPATH": str(project), "PYTHONDONTWRITEBYTECODE": "1",
            "SSL_CERT_FILE": str(certificate), "SELECTED_PROXY": proxy,
            "TEST_WEBHOOK_URL": "https://example.org/notification-TEST"}
        first = subprocess.run(command, cwd=project, env=environment,
            capture_output=True, text=True, timeout=20)
        assert first.returncode == 0, first.stdout + first.stderr
        first_report = json.loads(first.stdout)
        assert first_report["delivered"] == {"webhook": 1} and first_report["errors"] == []
        assert len(received) == len(tunnels) == 1
        cursor = output_root / "notifications/cursors/webhook.json"
        cursor_bytes = cursor.read_bytes()
        second = subprocess.run(command, cwd=project, env=environment,
            capture_output=True, text=True, timeout=20)
        assert second.returncode == 0, second.stdout + second.stderr
        second_report = json.loads(second.stdout)
        assert second_report["delivered"] == {"webhook": 0} and second_report["errors"] == []
        assert len(received) == len(tunnels) == 1
        assert cursor.read_bytes() == cursor_bytes
    path, headers, event = received[0]
    assert path == "/notification-TEST" and headers["Host"] == "example.org"
    assert "Authorization" not in headers and "Proxy-Authorization" not in headers
    assert event["type"] == "run_started" and event["source"] == "fact"
    assert event["run_id"] == "notifications" and event["event_id"] == f"fact:{event['source_id']}:1"
    assert tunnels[0][0].split()[:2] == [b"CONNECT", b"example.org:443"]
    assert source_snapshot() == before
