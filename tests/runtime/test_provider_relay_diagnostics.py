"""Real local HTTP evidence copies retain relay rejection and byte semantics."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from test_provider_relay import (
    _BODY, _KEY, _RESPONSE, _TOKEN, _Upstream, _connection, _relay,
    _request, upstream as upstream,
)


def _recorder(root: Path) -> Any:
    from hypoloom.runtime_diagnostics import RuntimeDiagnostics

    # The relay must supply its own known secrets to the copy boundary.
    return RuntimeDiagnostics(root)


def _exchange(root: Path, number: int = 1) -> tuple[Path, dict[str, Any]]:
    directory = root / "provider" / f"{number:06d}"
    metadata = directory / "exchange.json"
    until = time.monotonic() + 3
    while time.monotonic() < until:
        if metadata.exists():
            document = json.loads(metadata.read_text())
            if document.get("ended_at") is not None:
                return directory, document
        time.sleep(0.01)
    raise AssertionError(f"missing completed relay diagnostics: {metadata}")


def test_optional_capture_accepts_disabled_recorder(tmp_path: Path, upstream: _Upstream) -> None:
    relay = _relay(tmp_path, upstream, diagnostics=None)
    try:
        assert _request(relay) == (200, _RESPONSE)
    finally:
        relay.close()


def test_successful_actual_bytes_and_exchange_navigation(tmp_path: Path, upstream: _Upstream) -> None:
    root = tmp_path / "diagnostics"
    upstream.extra_headers = {"request-id": "req_safe-123", "Set-Cookie": "session=private"}
    relay = _relay(tmp_path, upstream, diagnostics=_recorder(root))
    try:
        for ordinal in (1, 2):
            assert _request(relay, path="/v1/messages?private=query") == (200, _RESPONSE)
            directory, metadata = _exchange(root, ordinal)
            assert (directory / "request.body").read_bytes() == _BODY
            assert (directory / "response.body").read_bytes() == _RESPONSE
            assert metadata["request_path"] == "/v1/messages"
            assert metadata["request_bytes_received"] == metadata["request_bytes_forwarded"] == len(_BODY)
            assert metadata["response_bytes_received"] == metadata["response_bytes_forwarded"] == len(_RESPONSE)
            assert metadata["response_body_complete"] is True
            assert metadata["response_partial_reason"] is None
            assert metadata["failure_stage"] is None
            assert metadata["response_headers"]["request-id"] == "req_safe-123"
            encoded = json.dumps(metadata)
            assert "private=query" not in encoded and "Set-Cookie" not in encoded
            assert upstream.base_url not in encoded
    finally:
        relay.close()


@pytest.mark.parametrize("status", (302, 429, 500))
def test_error_body_kept_private_and_response_unchanged(tmp_path: Path, upstream: _Upstream, status: int) -> None:
    root = tmp_path / "diagnostics"
    upstream.status = status
    upstream.body = json.dumps({"reason": "real provider failure", "key": _KEY, "token": _TOKEN}).encode()
    upstream.extra_headers = {
        "Location": "https://elsewhere.invalid/credential-location",
        "request-id": "req_failure_42", "x-api-key": _KEY,
        "Authorization": "Bearer " + _TOKEN,
        "x-request-id": "https://unsafe.invalid/request",
    }
    relay = _relay(tmp_path, upstream, diagnostics=_recorder(root))
    try:
        actual, forwarded = _request(relay)
        assert actual == (502 if status == 302 else status)
        assert b"real provider failure" not in forwarded
        directory, metadata = _exchange(root)
        captured = (directory / "response.body").read_bytes()
        assert b"real provider failure" in captured
        assert _KEY.encode() not in captured and _TOKEN.encode() not in captured
        assert metadata["upstream_status"] == status
        assert metadata["failure_stage"] == "upstream_status"
        assert metadata["response_bytes_forwarded"] == 0
        assert metadata["response_bytes_received"] == len(upstream.body)
        assert metadata["response_body_complete"] is True
        assert metadata["response_capture"]["redacted"] is True
        all_files = b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())
        assert _KEY.encode() not in all_files and _TOKEN.encode() not in all_files
        assert b"elsewhere.invalid" not in all_files and b"unsafe.invalid" not in all_files
        # Diagnostic error reads must not change the next accepted call's budget.
        upstream.status, upstream.body = 200, _RESPONSE
        assert _request(relay) == (200, _RESPONSE)
        assert relay._response_bytes == len(_RESPONSE)
    finally:
        relay.close()


def test_split_secret_copy_redacts_and_original_stream_still_rejects(tmp_path: Path, upstream: _Upstream) -> None:
    root = tmp_path / "diagnostics"
    release = threading.Event()
    prefix = b"event: ping\ndata: " + b"x" * 200 + b"\n\n"

    def stream(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(prefix + _KEY[:16].encode())
        handler.wfile.flush()
        if release.wait(2):
            handler.wfile.write(_KEY[16:].encode() + b"\n\n")
            handler.wfile.flush()

    upstream.script = stream
    relay = _relay(tmp_path, upstream, diagnostics=_recorder(root))
    try:
        connection = _connection(relay)
        connection.request("POST", "/v1/messages", body=_BODY)
        response = connection.getresponse()
        first = response.read(1)
        release.set()
        forwarded = first + response.read()
        connection.close()
        assert _KEY[:16].encode() not in forwarded
        directory, metadata = _exchange(root)
        captured = (directory / "response.body").read_bytes()
        assert captured.startswith(prefix)
        assert _KEY.encode() not in captured and _KEY[:16].encode() not in captured
        assert metadata["response_capture"]["redacted"] is True
        assert metadata["failure_stage"] == "credential_scan"
        assert metadata["response_bytes_forwarded"] == len(forwarded)
    finally:
        release.set()
        relay.close()


def test_oversized_response_is_explicitly_partial(tmp_path: Path, upstream: _Upstream) -> None:
    root = tmp_path / "diagnostics"
    maximum = len(_RESPONSE) - 1
    relay = _relay(tmp_path, upstream, max_response_bytes=maximum, diagnostics=_recorder(root))
    try:
        assert _request(relay)[0] == 502
        directory, metadata = _exchange(root)
        assert (directory / "response.body").read_bytes() == _RESPONSE[:maximum]
        assert metadata["response_bytes_received"] == len(_RESPONSE)
        assert metadata["response_body_complete"] is False
        assert metadata["response_partial_reason"] == "response_byte_limit"
        assert metadata["failure_stage"] == "response_byte_limit"
    finally:
        relay.close()


def test_rejected_body_cap_does_not_spend_success_budget(tmp_path: Path, upstream: _Upstream) -> None:
    root = tmp_path / "diagnostics"
    maximum = len(_RESPONSE)
    upstream.status, upstream.body = 429, b"e" * (maximum * 2)
    relay = _relay(tmp_path, upstream, max_response_bytes=maximum, diagnostics=_recorder(root))
    try:
        assert _request(relay)[0] == 429
        directory, metadata = _exchange(root)
        assert (directory / "response.body").read_bytes() == b"e" * maximum
        assert metadata["response_bytes_received"] == maximum
        assert metadata["response_body_complete"] is False
        assert metadata["response_partial_reason"] == "response_byte_limit"
        assert metadata["failure_stage"] == "upstream_status"
        upstream.status, upstream.body = 200, _RESPONSE
        assert _request(relay) == (200, _RESPONSE)
        assert relay._response_bytes == maximum
    finally:
        relay.close()


def test_request_copy_redacts_without_rewriting_forwarded_body(tmp_path: Path, upstream: _Upstream) -> None:
    root = tmp_path / "diagnostics"
    body = _BODY[:-1] + b', "private": "' + _KEY.encode() + b'"}'
    relay = _relay(tmp_path, upstream, diagnostics=_recorder(root))
    try:
        assert _request(relay, body=body) == (200, _RESPONSE)
        assert upstream.calls[0][2] == body
        directory, metadata = _exchange(root)
        assert _KEY.encode() not in (directory / "request.body").read_bytes()
        assert metadata["capture"]["redacted"] is True
        assert metadata["request_bytes_received"] == metadata["request_bytes_forwarded"] == len(body)
    finally:
        relay.close()


@pytest.mark.parametrize("status", (200, 429))
def test_actual_partial_body_deadline_does_not_replace_original_status(tmp_path: Path, upstream: _Upstream, status: int) -> None:
    root = tmp_path / "diagnostics"
    release = threading.Event()
    prefix = b"event: ping\ndata: " + b"p" * 200 + b"\n\n"

    def stalled(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(prefix)
        handler.wfile.flush()
        release.wait(2)

    upstream.script = stalled
    relay = _relay(tmp_path, upstream, deadline_at=time.monotonic() + 0.5, diagnostics=_recorder(root))
    try:
        actual, forwarded = _request(relay)
        assert actual == status
        directory, metadata = _exchange(root)
        assert (directory / "response.body").read_bytes() == prefix
        assert metadata["response_body_complete"] is False
        assert metadata["response_partial_reason"] == "deadline"
        assert metadata["failure_stage"] == ("response_read" if status == 200 else "upstream_status")
        assert metadata["response_bytes_received"] == len(prefix)
        if status == 200:
            assert prefix.startswith(forwarded)
            assert metadata["response_bytes_forwarded"] == len(forwarded)
        else:
            assert prefix not in forwarded
            assert metadata["response_bytes_forwarded"] == 0
    finally:
        release.set()
        relay.close()


@pytest.mark.parametrize("status", (200, 500))
def test_truncated_content_length_is_not_claimed_complete(tmp_path: Path, upstream: _Upstream, status: int) -> None:
    root = tmp_path / "diagnostics"

    def truncated(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(_RESPONSE) + 100))
        handler.end_headers()
        handler.wfile.write(_RESPONSE)

    upstream.script = truncated
    relay = _relay(tmp_path, upstream, diagnostics=_recorder(root))
    try:
        actual, forwarded = _request(relay)
        assert actual == status
        if status == 200:
            assert forwarded == _RESPONSE
        directory, metadata = _exchange(root)
        assert (directory / "response.body").read_bytes() == _RESPONSE
        assert metadata["response_body_complete"] is False
        assert metadata["response_partial_reason"] == "incomplete_content_length"
    finally:
        relay.close()


def test_diagnostic_io_failure_keeps_success_and_rejection(tmp_path: Path, upstream: _Upstream, capsys: Any) -> None:
    class BrokenRecorder:
        def child(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("do not print " + _KEY)

    relay = _relay(tmp_path, upstream, diagnostics=BrokenRecorder())
    try:
        assert _request(relay) == (200, _RESPONSE)
        upstream.status = 429
        assert _request(relay)[0] == 429
        assert _request(relay, body=json.dumps({"model": "wrong"}).encode())[0] == 400
        assert len(upstream.calls) == 2
    finally:
        relay.close()
    stderr = capsys.readouterr().err
    assert "provider diagnostics child failed: OSError" in stderr
    assert _KEY not in stderr


@pytest.mark.parametrize("status", (200, 429))
def test_midstream_capture_failure_does_not_replace_transport(tmp_path: Path, upstream: _Upstream, monkeypatch: Any, capsys: Any, status: int) -> None:
    root = tmp_path / "diagnostics"
    recorder = _recorder(root)

    class BrokenStream:
        def write(self, value: bytes) -> None:
            raise OSError("write failed " + _KEY)

        def close(self) -> None:
            raise OSError("close failed " + _TOKEN)

        def summary(self) -> dict[str, Any]:
            return {"complete": False}

    monkeypatch.setattr(type(recorder), "stream", lambda *args: BrokenStream())
    upstream.status = status
    relay = _relay(tmp_path, upstream, diagnostics=recorder)
    try:
        actual, forwarded = _request(relay)
        assert actual == status
        if status == 200:
            assert forwarded == _RESPONSE
        else:
            assert forwarded != _RESPONSE
        _, metadata = _exchange(root)
        assert metadata["failure_stage"] == (None if status == 200 else "upstream_status")
        assert {entry["operation"] for entry in metadata["capture_errors"]} == {"response_body", "response_close"}
        events = (root / "events.jsonl").read_text()
        assert "provider_capture_error" in events
        assert _KEY not in events and _TOKEN not in events
    finally:
        relay.close()
    stderr = capsys.readouterr().err
    assert "response_body failed: OSError" in stderr
    assert _KEY not in stderr and _TOKEN not in stderr
