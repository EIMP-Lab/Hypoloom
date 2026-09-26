"""Caught Host MCP errors retain private stacks without changing tool outcomes."""

from concurrent.futures import ThreadPoolExecutor
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from hypoloom.host_tools_server import AgentToolServer, call_tool
from hypoloom.runtime_diagnostics import RuntimeDiagnostics
from tests.runtime.test_host_tools_server import _finish


def _recorder(tmp_path):
    return RuntimeDiagnostics.for_agent(tmp_path / "state",
        SimpleNamespace(run_id="run-owned", attempt_id="attempt-owned"),
        secrets=("known-provider-value",))


def _events(recorder):
    path = recorder.root / "events.jsonl"
    assert path.is_file(), "caught MCP exception lost its Python traceback"
    assert path.stat().st_mode & 0o777 == 0o600
    assert recorder.root.stat().st_mode & 0o777 == 0o700
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("failure,stage,error_type,message", (
    ("workspace", "workspace_capture", "ValueError", "workspace denied known-provider-value"),
    ("handler", "handler", "RuntimeError", "handler rejected known-provider-value"),
    ("type", "result_validation", "TypeError", "Host tool handler must return an object"),
    ("encoding", "result_encoding", "TypeError", "Object of type set is not JSON serializable"),
))
def test_caught_service_errors_keep_exact_client_error_and_full_private_stack(
    tmp_path, failure, stage, error_type, message,
):
    recorder = _recorder(tmp_path)
    calls = []
    owner = threading.get_ident()

    def capture(candidate):
        assert threading.get_ident() == owner
        assert candidate == {"private_input": "do-not-copy-arguments"}
        if failure == "workspace":
            raise ValueError(message)
        return []

    def handler(attempt_id, tool, arguments):
        calls.append((threading.get_ident(), attempt_id, tool))
        if failure == "handler":
            try:
                raise LookupError("nested cause known-provider-value")
            except LookupError as cause:
                raise RuntimeError(message) from cause
        return [] if failure == "type" else {"invalid": set()}

    server = AgentToolServer(tmp_path / "tools.sock", "attempt-owned", handler, capture,
        diagnostics=recorder)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call_tool, str(server.socket_path), "submit_experiment",
                {"candidate": {"private_input": "do-not-copy-arguments"}})
            with pytest.raises(ValueError) as caught:
                _finish(server, future)
        assert str(caught.value) == message
        assert calls == ([] if failure == "workspace" else [(owner, "attempt-owned", "submit_experiment")])
        [event] = _events(recorder)
        assert event["kind"] == "mcp_exception"
        assert event["attempt_id"] == "attempt-owned"
        assert event["tool"] == "submit_experiment"
        assert event["stage"] == stage
        assert event["exception_type"] == error_type
        assert event["message"] == message.replace("known-provider-value", "[REDACTED]")
        assert "Traceback (most recent call last)" in event["traceback"]
        assert "host_tools_server.py" in event["traceback"]
        assert error_type + ": " in event["traceback"]
        if failure == "handler":
            assert "LookupError: nested cause [REDACTED]" in event["traceback"]
            assert "direct cause" in event["traceback"]
        assert "known-provider-value" not in json.dumps(event)
        assert "do-not-copy-arguments" not in json.dumps(event)
        assert recorder.summary()["capture_errors"] == []
    finally:
        server.close()


@pytest.mark.parametrize("record", (False, True))
def test_successful_calls_preserve_results_without_exception_events(tmp_path, record):
    recorder = _recorder(tmp_path)
    expected = {"experiments": [], "status": "unchanged", "value": 3}
    server = AgentToolServer(tmp_path / "tools.sock", "attempt-owned", lambda *_: expected,
        diagnostics=recorder if record else None)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert _finish(server, pool.submit(call_tool, str(server.socket_path),
                "list_my_experiments", {})) == expected
        assert not (recorder.root / "events.jsonl").exists()
        assert recorder.summary()["capture_errors"] == []
    finally:
        server.close()


def test_diagnostic_io_failure_is_visible_without_masking_tool_error(tmp_path, monkeypatch, capsys):
    recorder = _recorder(tmp_path)

    def disk_full(*_args, **_kwargs):
        raise OSError("diagnostic disk full known-provider-value")

    def handler(*_args):
        raise ValueError("original handler rejection")

    monkeypatch.setattr(recorder, "_open", disk_full)
    server = AgentToolServer(tmp_path / "tools.sock", "attempt-owned", handler,
        diagnostics=recorder)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ValueError, match="^original handler rejection$"):
                _finish(server, pool.submit(call_tool, str(server.socket_path),
                    "list_my_experiments", {}))
        [error] = recorder.summary()["capture_errors"]
        assert error["operation"] == "events.jsonl"
        assert error["message"] == "diagnostic disk full [REDACTED]"
        stderr = capsys.readouterr().err
        assert "diagnostic capture failed" in stderr
        assert "known-provider-value" not in stderr
    finally:
        server.close()


@pytest.mark.parametrize("document,message,tool", (
    (b"not JSON\n", "Expecting value: line 1 column 1 (char 0)", None),
    ({"tool": "list_my_experiments", "arguments": {}, "attempt_id": "forged"},
        "Host tool request requires only tool and arguments", None),
    ({"tool": "operator_cancel_run", "arguments": {}},
        "Host tool is not authorized for this invocation scope", None),
    ({"tool": "submit_experiment", "arguments": {"candidate": {}, "workspace_files": []}},
        'unknown tool arguments: "workspace_files"; allowed fields: candidate, command, gpus, timeout_min, description, replaces_experiment_id, resource_estimate.', "submit_experiment"),
))
def test_socket_validation_rejection_records_stack_without_authorizing_effects(
    tmp_path, document, message, tool,
):
    recorder = _recorder(tmp_path)
    calls = []
    server = AgentToolServer(tmp_path / "tools.sock", "attempt-owned",
        lambda *args: calls.append(args) or {}, diagnostics=recorder)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(server.socket_path))
            connection.sendall(document if isinstance(document, bytes)
                else json.dumps(document).encode() + b"\n")
            with connection.makefile("rb") as stream:
                assert json.loads(stream.readline()) == {"ok": False, "error": message}
        assert not server.service()
        assert calls == []
        [event] = _events(recorder)
        assert event["stage"] == "request_validation"
        assert event["tool"] == tool
        assert event["attempt_id"] == "attempt-owned"
        assert event["message"] == message
        assert "Traceback (most recent call last)" in event["traceback"]
        assert "forged" not in json.dumps(event)
    finally:
        server.close()


def test_disconnected_client_preserves_caught_response_write_traceback(tmp_path):
    recorder = _recorder(tmp_path)
    server = AgentToolServer(tmp_path / "tools.sock", "attempt-owned", lambda *_: {"ok": True},
        diagnostics=recorder)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(server.socket_path))
            connection.sendall(b'{"tool":"list_my_experiments","arguments":{}}\n')
            deadline = time.monotonic() + 2
            while not server._pending and time.monotonic() < deadline:
                time.sleep(0.005)
            assert server._pending
        assert server.service()
        deadline = time.monotonic() + 2
        while not (recorder.root / "events.jsonl").exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        server.close()
        [event] = _events(recorder)
        assert event["stage"] == "response_write"
        assert event["tool"] == "list_my_experiments"
        assert event["exception_type"] == "BrokenPipeError"
        assert "Traceback (most recent call last)" in event["traceback"]
    finally:
        server.close()
