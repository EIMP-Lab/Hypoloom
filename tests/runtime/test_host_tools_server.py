"""Invocation identity, Host-thread effects and bounded MCP waiting."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hypoloom import host_tools_server
from hypoloom.host_tools_server import AgentToolServer, call_tool


def _finish(server: AgentToolServer, future: Future[Any]) -> Any:
    deadline = time.monotonic() + 5
    while not future.done() and time.monotonic() < deadline:
        server.service()
        time.sleep(0.005)
    return future.result(timeout=0.5)


def test_host_tool_identity_capture_and_effects_stay_on_host_thread(
    tmp_path: Path,
) -> None:
    calls = []
    captured = []
    owner = threading.get_ident()

    def capture(candidate: dict[str, Any]) -> list[dict[str, Any]]:
        assert candidate == {"description": "test"}
        captured.append(threading.get_ident())
        return [{"path": "experiment.py", "content_base64": "cHJpbnQoMSkK"}]

    def handler(attempt_id: str, tool: str, arguments: Any) -> dict[str, Any]:
        calls.append((threading.get_ident(), attempt_id, tool, arguments))
        return {"exp_id": "experiment-1", "queue_position": 0}

    server = AgentToolServer(tmp_path / "tools.sock", "attempt-owned", handler, capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = _finish(
                server,
                pool.submit(
                    call_tool,
                    str(server.socket_path),
                    "submit_experiment",
                    {"candidate": {"description": "test"}},
                ),
            )
        assert result == {"exp_id": "experiment-1", "queue_position": 0}
        assert captured == [owner]
        assert calls == [
            (
                owner,
                "attempt-owned",
                "submit_experiment",
                {
                    "candidate": {"description": "test"},
                    "workspace_files": [
                        {"path": "experiment.py", "content_base64": "cHJpbnQoMSkK"}
                    ],
                },
            )
        ]
        assert not server.service()
        for forged in (
            {"candidate": {}, "attempt_id": "another-attempt"},
            {"candidate": {}, "workspace_files": []},
        ):
            with pytest.raises(ValueError, match="unknown tool arguments"):
                call_tool(str(server.socket_path), "submit_experiment", forged)
        assert len(calls) == 1
    finally:
        server.close()
    assert not server.socket_path.exists()
    assert not server.thread.is_alive()


def test_host_tool_wait_polls_without_stalling_host_and_preserves_queued_status(
    tmp_path: Path,
) -> None:
    calls = []
    terminal = False

    def handler(attempt_id: str, tool: str, arguments: Any) -> dict[str, Any]:
        calls.append((attempt_id, tool, arguments))
        return {
            "exp_id": "experiment-1",
            "status": "completed" if terminal else "queued",
            "terminal": terminal,
            "tail_log": "bounded log",
            "resource_wait": None if terminal else "capacity_unavailable",
        }

    server = AgentToolServer(tmp_path / "tools.sock", "attempt-owned", handler)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            started = time.monotonic()
            result = _finish(
                server,
                pool.submit(
                    call_tool,
                    str(server.socket_path),
                    "wait_experiment",
                    {"exp_id": "experiment-1", "max_wait_seconds": 0.15},
                ),
            )
            assert time.monotonic() - started < 2
            assert result["status"] == "queued"
            assert result["terminal"] is False
            assert result["resource_wait"] == "capacity_unavailable"
            assert len(calls) >= 2
            assert all(arguments["max_wait_seconds"] == 0 for _, _, arguments in calls)
            terminal = True
            result = _finish(
                server,
                pool.submit(
                    call_tool,
                    str(server.socket_path),
                    "wait_experiment",
                    {"exp_id": "experiment-1", "max_wait_seconds": 1},
                ),
            )
            assert result["status"] == "completed"
            assert result["terminal"] is True
        for invalid in (-1, 601, True):
            with pytest.raises(ValueError, match="max_wait_seconds"):
                call_tool(
                    str(server.socket_path),
                    "wait_experiment",
                    {"exp_id": "experiment-1", "max_wait_seconds": invalid},
                )
    finally:
        server.close()


@pytest.mark.parametrize("terminal_at", (None, 1.5))
def test_wait_backoff_is_bounded_and_preserves_timeout_with_fake_clock(monkeypatch, terminal_at):
    now = 0.0
    sleeps = []
    calls = []
    def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += seconds
    def query(_socket, tool, arguments):
        calls.append((now, tool, arguments))
        return {"exp_id": "experiment-1", "status": "queued",
                "terminal": terminal_at is not None and now >= terminal_at}
    monkeypatch.setattr(host_tools_server, "time", SimpleNamespace(monotonic=lambda: now, sleep=sleep))
    monkeypatch.setattr(host_tools_server, "_call_host", query)
    result = call_tool("not-used.sock", "wait_experiment", {"exp_id": "experiment-1", "max_wait_seconds": 3.1})
    assert sleeps[:4] == pytest.approx([0.1, 0.2, 0.4, 0.8])
    assert max(sleeps) <= 1
    assert all(arguments == {"exp_id": "experiment-1", "max_wait_seconds": 0} for _, _, arguments in calls)
    if terminal_at is None:
        assert now == pytest.approx(3.1)
        assert len(calls) == 7  # fixed 100ms polling previously needed 32 calls
        assert result["status"] == "queued" and result["terminal"] is False
    else:
        assert now == pytest.approx(1.5)
        assert result["terminal"] is True


def test_wait_holds_dispatch_block_without_model_turns_or_fabricated_completion(monkeypatch):
    now = 0.0
    calls = []
    result = {"exp_id": "experiment-1", "status": "blocked", "terminal": False,
              "state": "DRAFTED", "dispatch_error": {"exception_type": "AgentACIError"}}
    def sleep(seconds):
        nonlocal now
        now += seconds
    def read(*args):
        calls.append(args)
        return result
    monkeypatch.setattr(host_tools_server, "time", SimpleNamespace(monotonic=lambda: now, sleep=sleep))
    monkeypatch.setattr(host_tools_server, "_call_host", read)
    assert call_tool("unused.sock", "wait_experiment", {"exp_id": "experiment-1", "max_wait_seconds": 600}) == result
    assert now == 600 and 1 < len(calls) < 610
    calls.clear()
    assert call_tool("unused.sock", "wait_experiment", {"exp_id": "experiment-1", "max_wait_seconds": 0}) == result
    assert len(calls) == 1


@pytest.mark.parametrize("outcome", ["available", "verification_required", "deadline", "legacy", "batch"])
def test_document_wait_uses_existing_deadline_without_model_polling(monkeypatch, outcome):
    now, sleeps, calls = [0.0], [], []
    def sleep(seconds):
        now[0] += seconds
        sleeps.append(seconds)
    def query(_socket, tool, arguments, **kwargs):
        calls.append(now[0])
        if outcome == "available" and now[0] >= .3:
            return {"status": "available", "source_ref": "owned:paper"}
        if outcome == "verification_required" and now[0] >= .3:
            return {"status": "unavailable", "reason": outcome}
        if outcome == "legacy": return {"status": "pending"}
        # A repeated full window must not extend the first observed deadline.
        result = {"status": "pending", "remaining_seconds": .7}
        return {"documents": [{"status": "available", "source_ref": "owned:paper"}, result]} if outcome == "batch" else result
    monkeypatch.setattr(host_tools_server, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))
    monkeypatch.setattr(host_tools_server, "_call_host", query)
    result = call_tool("unused.sock", "web_fetch", {"mode": "document", "url": "https://doi.org/10.1109/example"})
    if outcome in {"deadline", "batch"}:
        assert now[0] == pytest.approx(.7)
        assert result.get("status") != "available"
    elif outcome == "legacy":
        assert len(calls) == 1 and not sleeps
    else:
        assert now[0] == pytest.approx(.3)
        assert result["status"] == ("available" if outcome == "available" else "unavailable")


def test_reader_capacity_block_still_returns_for_parent_serial_reading(monkeypatch):
    result = {"status": "blocked", "terminal": False, "reason": "agent_capacity"}
    monkeypatch.setattr(host_tools_server, "_call_host", lambda *args: result)
    monkeypatch.setattr(host_tools_server.time, "sleep", lambda *_: pytest.fail("reader capacity wait must return"))
    assert call_tool("unused.sock", "read_papers", {"action": "wait"}) == result


def test_real_socket_wait_backoff_keeps_host_service_nonblocking(tmp_path):
    query_times = []
    service_durations = []
    def handler(_attempt, _tool, _arguments):
        query_times.append(time.monotonic())
        return {"status": "running", "terminal": False}
    server = AgentToolServer(tmp_path / "tools.sock", "wait-owned", handler)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call_tool, str(server.socket_path), "wait_experiment",
                                 {"exp_id": "experiment-1", "max_wait_seconds": 0.8})
            deadline = time.monotonic() + 3
            while not future.done() and time.monotonic() < deadline:
                started = time.monotonic()
                server.service()
                service_durations.append(time.monotonic() - started)
                time.sleep(0.005)
            assert future.result(timeout=1)["status"] == "running"
        assert len(query_times) <= 5
        assert max(service_durations) < 0.1
    finally:
        server.close()


def test_closing_host_tool_endpoint_fails_pending_call_and_preserves_existing_path(
    tmp_path: Path,
) -> None:
    calls = []
    server = AgentToolServer(
        tmp_path / "tools.sock", "attempt-owned", lambda *args: calls.append(args) or {}
    )
    try:
        with pytest.raises(FileExistsError):
            AgentToolServer(server.socket_path, "another-attempt", lambda *_args: {})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                call_tool, str(server.socket_path), "list_my_experiments", {}
            )
            deadline = time.monotonic() + 2
            while not server._pending and time.monotonic() < deadline:
                time.sleep(0.005)
            assert server._pending
            server.close()
            with pytest.raises(ValueError, match="endpoint closed"):
                future.result(timeout=2)
        assert calls == []
        assert server.service() is False
        with pytest.raises(OSError):
            call_tool(str(server.socket_path), "list_my_experiments", {})
    finally:
        server.close()


def test_host_tool_socket_rejects_spoofing_unknown_tools_and_oversize(
    tmp_path: Path,
) -> None:
    calls = []
    server = AgentToolServer(
        tmp_path / "tools.sock",
        "attempt-owned",
        lambda *args: calls.append(args) or {},
        max_request_bytes=180,
    )
    try:
        for document in (
            {"tool": "list_my_experiments", "arguments": {}, "attempt_id": "forged"},
            {"tool": "operator_cancel_run", "arguments": {}},
            {
                "tool": "submit_experiment",
                "arguments": {"candidate": {}, "padding": "x" * 200},
            },
        ):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(str(server.socket_path))
                connection.sendall(json.dumps(document).encode() + b"\n")
                with connection.makefile("rb") as stream:
                    result = json.loads(stream.readline())
            assert result["ok"] is False
        assert calls == []
        assert server.service() is False
    finally:
        server.close()


def test_stdio_mcp_reports_host_authorization_errors_as_tool_results(
    tmp_path: Path,
) -> None:
    def handler(_attempt: str, _tool: str, _arguments: Any) -> dict[str, Any]:
        raise ValueError("experiment is owned by another chain")

    server = AgentToolServer(tmp_path / "tools.sock", "attempt-owned", handler)
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_experiment_log",
                "arguments": {"exp_id": "other-chain"},
            },
        },
    ]
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            completed = _finish(
                server,
                pool.submit(
                    subprocess.run,
                    [
                        sys.executable,
                        host_tools_server.__file__,
                        "--socket",
                        str(server.socket_path),
                    ],
                    input=b"".join(
                        json.dumps(item).encode() + b"\n" for item in requests
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    check=False,
                ),
            )
        assert completed.returncode == 0, completed.stderr
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        assert [response["id"] for response in responses] == [1, 2, 3]
        assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {
            "submit_experiment",
            "wait_experiment",
            "get_experiment_log",
            "cancel_experiment",
            "list_my_experiments",
            "get_resources",
        }
        denied = responses[2]["result"]
        assert denied["isError"] is True
        assert "another chain" in denied["content"][0]["text"]
    finally:
        server.close()


@pytest.mark.parametrize("scope,permitted,arguments,denied,denied_arguments", (
    ("grounding", "web_search", {"query": "repository benchmark", "max_results": 2},
     "submit_experiment", {"candidate": {}}),
    ("literature", "web_fetch", {"url": "https://example.org/paper"},
     "submit_experiment", {"candidate": {}}),
    ("execution", "list_my_experiments", {}, "web_fetch", {"url": "https://example.org"}),
))
def test_invocation_scope_rejects_cross_scope_socket_calls(
    tmp_path: Path, scope: str, permitted: str, arguments: Any, denied: str, denied_arguments: Any,
) -> None:
    calls = []
    def handler(attempt: str, tool: str, values: Any) -> dict[str, Any]:
        calls.append((attempt, tool, values))
        return {"handled": tool}
    server = AgentToolServer(tmp_path / "tools.sock", "owned-attempt", handler, scope=scope)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert _finish(server, pool.submit(call_tool, str(server.socket_path), permitted, arguments)) == {"handled": permitted}
        with pytest.raises(ValueError, match="invocation scope"):
            call_tool(str(server.socket_path), denied, denied_arguments)
        assert calls == [("owned-attempt", permitted, arguments)]
        assert not server.service()
    finally:
        server.close()


@pytest.mark.parametrize("tool,arguments", (
    ("web_search", {"query": "q", "max_results": 11}),
    ("web_search", {"query": "q", "max_results": True}),
    ("web_search", {"query": " "}),
    ("web_fetch", {"url": "https://example.org", "max_chars": 100001}),
))
def test_web_mcp_rejects_invalid_public_bounds(tmp_path: Path, tool: str, arguments: Any) -> None:
    with pytest.raises(ValueError, match="invalid tool argument"):
        call_tool(str(tmp_path / "not-contacted.sock"), tool, arguments)


def test_literature_profile_tool_subset_is_enforced_on_the_socket(tmp_path):
    calls = []
    server = AgentToolServer(tmp_path / "tools.sock", "literature-attempt",
        lambda *args: calls.append(args) or {"result": "paper"}, scope="literature", tools=("web_fetch",))
    try:
        with pytest.raises(ValueError, match="invocation scope"):
            call_tool(str(server.socket_path), "web_search", {"query": "not authorized"})
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert _finish(server, pool.submit(call_tool, str(server.socket_path), "web_fetch",
                {"url": "https://example.org/paper"})) == {"result": "paper"}
        assert len(calls) == 1 and calls[0][0] == "literature-attempt"
    finally: server.close()


@pytest.mark.parametrize("tools", [("submit_experiment",), ("web_fetch", "web_fetch"), ("invented",)])
def test_literature_tool_subset_cannot_expand_scope(tmp_path, tools):
    with pytest.raises(ValueError, match="scope|duplicate"):
        AgentToolServer(tmp_path / "not-created.sock", "attempt", lambda *args: {}, scope="literature", tools=tools)
    assert not (tmp_path / "not-created.sock").exists()
