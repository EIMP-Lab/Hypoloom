"""Slow storage must not own transport, admission or final usage observations."""
from __future__ import annotations

import json
import threading
import time

import pytest

from hypoloom.runtime_diagnostics import DeferredProviderCapture, ProviderDiagnosticWriter, RuntimeDiagnostics
from test_provider_relay import _MODEL, _RESPONSE, _relay, _request, upstream as upstream
from test_provider_relay_diagnostics import _exchange


@pytest.mark.parametrize('reflection', ['request_id', 'model'])
def test_live_observation_preserves_existing_credential_redaction(tmp_path, upstream, reflection):
    from test_provider_relay import _KEY
    if reflection == 'request_id':
        upstream.extra_headers = {'x-request-id': _KEY}
    else:
        upstream.body = json.dumps({'type': 'message', 'model': _KEY,
            'stop_reason': 'end_turn', 'usage': {'input_tokens': 2, 'output_tokens': 1}}).encode()
    recorder = RuntimeDiagnostics(tmp_path / 'diagnostics')
    relay = _relay(tmp_path, upstream, diagnostics=recorder)
    try:
        status, body = _request(relay)
        assert status == (200 if reflection == 'request_id' else 502)
        assert _KEY.encode() not in body
    finally:
        relay.close()
    rows = relay.take_exchanges()
    assert len(rows) == 1
    assert _KEY not in json.dumps(rows)
    _, saved = _exchange(recorder.root)
    assert _KEY not in json.dumps(saved)
    assert rows[0]['response_headers'] == saved['response_headers']
    assert rows[0]['served_models'] == saved['served_models']
    assert rows[0]['usage'] == saved['usage']


@pytest.mark.parametrize('delay', [0.1, 1.0, 10.0])
def test_blocked_storage_does_not_delay_transport_or_final_observation(tmp_path, upstream, monkeypatch, delay):
    entered, release = threading.Event(), threading.Event()
    original = RuntimeDiagnostics.child

    def slow(self, *args, **kwargs):
        entered.set()
        release.wait(delay)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RuntimeDiagnostics, 'child', slow)
    from hypoloom.provider_traffic import ProviderTraffic
    traffic = ProviderTraffic(max_in_flight=1)
    writer = ProviderDiagnosticWriter()
    recorder = RuntimeDiagnostics(tmp_path / 'diagnostics')
    relay = _relay(tmp_path, upstream, diagnostics=recorder, diagnostic_writer=writer,
                   traffic_binding=(traffic, ('expert', 'test'), None))
    upstream.body = json.dumps({'type': 'message', 'model': _MODEL,
        'content': [{'type': 'text', 'text': 'answer'}], 'stop_reason': 'end_turn',
        'usage': {'input_tokens': 11, 'output_tokens': 7}}).encode()
    try:
        start = time.monotonic()
        assert _request(relay) == (200, upstream.body)
        assert entered.wait(1)
        assert _request(relay) == (200, upstream.body)  # Released slot, same blocked disk.
        relay.close(drain_diagnostics=False)
        rows = relay.take_exchanges()
        assert len(rows) == 2
        assert traffic.snapshot()['in_flight'] == traffic.snapshot()['queued'] == 0
        assert traffic.snapshot()['rpm'] == 2
        assert time.monotonic() - start < 1.5
        assert rows[0]['response_body_complete'] is True
        assert rows[0]['usage'] == {'input_tokens': 11, 'output_tokens': 7}
        assert rows[0]['message_complete'] is True
        if delay == 10:
            assert not (recorder.root / 'provider/000001/exchange.json').exists()
            assert not writer.close(timeout=0.01)
    finally:
        release.set()
        relay.close(drain_diagnostics=False)
        assert writer.close()
    _, saved = _exchange(recorder.root)
    assert saved['ended_at'] == rows[0]['ended_at']
    assert saved['usage'] == rows[0]['usage']


def test_body_overflow_keeps_safe_prefix_and_terminal_metadata(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = RuntimeDiagnostics.child

    def slow(self, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RuntimeDiagnostics, 'child', slow)
    backend = ProviderDiagnosticWriter(exchange_bytes=256)
    recorder = RuntimeDiagnostics(tmp_path / 'diagnostics')
    capture = DeferredProviderCapture(backend, recorder, 1, (b'secret-value',))
    data = {'exchange': 1, 'started_at': 1, 'ended_at': None, 'response_body_complete': True}
    capture.save(data)
    assert entered.wait(1)
    capture.response()
    capture.write(b'a' * 100 + b'secret-')
    capture.write(b'value' + b'b' * 400)
    capture.write(b'never append this suffix')
    capture.finish({**data, 'ended_at': 2})
    assert backend._bytes <= backend.max_bytes
    release.set()
    assert backend.close()
    directory, metadata = _exchange(recorder.root)
    assert (directory / 'response.body').read_bytes() == b'a' * 96
    assert metadata['response_body_complete'] is True
    assert metadata['response_capture']['complete'] is False
    assert metadata['response_capture']['bytes_received'] == 107 + 405 + 24
    assert any(error['exception_type'] == 'queue_capacity' for error in metadata['capture_errors'])
    assert backend._bytes == backend._body_bytes == 0


def test_control_exhaustion_never_certifies_truncated_live_observations(tmp_path, upstream):
    backend = ProviderDiagnosticWriter(max_bytes=32, control_bytes=16)
    relay = _relay(tmp_path, upstream, diagnostic_writer=backend)
    try:
        assert _request(relay) == (200, _RESPONSE)
        relay.close(drain_diagnostics=False)
        assert relay.take_exchanges() is None
        assert backend._bytes == 0
    finally:
        relay.close(drain_diagnostics=False)
        backend.close()


def test_many_tiny_chunks_do_not_fsync_each_chunk(tmp_path, upstream, monkeypatch):
    from test_provider_relay import _BODY
    import os
    fsyncs = []
    original = os.fsync
    monkeypatch.setattr(os, 'fsync', lambda fd: (fsyncs.append(fd), original(fd))[-1])
    def stream(handler):
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream')
        handler.send_header('Transfer-Encoding', 'chunked')
        handler.end_headers()
        for _ in range(200):
            chunk = b'event: ping\ndata: {}\n\n'
            handler.wfile.write(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n')
            handler.wfile.flush()
        handler.wfile.write(b'0\r\n\r\n')
    upstream.script = stream
    relay = _relay(tmp_path, upstream, diagnostics=RuntimeDiagnostics(tmp_path / 'diagnostics'))
    try:
        status, body = _request(relay, body=_BODY)
        assert status == 200 and body == b'event: ping\ndata: {}\n\n' * 200
    finally:
        relay.close()
    assert len(fsyncs) <= 6
    _, metadata = _exchange(tmp_path / 'diagnostics')
    assert metadata['response_capture']['complete']
