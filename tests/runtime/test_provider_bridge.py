"""The sandbox bridge streams only supported requests to its fixed local relay."""

from __future__ import annotations

import http.client
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from hypoloom.provider_bridge import LOCAL_PROVIDER_KEY, ProviderBridge


def test_provider_bridge_streams_and_rejects_unapproved_requests(
    tmp_path: Path,
) -> None:
    received: list[tuple[str, dict[str, str], bytes]] = []
    release = threading.Event()
    first = b"event: ping\ndata: {}\n\n"
    last = b"event: message_stop\ndata: {}\n\n"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_arguments: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("request-id", "local-request")
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            if release.wait(timeout=5):
                self.wfile.write(last)
                self.wfile.flush()

    socket_path = tmp_path / "provider.sock"
    server = socketserver.UnixStreamServer(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with ProviderBridge(str(socket_path), timeout_seconds=5) as bridge:
            endpoint = urlsplit(bridge.base_url)
            connection = http.client.HTTPConnection(
                endpoint.hostname, endpoint.port, timeout=3
            )
            connection.request(
                "POST",
                "/v1/messages?beta=true",
                body=b'{"stream":true}',
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "test-beta",
                    "x-api-key": "synthetic-client-key",
                    "Authorization": "Bearer synthetic-client-token",
                    "Cookie": "synthetic-cookie",
                },
            )
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("request-id") == "local-request"
            # The final provider bytes do not exist yet: receiving this prefix
            # proves the bridge does not buffer the entire SSE response.
            assert response.read(len(first)) == first
            release.set()
            assert response.read() == last
            connection.close()
            for method, path, expected in (
                ("POST", "http://elsewhere.invalid/v1/messages", 404),
                ("POST", "/v1/unsupported", 404),
                ("CONNECT", "elsewhere.invalid:443", 501),
            ):
                connection = http.client.HTTPConnection(
                    endpoint.hostname, endpoint.port, timeout=3
                )
                connection.request(method, path, body=b"{}")
                response = connection.getresponse()
                assert response.status == expected
                response.read()
                connection.close()
        assert len(received) == 1
        path, headers, body = received[0]
        assert path == "/v1/messages?beta=true"
        assert body == b'{"stream":true}'
        assert headers["x-api-key"] == LOCAL_PROVIDER_KEY
        assert headers["anthropic-version"] == "2023-06-01"
        assert headers["anthropic-beta"] == "test-beta"
        assert "Authorization" not in headers
        assert "Cookie" not in headers
        assert not bridge.thread.is_alive()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_provider_bridge_reports_unavailable_relay(tmp_path: Path) -> None:
    with ProviderBridge(str(tmp_path / "missing.sock"), timeout_seconds=1) as bridge:
        endpoint = urlsplit(bridge.base_url)
        connection = http.client.HTTPConnection(
            endpoint.hostname, endpoint.port, timeout=3
        )
        connection.request("POST", "/v1/messages", body=b"{}")
        response = connection.getresponse()
        assert response.status == 502
        connection.close()
