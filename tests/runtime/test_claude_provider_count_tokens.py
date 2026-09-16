"""Large native Read token counting is separate from scripted model turns."""

import json
import threading
import time
import uuid
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from hypoloom.provider_relay import ProviderRelay
from test_claude_file_tool_cli import _handler_for, _installed_claude, _result_text, _tool_results


def test_scripted_provider_counts_large_native_read_without_requiring_agent_prompt():
    class Scenario:
        errors = []
        def record(self, document):
            raise AssertionError("count_tokens must not consume a model-script step")

    scenario = Scenario()
    server = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    document = {"model": "local-model", "messages": [{"role": "user", "content": "Actual large file text.\n" * 2000}], "tools": []}
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages/count_tokens?beta=true",
            data=json.dumps(document).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            counted = json.load(response)
        assert counted == {"input_tokens": (len(json.dumps(document["messages"])) + 3) // 4}
        assert scenario.count_token_requests == [document]
        assert not scenario.errors
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_native_long_context_read_counts_tokens_without_generation_beta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import test_claude_session_cli as native

    class Scenario:
        def __init__(self):
            self.requests = []
            self.errors = []

        def record(self, document):
            self.requests.append(document)
            if len(self.requests) == 2:
                result = _tool_results(document)["large-read"]
                assert "400" not in _result_text(result), result
                assert "count-only text" in _result_text(result) or "maximum allowed tokens" in _result_text(result), result
            return len(self.requests)

        def tool_calls(self, step):
            if step == 1:
                return (("large-read", "Read", {"file_path": "/workspace/large.txt"}),)
            return (("finish", "StructuredOutput", {"stage": "count", "ok": True}),)

    scenario = Scenario()
    headers = []
    base_handler = _handler_for(scenario)

    class Handler(base_handler):
        def do_POST(self):  # noqa: N802 - stdlib API
            headers.append((self.path.split("?", 1)[0], dict(self.headers)))
            super().do_POST()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model = "claude-sonnet-4-6[1m]"
    monkeypatch.setattr(native, "_MODEL", model)
    relay = ProviderRelay(tmp_path / "provider.sock", base_url=f"http://127.0.0.1:{server.server_port}",
        provider_environment={"ANTHROPIC_API_KEY": "local-count-only-dummy-key"}, model=model,
        max_request_bytes=4 * 1024 * 1024, max_response_bytes=4 * 1024 * 1024,
        max_requests=8, deadline_at=time.monotonic() + 45)
    workspace, sessions = tmp_path / "workspace", tmp_path / "sessions"
    workspace.mkdir(); sessions.mkdir()
    (workspace / "large.txt").write_text(("count-only text " * 60 + "\n") * 200)
    try:
        native._invoke(tmp_path, executable=_installed_claude(), socket_path=relay.socket_path,
            sessions=sessions, workspace=workspace, stage="count",
            session_arguments=("--session-id", str(uuid.uuid4())), prompt="Read the large file once, then finish.")
        assert len(scenario.requests) == 2
        assert len(scenario.count_token_requests) == 1
        assert scenario.count_token_requests[0]["model"] == "claude-sonnet-4-6"
        counted_headers = [value for path, value in headers if path == "/v1/messages/count_tokens"]
        assert len(counted_headers) == 1
        assert "context-1m-2025-08-07" not in counted_headers[0].get("anthropic-beta", "")
        assert all("context-1m-2025-08-07" in value.get("anthropic-beta", "")
            for path, value in headers if path == "/v1/messages")
    finally:
        relay.close(); server.shutdown(); server.server_close(); thread.join(timeout=5)
