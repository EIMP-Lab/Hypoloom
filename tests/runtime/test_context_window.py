"""Window acquisition, actual request limits and credential-free CC launch."""
import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.context_management import context_policy, available_input, request_capacity
from hypoloom.context_window import (WindowResolver, bind_inputs, context_environment,
    metadata_limits, resolve_window, window_policy)
from hypoloom.full_cc_runtime import full_cc_environment
from hypoloom.profile_v4 import request_node_runtime
from test_context_management import request24
from test_provider_relay import upstream as upstream, _relay, _request


MAX = "CLAUDE_CODE_MAX_CONTEXT_TOKENS"
DISABLE = "CLAUDE_CODE_DISABLE_1M_CONTEXT"
COMPACT = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"


def resolved_request(model="glm-5.3", environment=None, memory=None, metadata=None):
    memory = memory or {}
    window = resolve_window(model, {}, environment or {MAX: "1000000"}, memory, metadata)
    config = EmbeddedInput.from_bytes("context--expert-config", "application/json",
        json.dumps({"node_runtime": {"memory": memory, "context_window": window}}).encode())
    request = replace(request24(), schema_version=29, required_model_identity=model,
        input_artifacts=(*request24().input_artifacts, config))
    return request


def test_reader_inherits_capacity_controls_without_parent_scientific_context(tmp_path):
    from types import SimpleNamespace
    from hypoloom.paper_reading import PaperReadingHost
    parent = resolved_request(memory={"working_context_tokens": 120000,
        "context_metadata": False, "max_memory_bytes": 12345})
    host = SimpleNamespace(store=SimpleNamespace(get_attempt=lambda _: {
        "observation_json": json.dumps({"request": parent.to_dict()})}))
    readers = PaperReadingHost(host)
    readers.rows = lambda: [{"command_id": "reader", "request": {
        "paper_reading": {"parent_attempt_id": parent.attempt_id, "question": "Check assumptions"},
        "source": {"source_ref": "paper"}}}]
    inputs = readers.inputs(SimpleNamespace(command_id="reader"))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {MAX: "1000000"}}))
    settings.chmod(0o600)
    adapter = ClaudeAgentHost(claude_executable=sys.executable, settings_path=settings)
    inputs = bind_inputs(adapter, inputs, parent.required_model_identity, {})
    child = replace(parent, input_artifacts=inputs)
    assert request_node_runtime(inputs)["memory"] == {
        "working_context_tokens": 120000, "context_metadata": False}
    assert context_policy(child)["effective_context_tokens"] == 120000
    assert context_policy(child)["cc_context_tokens"] == 1000000
    assert len(inputs) == 3


@pytest.mark.parametrize("model,env,tokens,source", [
    ("glm-5.3-flash", {MAX: "1000000"}, 1000000, "cc_config"),
    ("mimo-v2.6-pro", {MAX: "1000000"}, 1000000, "cc_config"),
    ("deepseek-v4-flash", {MAX: "1000000"}, 1000000, "cc_config"),
    ("k3-256k", {MAX: "262144"}, 262144, "cc_config"),
    ("k3[1m]", {MAX: "262144"}, 1000000, "cc_model_suffix"),
    ("kimi-for-coding[1M]", {MAX: "262144"}, 1000000, "cc_model_suffix"),
    ("k3[1m]", {MAX: "262144", DISABLE: "1"}, 262144, "cc_config"),
    ("claude-sonnet-4-6", {MAX: "1000000"}, 200000, "cc_builtin"),
    ("claude-sonnet-4-6[1m]", {}, 1000000, "cc_model_suffix"),
    ("claude-opus-4-8", {}, 1000000, "cc_builtin"),
    ("claude-haiku-4-5-20251001", {}, 200000, "cc_builtin"),
    ("claude-fable-5-1", {}, 1000000, "cc_builtin"),
    ("new-provider-model", {}, 200000, "cc_default"),
    ("claude-unknown", {MAX: "1000000"}, 200000, "cc_default"),
    ("claude-sonnet-4-6", {MAX: "1000000", "DISABLE_COMPACT": "1"}, 1000000, "cc_config"),
])
def test_cc_rules_are_per_exact_model(model, env, tokens, source):
    window = resolve_window(model, {}, env, {"context_metadata": False})
    assert (window["cc_context_tokens"], window["cc_window_source"]) == (tokens, source)


def test_default_follows_cc_window_and_retained_requests_keep_their_original_policy():
    request = resolved_request()
    policy = context_policy(request)
    assert policy["effective_context_tokens"] == 1000000
    assert policy["working_context_tokens"] is None
    assert policy["working_context_mode"] == "automatic"
    assert available_input(policy, 32000) == 897308
    assert context_policy(request24())["effective_context_tokens"] == 262144
    assert full_cc_environment(request=request)[MAX] == "1000000"
    assert MAX not in full_cc_environment(request=request24())


@pytest.mark.parametrize("memory,expected", [
    ({"working_context_tokens": 262144}, 262144),
    ({"context_window_tokens": 128000}, 128000),
    ({"runtime_context_tokens": 64000}, 64000),
    ({"working_context_tokens": 2000000}, 1000000),
])
def test_explicit_limits_constrain_instead_of_enlarging_model_window(memory, expected):
    policy = context_policy(resolved_request(memory=memory))
    assert policy["effective_context_tokens"] == expected
    assert request_capacity({"messages": [{"role": "user", "content": "x" * expected}], "max_tokens": 100}, policy)["fits"] is False


def test_metadata_total_can_configure_custom_cc_but_input_is_a_separate_limit():
    metadata = lambda _: {"provider_context_tokens": 1000000, "provider_input_tokens": 700000, "metadata_status": "available"}
    window = resolve_window("custom", {}, {}, {}, metadata)
    assert window["cc_environment"][MAX] == "1000000"
    assert window["cc_window_source"] == "provider_metadata"
    request = resolved_request("custom", {"unused": "ignored"}, metadata=metadata)
    policy = context_policy(request)
    assert available_input(policy, 32000) == 629308
    assert policy["effective_context_tokens"] == 1000000


def test_known_claude_does_not_adopt_a_custom_override_and_smaller_provider_wins():
    window = resolve_window("claude-opus-4-8", {}, {}, {},
        lambda _: {"provider_context_tokens": 128000, "metadata_status": "available"})
    assert MAX not in window["cc_environment"]
    assert window_policy({}, window)["effective_context_tokens"] == 128000
    assert window["window_warnings"] == ["provider_cc_window_difference"]


def test_compact_precedence_and_model_cap_are_separate_from_window():
    settings = {"autoCompactWindow": 500000, "modelSettings": {"k3-256k": {"autoCompactWindow": 200000}}}
    window = resolve_window("k3-256k", settings, {MAX: "262144"}, {})
    assert window["auto_compact_tokens"] == 200000
    window = resolve_window("k3-256k", settings, {MAX: "262144", COMPACT: "1000000"}, {})
    assert window["auto_compact_tokens"] == 262144
    assert window["cc_environment"][COMPACT] == "1000000"
    assert window_policy({"working_context_tokens": 100000}, window)["effective_context_tokens"] == 100000


@pytest.mark.parametrize("name,value", [(MAX, "1M"), (MAX, "-1"), (MAX, True), (MAX, "0"),
    (MAX, "1\n000"), (COMPACT, "1000"), (DISABLE, "perhaps")])
def test_invalid_context_settings_fail_locally(name, value):
    with pytest.raises(ValueError): context_environment({name: value})


@pytest.mark.parametrize("document,expected", [
    ({"data": [{"id": "custom", "context_length": 1000000, "max_tokens": 32000}]}, {"provider_context_tokens": 1000000, "provider_input_tokens": None}),
    ({"id": "custom", "max_input_tokens": 800000}, {"provider_context_tokens": None, "provider_input_tokens": 800000}),
    ({"id": "different", "context_length": 1000000}, {}),
    ({"id": "custom", "max_tokens": 1000000}, {}),
    ({"id": "custom", "context_length": "1000000"}, {}),
    ({"id": "custom", "context_length": True}, {}),
    ({"id": "custom", "context_length": -100}, {}),
    ({"data": [{"id": "custom", "context_length": 100}, {"id": "custom", "context_length": 200}]}, {}),
])
def test_metadata_does_not_guess_ids_or_token_units(document, expected):
    assert metadata_limits(document, "custom[1M]") == expected


@pytest.fixture
def metadata_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            self.server.calls.append((self.path, self.headers.get("x-api-key")))
            if self.server.delay: time.sleep(self.server.delay)
            body = json.dumps(self.server.document).encode()
            self.send_response(self.server.status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Location", "http://127.0.0.1:1/must-not-follow")
            self.end_headers()
            try: self.wfile.write(body)
            except OSError: pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.calls, server.status, server.delay = [], 200, 0
    server.document = {"data": [{"id": "custom", "context_length": 1000000}]}
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    yield server
    server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_metadata_exact_connection_cache_and_config_skip(metadata_server):
    resolver = WindowResolver()
    env = {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{metadata_server.server_port}/anthropic/v1", "ANTHROPIC_API_KEY": "private-test-key"}
    first = resolver.resolve("custom", {}, env, {})
    assert first["cc_context_tokens"] == 1000000
    assert "private-test-key" not in json.dumps(first)
    assert resolver.resolve("custom", {}, env, {}) == first
    resolver.resolve("custom", {}, {**env, "ANTHROPIC_API_KEY": "new-key"}, {})
    assert metadata_server.calls == [("/anthropic/v1/models", "private-test-key"), ("/anthropic/v1/models", "new-key")]
    resolver.resolve("custom", {}, {**env, MAX: "1000000"}, {})
    assert len(metadata_server.calls) == 2


@pytest.mark.parametrize("status", [302, 401, 404, 429, 500])
def test_metadata_failure_does_not_block_or_retry(metadata_server, status):
    metadata_server.status = status
    resolver = WindowResolver()
    env = {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{metadata_server.server_port}", "ANTHROPIC_API_KEY": "local-only"}
    result = resolver.resolve("custom", {}, env, {})
    assert result["metadata_status"] == "unavailable"
    assert result["cc_context_tokens"] == 200000
    assert resolver.resolve("custom", {}, env, {}) == result
    assert len(metadata_server.calls) == 1


def test_metadata_hard_deadline_and_total_budget(metadata_server):
    metadata_server.delay = 3
    resolver = WindowResolver()
    resolver.remaining_seconds = .4
    started = time.monotonic()
    result = resolver.metadata({"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{metadata_server.server_port}", "ANTHROPIC_API_KEY": "local-only"}, "custom")
    assert time.monotonic() - started < 1.5
    assert result["metadata_status"] == "unavailable"
    assert resolver.metadata({}, "another")["metadata_status"] == "budget_exhausted"


def test_binding_preserves_secrets_and_request_stays_independent_of_later_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {MAX: "1000000", "ANTHROPIC_AUTH_TOKEN": "host-secret", "UNRELATED": "unrelated-private"}}))
    settings.chmod(0o600)
    adapter = ClaudeAgentHost(claude_executable=sys.executable, settings_path=settings)
    request = resolved_request()
    inputs = bind_inputs(adapter, request.input_artifacts, "glm-5.3", {})
    request = replace(request, input_artifacts=inputs)
    settings.write_text(json.dumps({"env": {MAX: "262144"}}))
    assert context_policy(request)["effective_context_tokens"] == 1000000
    environment = full_cc_environment(request=request)
    assert environment[MAX] == "1000000"
    assert not {"ANTHROPIC_AUTH_TOKEN", "UNRELATED"}.intersection(environment)
    assert "host-secret" not in json.dumps(request.to_dict())
    fresh = bind_inputs(adapter, inputs, "glm-5.3", {})
    assert request_node_runtime(fresh)["context_window"]["cc_context_tokens"] == 262144
    with pytest.raises(ValueError, match="match the request model"):
        full_cc_environment(request=replace(request, required_model_identity="other"))


def test_metadata_budget_also_covers_dns_or_child_start_failure(monkeypatch):
    def hung(*args, **kwargs):
        assert kwargs["timeout"] <= 2
        assert "host-secret" not in repr(args)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", hung)
    assert WindowResolver().metadata({"ANTHROPIC_API_KEY": "host-secret"}, "custom")["metadata_status"] == "unavailable"


@pytest.mark.parametrize("disabled", [False, True])
def test_relay_uppercase_suffix_and_explicit_disable_preserve_wire_identity(tmp_path, upstream, disabled):
    environment = {MAX: "262144", **({DISABLE: "1"} if disabled else {})}
    policy = context_policy(resolved_request("k3[1M]", environment))
    relay = _relay(tmp_path, upstream, model="k3[1M]", context_policy=policy)
    body = json.dumps({"model": "k3", "messages": [{"role": "user", "content": "fixture"}], "max_tokens": 100}).encode()
    try:
        assert _request(relay, body=body)[0] == (200 if disabled else 400)
        assert _request(relay, body=body, headers={"anthropic-beta": "context-1m-2025-08-07"})[0] == 200
        assert all(json.loads(call[2])["model"] == "k3" for call in upstream.calls)
    finally:
        relay.close()


def test_cc_report_is_exact_model_observation_not_provider_capacity(tmp_path):
    from hypoloom.context_observation import read_native_events
    path = tmp_path / "stdout.log"
    path.write_text(json.dumps({"type": "result", "modelUsage": {
        "different": {"contextWindow": 1000000}, "k3": {"contextWindow": 262144}}}) + '\n')
    assert read_native_events(path, model="k3[1m]")["reported_cc_context_tokens"] == 262144
    assert "reported_cc_context_tokens" not in read_native_events(path, model="absent")
    assert "reported_cc_context_tokens" not in read_native_events(path)


def test_metadata_queries_have_a_count_limit_even_when_failures_are_fast(monkeypatch):
    calls = []
    def missing(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "{}")
    monkeypatch.setattr(subprocess, "run", missing)
    resolver = WindowResolver()
    for index in range(20): resolver.metadata({}, f"custom-{index}")
    assert len(calls) == 3
