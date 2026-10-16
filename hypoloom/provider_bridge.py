"""Sandbox-local Anthropic HTTP bridge to one Host-owned Unix socket.

This module uses only the standard library so the adapter can mount and execute
it directly inside the isolated CPU runtime.  It neither holds provider secrets
nor selects upstream endpoints; the Host relay owns those responsibilities.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import http.client
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


LOCAL_PROVIDER_KEY = "hypoloom-local-provider"
_POST_PATHS = frozenset({"/v1/messages", "/v1/messages/count_tokens"})


class ProviderBridge:
    """Serve the CLI's local HTTP requests through one fixed Unix endpoint."""

    def __init__(self, socket_path: str, *, timeout_seconds: float = 600) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_arguments: object) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc or parsed.path not in _POST_PATHS:
                    self.send_error(404, "unsupported provider operation")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    length = -1
                if length < 0:
                    self.send_error(411, "Content-Length required")
                    return
                connection = http.client.HTTPConnection(
                    "localhost", timeout=bridge.timeout_seconds
                )
                started = False
                try:
                    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    connection.sock.settimeout(bridge.timeout_seconds)
                    connection.sock.connect(bridge.socket_path)
                    body = self.rfile.read(length)
                    headers = {
                        name: value
                        for name in (
                            "Content-Type",
                            "anthropic-version",
                            "anthropic-beta",
                        )
                        if (value := self.headers.get(name)) is not None
                    }
                    headers["x-api-key"] = LOCAL_PROVIDER_KEY
                    connection.request("POST", self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    self.send_response(response.status)
                    for name in ("Content-Type", "request-id", "retry-after"):
                        value = response.getheader(name)
                        if value is not None:
                            self.send_header(name, value)
                    self.end_headers()
                    started = True
                    while chunk := response.read1(64 * 1024):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (OSError, http.client.HTTPException):
                    if not started:
                        self.send_error(502, "provider relay unavailable")
                finally:
                    connection.close()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> ProviderBridge:
        self.thread.start()
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--network-socket")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a Claude command is required after --")
    with ExitStack() as resources:
        bridge = resources.enter_context(ProviderBridge(arguments.socket))
        environment = dict(os.environ)
        for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            environment.pop(name, None)
        if arguments.network_socket is not None:
            if __package__:
                from .network_proxy import NetworkBridge
            else:
                from network_proxy import NetworkBridge
            network = resources.enter_context(NetworkBridge(arguments.network_socket))
            environment.update({name: network.proxy_url for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")})
            environment.update({name: "127.0.0.1,localhost,::1" for name in ("no_proxy", "NO_PROXY")})
        environment.pop("ANTHROPIC_AUTH_TOKEN", None)
        environment["ANTHROPIC_API_KEY"] = LOCAL_PROVIDER_KEY
        environment["ANTHROPIC_BASE_URL"] = bridge.base_url
        return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
