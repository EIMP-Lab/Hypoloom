"""Host authentication stays outside the sandbox's fixed provider transport."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import pytest

from hypoloom.provider_bridge import ProviderBridge
from hypoloom.provider_relay import ProviderRelay


_MODEL = "claude-sonnet-4-6"
_KEY = "synthetic-host-api-key-0123456789abcdef"
_TOKEN = "synthetic-host-auth-token-abcdef0123456789"
_BODY = json.dumps({"model": _MODEL, "messages": [], "stream": True}).encode()
_RESPONSE = (
    b'{"type":"message","content":[],"usage":{"input_tokens":1,"output_tokens":1}}'
)


class _Upstream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.status = 200
        self.body = _RESPONSE
        self.extra_headers: dict[str, str] = {}
        self.script: Callable[[BaseHTTPRequestHandler], None] | None = None
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_arguments: object) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                body = self.rfile.read(int(self.headers["Content-Length"]))
                upstream.calls.append((self.path, dict(self.headers), body))
                try:
                    if upstream.script is not None:
                        upstream.script(self)
                        return
                    self.send_response(upstream.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(upstream.body)))
                    for name, value in upstream.extra_headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(upstream.body)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/api"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def upstream() -> Any:
    server = _Upstream()
    try:
        yield server
    finally:
        server.close()


def _relay(tmp_path: Path, upstream: _Upstream, **overrides: Any) -> ProviderRelay:
    options = {
        "base_url": upstream.base_url,
        "provider_environment": {
            "ANTHROPIC_API_KEY": _KEY,
            "ANTHROPIC_AUTH_TOKEN": _TOKEN,
        },
        "model": _MODEL,
        "max_request_bytes": 4096,
        "max_response_bytes": 8192,
        "max_requests": 5,
        "deadline_at": time.monotonic() + 10,
        **overrides,
    }
    return ProviderRelay(tmp_path / "relay.sock", **options)


def _connection(relay: ProviderRelay) -> http.client.HTTPConnection:
    connection = http.client.HTTPConnection("localhost", timeout=3)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(3)
    connection.sock.connect(str(relay.socket_path))
    return connection


def _request(
    relay: ProviderRelay,
    *,
    path: str = "/v1/messages",
    body: bytes = _BODY,
    method: str = "POST",
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    connection = _connection(relay)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_token_budget_blocks_next_generation_including_cached_input(tmp_path, upstream):
    upstream.body = json.dumps({"type": "message", "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 40}}).encode()
    relay = _relay(tmp_path, upstream, token_budget=50)
    try:
        assert _request(relay)[0] == 200
        assert _request(relay)[0] == 400
        assert len(upstream.calls) == 1
        assert relay.token_limit_reached
        # Count-only requests are not generations and spend no generation tokens.
        assert _request(relay, path="/v1/messages/count_tokens")[0] == 200
    finally:
        relay.close()


@pytest.mark.parametrize("semantics,second_status", [("includes_cache", 200), ("unknown", 400)])
def test_cache_semantics_control_observed_budget_without_weakening_unknown_limits(tmp_path, upstream, semantics, second_status):
    upstream.body = json.dumps({"type": "message", "stop_reason": "end_turn",
        "usage": {"input_tokens": 50, "output_tokens": 3, "cache_read_input_tokens": 40}}).encode()
    relay = _relay(tmp_path, upstream, token_budget=60, context_policy={
        "effective_context_tokens": 100000, "tool_reserve_tokens": 1000,
        "estimate_margin_tokens": 1000, "input_usage_semantics": semantics})
    try:
        assert _request(relay)[0] == 200
        assert _request(relay)[0] == second_status
        assert _request(relay)[0] == 400
        assert len(upstream.calls) == (2 if second_status == 200 else 1)
        assert relay.token_limit_reached
    finally:
        relay.close()


def test_observe_mode_does_not_enable_token_enforcement(tmp_path, upstream):
    upstream.body = json.dumps({"type": "message", "stop_reason": "end_turn",
        "usage": {"input_tokens": 1000000, "output_tokens": 10}}).encode()
    relay = _relay(tmp_path, upstream)
    try:
        assert _request(relay)[0] == _request(relay)[0] == 200
        assert len(upstream.calls) == 2
        assert not relay.token_limit_reached
    finally:
        relay.close()


def test_host_authentication_and_base_prefix_work_through_sandbox_bridge(
    tmp_path: Path, upstream: _Upstream
) -> None:
    relay = _relay(tmp_path, upstream)
    try:
        with ProviderBridge(str(relay.socket_path)) as bridge:
            endpoint = urlsplit(bridge.base_url)
            connection = http.client.HTTPConnection(
                endpoint.hostname, endpoint.port, timeout=3
            )
            connection.request(
                "POST",
                "/v1/messages?beta=true",
                body=_BODY,
                headers={
                    "x-api-key": "synthetic-sandbox-override",
                    "Authorization": "Bearer synthetic-sandbox-override",
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "test-beta",
                    "Cookie": "synthetic-cookie",
                },
            )
            response = connection.getresponse()
            body = response.read()
            connection.close()
            assert response.status == 200
            assert body == _RESPONSE
            assert _KEY.encode() not in body
            assert _TOKEN.encode() not in body
        assert len(upstream.calls) == 1
        path, headers, forwarded = upstream.calls[0]
        assert path == "/api/v1/messages?beta=true"
        assert forwarded == _BODY
        assert headers["x-api-key"] == _KEY
        assert headers["Authorization"] == "Bearer " + _TOKEN
        assert headers["anthropic-version"] == "2023-06-01"
        assert headers["anthropic-beta"] == "test-beta"
        assert "Cookie" not in headers
        assert _request(relay, path="/v1/messages/count_tokens")[0] == 200
        assert upstream.calls[-1][0] == "/api/v1/messages/count_tokens"
    finally:
        relay.close()
    assert not relay.socket_path.exists()
    assert not relay.thread.is_alive()


@pytest.mark.parametrize("actual", (None, {}, {"effort": "high"}, {"effort": "unknown"}))
def test_effort_mismatch_is_explicit_and_never_reaches_provider(
    tmp_path: Path, upstream: _Upstream, actual: Any
) -> None:
    relay = _relay(tmp_path, upstream, expected_effort="xhigh")
    document = {"model": _MODEL, "output_config": actual}
    try:
        status, body = _request(relay, body=json.dumps(document).encode())
        assert status == 400
        assert b"provider relay effort mismatch" in body
        assert not upstream.calls
        assert relay._request_count == 0
        assert _KEY.encode() not in body and _TOKEN.encode() not in body
    finally:
        relay.close()


@pytest.mark.parametrize("effort", ("low", "medium", "high", "xhigh", "max"))
def test_declared_effort_is_preserved_without_model_name_allowlist(
    tmp_path: Path, upstream: _Upstream, effort: str
) -> None:
    relay = _relay(tmp_path, upstream, expected_effort=effort)
    body = json.dumps({"model": _MODEL, "output_config": {"effort": effort}}).encode()
    try:
        assert _request(relay, body=body)[0] == 200
        assert upstream.calls[0][2] == body
        # Token counting has no generated response or output effort field.
        assert _request(relay, path="/v1/messages/count_tokens")[0] == 200
    finally:
        relay.close()


@pytest.mark.parametrize("path", ("/v1/messages", "/v1/messages/count_tokens"))
def test_long_context_selector_keeps_identity_and_exact_wire_model(
    tmp_path: Path, upstream: _Upstream, path: str
) -> None:
    selector = _MODEL + "[1m]"
    relay = _relay(tmp_path, upstream, model=selector)
    beta = "other-beta,context-1m-2025-08-07"
    try:
        assert relay.model == selector
        assert _request(relay, path=path, headers={"anthropic-beta": beta})[0] == 200
        assert upstream.calls[0][1]["anthropic-beta"] == beta
        assert upstream.calls[0][2] == _BODY
    finally:
        relay.close()


@pytest.mark.parametrize("beta", (None, "token-counting-2024-11-01"))
def test_long_context_count_tokens_does_not_require_generation_context_beta(
    tmp_path: Path, upstream: _Upstream, beta: str | None
) -> None:
    relay = _relay(tmp_path, upstream, model=_MODEL + "[1m]", expected_effort="high")
    headers = {} if beta is None else {"anthropic-beta": beta}
    try:
        assert _request(relay, path="/v1/messages/count_tokens?beta=true", headers=headers)[0] == 200
        assert upstream.calls[0][2] == _BODY
        assert upstream.calls[0][1].get("anthropic-beta") == beta
        assert _request(relay, path="/v1/messages/count_tokens",
            body=json.dumps({"model": "different-model"}).encode(), headers=headers)[0] == 400
        assert len(upstream.calls) == 1
        assert _request(relay, headers=headers)[0] == 400
        assert len(upstream.calls) == 1
    finally:
        relay.close()


@pytest.mark.parametrize(
    "model,beta",
    (
        (_MODEL, ""),
        (_MODEL, "context-1m-unknown"),
        (_MODEL + "[1m]", "context-1m-2025-08-07"),
        ("claude-other-model", "context-1m-2025-08-07"),
    ),
)
def test_long_context_selector_refuses_context_downgrade_or_model_fallback(
    tmp_path: Path, upstream: _Upstream, model: str, beta: str
) -> None:
    relay = _relay(tmp_path, upstream, model=_MODEL + "[1m]")
    try:
        assert (
            _request(
                relay,
                body=json.dumps({"model": model}).encode(),
                headers={"anthropic-beta": beta},
            )[0]
            == 400
        )
        assert not upstream.calls
    finally:
        relay.close()


@pytest.mark.parametrize(
    "method,path,body,status",
    [
        ("POST", "http://elsewhere.invalid/v1/messages", _BODY, 404),
        ("POST", "/v1/unsupported", _BODY, 404),
        ("CONNECT", "elsewhere.invalid:443", _BODY, 501),
        ("POST", "/v1/messages", b'{"model":"wrong-model"}', 400),
        (
            "POST",
            "/v1/messages",
            b'{"model":"wrong-model","model":"claude-sonnet-4-6"}',
            400,
        ),
        ("POST", "/v1/messages", b'{"model":"claude-sonnet-4-6","value":NaN}', 400),
    ],
)
def test_relay_rejects_unbound_requests_before_upstream(
    tmp_path: Path,
    upstream: _Upstream,
    method: str,
    path: str,
    body: bytes,
    status: int,
) -> None:
    relay = _relay(tmp_path, upstream)
    try:
        actual, response = _request(relay, path=path, body=body, method=method)
        assert actual == status
        assert _KEY.encode() not in response
        assert upstream.calls == []
    finally:
        relay.close()


@pytest.mark.parametrize("status", [302, 400, 401, 429, 500])
def test_relay_sanitizes_reflected_credentials_and_never_follows_redirects(
    tmp_path: Path, upstream: _Upstream, status: int
) -> None:
    upstream.status = status
    upstream.body = json.dumps(
        {"reflected_key": _KEY, "reflected_token": _TOKEN}
    ).encode()
    upstream.extra_headers = {
        "Location": "http://elsewhere.invalid/" + _KEY,
        "request-id": _TOKEN,
    }
    relay = _relay(tmp_path, upstream)
    try:
        actual, body = _request(relay)
        assert actual == (502 if status == 302 else status)
        assert _KEY.encode() not in body
        assert _TOKEN.encode() not in body
        assert len(upstream.calls) == 1
    finally:
        relay.close()


def test_relay_streams_before_upstream_completes_and_blocks_split_credentials(
    tmp_path: Path, upstream: _Upstream
) -> None:
    release = threading.Event()
    prefix = b"event: ping\ndata: " + b"x" * 200 + b"\n\n"
    suffix = b"event: message_stop\ndata: {}\n\n"
    leak = False

    def stream(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(prefix + (_KEY[:16].encode() if leak else b""))
        handler.wfile.flush()
        if release.wait(timeout=3):
            handler.wfile.write((_KEY[16:].encode() if leak else b"") + suffix)
            handler.wfile.flush()

    upstream.script = stream
    relay = _relay(tmp_path, upstream)
    try:
        for leaking in (False, True):
            leak = leaking
            release.clear()
            connection = _connection(relay)
            connection.request("POST", "/v1/messages", body=_BODY)
            response = connection.getresponse()
            first = response.read(1)
            assert first == prefix[:1]
            release.set()
            body = first + response.read()
            connection.close()
            if leaking:
                assert _KEY.encode() not in body
                assert _KEY[:16].encode() not in body
                assert suffix not in body
            else:
                assert body == prefix + suffix
    finally:
        release.set()
        relay.close()


@pytest.mark.parametrize(
    "limits,status,upstream_count",
    [
        ({"max_request_bytes": len(_BODY) - 1}, 413, 0),
        ({"max_response_bytes": len(_RESPONSE) - 1}, 502, 1),
        ({"deadline_at": 0.0}, 504, 0),
    ],
)
def test_relay_enforces_request_response_and_deadline_limits(
    tmp_path: Path,
    upstream: _Upstream,
    limits: dict[str, Any],
    status: int,
    upstream_count: int,
) -> None:
    relay = _relay(tmp_path, upstream, **limits)
    try:
        assert _request(relay)[0] == status
        assert len(upstream.calls) == upstream_count
    finally:
        relay.close()


def test_relay_caps_calls_and_aggregate_response_bytes(
    tmp_path: Path, upstream: _Upstream
) -> None:
    relay = _relay(tmp_path, upstream, max_requests=1)
    try:
        assert _request(relay)[0] == 200
        assert _request(relay)[0] == 429
        assert len(upstream.calls) == 1
    finally:
        relay.close()
    relay = _relay(tmp_path, upstream, max_response_bytes=2 * len(_RESPONSE) - 1)
    try:
        assert _request(relay)[0] == 200
        assert _request(relay)[0] == 502
    finally:
        relay.close()


@pytest.mark.parametrize(
    "mode",
    ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"],
)
def test_relay_rejects_unsupported_credential_modes(
    tmp_path: Path, upstream: _Upstream, mode: str
) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _relay(
            tmp_path,
            upstream,
            provider_environment={"ANTHROPIC_API_KEY": _KEY, mode: "1"},
        )
    assert not (tmp_path / "relay.sock").exists()
    assert upstream.calls == []


@pytest.mark.parametrize("stop", ["deadline", "close"])
def test_relay_interrupts_a_stalled_upstream(
    tmp_path: Path, upstream: _Upstream, stop: str
) -> None:
    entered, release = threading.Event(), threading.Event()

    def stalled(handler: BaseHTTPRequestHandler) -> None:
        entered.set()
        release.wait(timeout=3)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(_RESPONSE)

    upstream.script = stalled
    relay = _relay(
        tmp_path,
        upstream,
        deadline_at=time.monotonic() + (0.2 if stop == "deadline" else 10),
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_request, relay)
            assert entered.wait(timeout=2)
            started = time.monotonic()
            if stop == "close":
                relay.close()
                with pytest.raises((OSError, http.client.HTTPException)):
                    future.result(timeout=1)
            else:
                assert future.result(timeout=1)[0] in {502, 504}
            assert time.monotonic() - started < 1
    finally:
        release.set()
        relay.close()
